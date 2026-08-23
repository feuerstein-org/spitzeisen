"""
Parse typed OpenAPI objects into Spitzeisen generator data.

The control flow deliberately follows ``openapi_python_client.parser.openapi``: hydrate the
vendored Pydantic model, build component registries, create endpoints from operations, add
Path Item parameters afterwards, sort path parameters, and collect parser warnings.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, NewType, cast
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from spitzeisen.codegen import schema as oai
from spitzeisen.codegen.ir import (
    ArrayType,
    HTTPMethod,
    IntersectionType,
    JSONScalar,
    LiteralType,
    MediaTypeIR,
    ObjectType,
    ParameterIR,
    ParameterLocation,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    RequestBodyIR,
    ResponseIR,
    SchemaId,
    StatusPattern,
    TypeIR,
    UnionType,
    UnknownType,
)
from spitzeisen.codegen.parser.errors import GeneratorError, ParameterError, ParseError, PropertyError

_PATH_PARAM_REGEX = re.compile(r"{([a-zA-Z_-][a-zA-Z0-9_-]*)}")
_METHODS = tuple(HTTPMethod)

ReferencePath = NewType("ReferencePath", str)


def parse_reference_path(ref_path_raw: str) -> ReferencePath | ParseError:
    """Validate the local-reference form supported by openapi-python-client."""
    parsed = urlparse(ref_path_raw)
    if parsed.scheme or parsed.path:
        return ParseError(detail=f"Remote references such as {ref_path_raw} are not supported yet.")
    return cast("ReferencePath", parsed.fragment)


def get_reference_simple_name(ref_path: str) -> str:
    """Return the final name from a component reference path."""
    return unquote(ref_path.rsplit("/", maxsplit=1)[-1])


@dataclass
class EndpointCollection:
    """Endpoints grouped under the first operation tag."""

    tag: str
    endpoints: list[Endpoint] = field(default_factory=list["Endpoint"])
    parse_errors: list[ParseError] = field(default_factory=list[ParseError])

    @staticmethod
    def from_data(
        *,
        data: dict[str, oai.PathItem],
        parameter_components: dict[ReferencePath, oai.Parameter],
        request_bodies: dict[str, oai.RequestBody | oai.Reference],
        responses: dict[str, oai.Response | oai.Reference],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> dict[str, EndpointCollection]:
        """Parse OpenAPI paths into endpoint collections by tag."""
        endpoints_by_tag: dict[str, EndpointCollection] = {}
        for path, path_data in data.items():
            for method in _METHODS:
                operation = getattr(path_data, method.value)
                if operation is None:
                    continue
                tags = (operation.tags or ["default"])[:1]
                collections = [endpoints_by_tag.setdefault(tag, EndpointCollection(tag=tag)) for tag in tags]
                endpoint = Endpoint.from_data(
                    data=operation,
                    path=path,
                    method=method,
                    parameter_components=parameter_components,
                    request_bodies=request_bodies,
                    responses=responses,
                    schema_components=schema_components,
                )
                if not isinstance(endpoint, ParseError):
                    endpoint = endpoint.add_parameters(
                        data=path_data,
                        parameter_components=parameter_components,
                        schema_components=schema_components,
                    )
                if not isinstance(endpoint, ParseError):
                    endpoint = endpoint.sort_parameters()
                if isinstance(endpoint, ParseError):
                    endpoint.header = (
                        f"WARNING parsing {method.value.upper()} {path} within {'/'.join(tags)}. "
                        "Endpoint will not be generated."
                    )
                    for collection in collections:
                        collection.parse_errors.append(endpoint)
                    continue
                for parse_error in endpoint.errors:
                    parse_error.header = f"WARNING parsing {method.value.upper()} {path} within {'/'.join(tags)}."
                    for collection in collections:
                        collection.parse_errors.append(parse_error)
                for collection in collections:
                    collection.endpoints.append(endpoint)
        return endpoints_by_tag


def generate_operation_id(*, path: str, method: str) -> str:
    """Generate an operation ID using openapi-python-client's spelling."""
    clean_path = path.replace("{", "").replace("}", "").replace("/", "_")
    clean_path = clean_path.removeprefix("_").removesuffix("_")
    return f"{method}_{clean_path}"


@dataclass
class Endpoint:
    """One parsed endpoint, shaped like openapi-python-client's parser object."""

    path: str
    method: HTTPMethod
    description: str
    name: str
    requires_security: bool
    tags: tuple[str, ...]
    summary: str = ""
    query_parameters: list[ParameterIR] = field(default_factory=list[ParameterIR])
    path_parameters: list[ParameterIR] = field(default_factory=list[ParameterIR])
    header_parameters: list[ParameterIR] = field(default_factory=list[ParameterIR])
    cookie_parameters: list[ParameterIR] = field(default_factory=list[ParameterIR])
    responses: list[ResponseIR] = field(default_factory=list[ResponseIR])
    request_body: RequestBodyIR | None = None
    errors: list[ParseError] = field(default_factory=list[ParseError])
    deprecated: bool = False
    security: tuple[dict[str, tuple[str, ...]], ...] = ()

    @property
    def parameters(self) -> tuple[ParameterIR, ...]:
        """Return parameters in the same location order as upstream."""
        return tuple(
            self.path_parameters + self.query_parameters + self.header_parameters + self.cookie_parameters,
        )

    def add_parameters(
        self,
        *,
        data: oai.Operation | oai.PathItem,
        parameter_components: dict[ReferencePath, oai.Parameter],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> Endpoint | ParseError:
        """Add parameters, allowing earlier operation-level definitions to take precedence."""
        if data.parameters is None:
            return self
        endpoint = deepcopy(self)
        unique_parameters: set[tuple[str, oai.ParameterLocation]] = set()
        parameters_by_location = {
            oai.ParameterLocation.QUERY: endpoint.query_parameters,
            oai.ParameterLocation.PATH: endpoint.path_parameters,
            oai.ParameterLocation.HEADER: endpoint.header_parameters,
            oai.ParameterLocation.COOKIE: endpoint.cookie_parameters,
        }
        for parameter_data in data.parameters:
            parameter = _parameter_from_reference(parameter_data, parameter_components)
            if isinstance(parameter, ParseError):
                return parameter
            if parameter.param_schema is None:
                continue
            identity = (parameter.name, parameter.param_in)
            if identity in unique_parameters:
                return ParseError(
                    data=data,
                    detail=(
                        "Parameters MUST NOT contain duplicates. A unique parameter is defined by a combination "
                        f"of a name and location. Duplicated parameters named `{parameter.name}` detected in "
                        f"`{parameter.param_in}`."
                    ),
                )
            unique_parameters.add(identity)
            existing = parameters_by_location[parameter.param_in]
            if any(item.wire_name == parameter.name for item in existing):
                continue
            lowered = _lower_parameter(parameter, schema_components)
            if isinstance(lowered, ParseError):
                return ParseError(
                    detail=f"cannot parse parameter of endpoint {endpoint.name}: {lowered.detail}",
                    data=lowered.data,
                )
            existing.append(lowered)
        return endpoint

    def sort_parameters(self) -> Endpoint | ParseError:
        """Sort path parameters into path-template order and validate the template."""
        endpoint = deepcopy(self)
        parameters_from_path = re.findall(_PATH_PARAM_REGEX, endpoint.path)
        try:
            endpoint.path_parameters.sort(key=lambda parameter: parameters_from_path.index(parameter.wire_name))
        except ValueError:
            pass
        if parameters_from_path != [parameter.wire_name for parameter in endpoint.path_parameters]:
            return ParseError(
                detail=f"Incorrect path templating for {endpoint.path} (Path parameters do not match with path)",
            )
        return endpoint

    @staticmethod
    def from_data(
        *,
        data: oai.Operation,
        path: str,
        method: HTTPMethod,
        parameter_components: dict[ReferencePath, oai.Parameter],
        request_bodies: dict[str, oai.RequestBody | oai.Reference],
        responses: dict[str, oai.Response | oai.Reference],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> Endpoint | ParseError:
        """Construct an endpoint from one typed OpenAPI operation."""
        endpoint = Endpoint(
            path=path,
            method=method,
            summary=data.summary or "",
            description=data.description or "",
            name=data.operationId or generate_operation_id(path=path, method=method.value),
            requires_security=bool(data.security),
            tags=tuple((data.tags or ["default"])[:1]),
            deprecated=data.deprecated,
            security=tuple({name: tuple(scopes) for name, scopes in item.items()} for item in (data.security or [])),
        )
        result = endpoint.add_parameters(
            data=data,
            parameter_components=parameter_components,
            schema_components=schema_components,
        )
        if isinstance(result, ParseError):
            return result
        for code, response_data in data.responses.items():
            status = _status_pattern(code)
            if isinstance(status, ParseError):
                result.errors.append(status)
                continue
            response = _lower_response(status, response_data, responses, schema_components)
            if isinstance(response, ParseError):
                suffix = "" if response.detail is None else f" ({response.detail})"
                result.errors.append(
                    ParseError(
                        detail=(
                            f"Cannot parse response for status code {code}{suffix}, "
                            "response will be omitted from generated client"
                        ),
                        data=response.data,
                    ),
                )
                continue
            result.responses.append(response)
        result.responses.sort(key=_response_sort_key)
        body = _lower_request_body(data.request_body, request_bodies, schema_components)
        if isinstance(body, ParseError):
            return body
        result.request_body, body_errors = body
        result.errors.extend(body_errors)
        return result


@dataclass
class GeneratorData:
    """All parsed data needed by Spitzeisen's generation policy."""

    title: str
    description: str | None
    version: str
    errors: list[ParseError]
    endpoint_collections_by_tag: dict[str, EndpointCollection]

    @property
    def endpoints(self) -> tuple[Endpoint, ...]:
        """Return the flat endpoint sequence used by manifest selection."""
        return tuple(
            endpoint for collection in self.endpoint_collections_by_tag.values() for endpoint in collection.endpoints
        )

    def operation_at(self, path: str, method: HTTPMethod) -> Endpoint | None:
        """Find an endpoint by wire path and method."""
        return next((item for item in self.endpoints if item.path == path and item.method is method), None)

    def operation_named(self, operation_id: str) -> Endpoint | None:
        """Find an endpoint by explicit or generated operation ID."""
        return next((item for item in self.endpoints if item.name == operation_id), None)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> GeneratorData | GeneratorError:
        """Hydrate the vendored model and parse it using the upstream workflow."""
        try:
            openapi = oai.OpenAPI.model_validate(data)
        except ValidationError as err:
            detail = str(err)
            if "swagger" in data:
                detail = (
                    "You may be trying to use a Swagger document; this is not supported by this project.\n\n" + detail
                )
            return GeneratorError(header="Failed to parse OpenAPI document", detail=detail)

        components = openapi.components
        schema_components = (components and components.schemas) or {}
        parameter_components, errors = _build_parameters((components and components.parameters) or {})
        request_bodies = (components and components.requestBodies) or {}
        responses = (components and components.responses) or {}
        endpoint_collections = EndpointCollection.from_data(
            data=openapi.paths,
            parameter_components=parameter_components,
            request_bodies=request_bodies,
            responses=responses,
            schema_components=schema_components,
        )
        return GeneratorData(
            title=openapi.info.title,
            description=openapi.info.description,
            version=openapi.info.version,
            endpoint_collections_by_tag=endpoint_collections,
            errors=errors,
        )


def _build_parameters(
    components: dict[str, oai.Reference | oai.Parameter],
) -> tuple[dict[ReferencePath, oai.Parameter], list[ParseError]]:
    """Build the reusable parameter registry using upstream's accepted subset."""
    parameters: dict[ReferencePath, oai.Parameter] = {}
    errors: list[ParseError] = []
    for name, data in components.items():
        if isinstance(data, oai.Reference):
            errors.append(ParameterError(data=data, detail="Reference parameters are not supported."))
            continue
        path = parse_reference_path(f"#/components/parameters/{name}")
        if isinstance(path, ParseError):
            errors.append(ParameterError(detail=path.detail, data=data))
            continue
        parameters[path] = data
    return parameters, errors


def _parameter_from_reference(
    parameter: oai.Reference | oai.Parameter,
    parameters: dict[ReferencePath, oai.Parameter],
) -> oai.Parameter | ParameterError:
    """Resolve one parameter reference from the component registry."""
    if isinstance(parameter, oai.Parameter):
        return parameter
    path = parse_reference_path(parameter.ref)
    if isinstance(path, ParseError):
        return ParameterError(detail=path.detail)
    resolved = parameters.get(path)
    if resolved is None:
        return ParameterError(detail=f"Reference `{path}` not found.")
    return resolved


def _lower_parameter(
    parameter: oai.Parameter,
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> ParameterIR | PropertyError:
    """Lower the schema-bearing part of a typed OpenAPI parameter."""
    assert parameter.param_schema is not None  # noqa: S101 - caller follows upstream's schema-less skip
    schema = lower_schema_type(parameter.param_schema, schema_components)
    if isinstance(schema, PropertyError):
        return schema
    content = _lower_content(parameter.content or {}, schema_components)
    if isinstance(content, PropertyError):
        return content
    param_schema = parameter.param_schema
    has_default = isinstance(param_schema, oai.Schema) and "default" in param_schema.model_fields_set
    default = cast("Any", param_schema.default) if isinstance(param_schema, oai.Schema) else None
    return ParameterIR(
        wire_name=parameter.name,
        location=ParameterLocation(parameter.param_in.value),
        required=parameter.required,
        schema=schema,
        content=content,
        style=parameter.style,
        explode=parameter.explode,
        allow_reserved=parameter.allowReserved,
        description=parameter.description or "",
        default=default,
        has_default=has_default,
        deprecated=parameter.deprecated,
    )


def _lower_content(
    content: dict[str, oai.MediaType],
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> tuple[MediaTypeIR, ...] | PropertyError:
    """Lower every media type in insertion order."""
    result: list[MediaTypeIR] = []
    for content_type, media in content.items():
        schema: TypeIR | None = None
        if media.media_type_schema is not None:
            lowered = lower_schema_type(media.media_type_schema, schema_components)
            if isinstance(lowered, PropertyError):
                return lowered
            schema = lowered
        result.append(MediaTypeIR(media_type=content_type, schema=schema))
    return tuple(result)


def _lower_response(
    status: StatusPattern,
    data: oai.Response | oai.Reference,
    responses: dict[str, oai.Response | oai.Reference],
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> ResponseIR | ParseError:
    """Resolve and lower one response using upstream's component restrictions."""
    if isinstance(data, oai.Reference):
        path = parse_reference_path(data.ref)
        if isinstance(path, ParseError):
            return path
        if not path.startswith("/components/responses/"):
            return ParseError(data=data, detail=f"$ref to {data.ref} not allowed in responses")
        resolved = responses.get(get_reference_simple_name(path))
        if resolved is None:
            return ParseError(data=data, detail=f"Could not find reference: {data.ref}")
        if not isinstance(resolved, oai.Response):
            return ParseError(data=data, detail="Top-level $ref inside components/responses is not supported")
        data = resolved
    content = _lower_content(data.content or {}, schema_components)
    if isinstance(content, ParseError):
        content.data = data
        return content
    return ResponseIR(status=status, description=data.description, content=content)


def _lower_request_body(
    body: oai.RequestBody | oai.Reference | None,
    request_bodies: dict[str, oai.RequestBody | oai.Reference],
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> tuple[RequestBodyIR | None, list[ParseError]] | ParseError:
    """Resolve a request body and retain the media types upstream can parse."""
    resolved = _resolve_request_body(body, request_bodies)
    if isinstance(resolved, ParseError):
        return resolved
    if resolved is None:
        return None, []
    content: list[MediaTypeIR] = []
    errors: list[ParseError] = []
    for content_type, media in resolved.content.items():
        simplified = content_type.split(";", maxsplit=1)[0].strip().lower()
        supported = simplified in {
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "application/octet-stream",
            "application/json",
        } or simplified.endswith("+json")
        if not supported:
            errors.append(ParseError(detail=f"Unsupported content type {simplified}", data=resolved))
            continue
        if media.media_type_schema is None:
            errors.append(ParseError(detail="Missing schema", data=resolved))
            continue
        schema = lower_schema_type(media.media_type_schema, schema_components)
        if isinstance(schema, ParseError):
            schema.data = resolved
            errors.append(schema)
            continue
        content.append(MediaTypeIR(media_type=content_type, schema=schema))
    if not content and errors:
        return ParseError(
            header="Endpoint requires a body, but none were parseable.",
            detail="\n".join(error.detail or "" for error in errors),
            data=resolved,
        )
    return (
        RequestBodyIR(required=resolved.required, description=resolved.description or "", content=tuple(content)),
        errors,
    )


def _resolve_request_body(
    body: oai.RequestBody | oai.Reference | None,
    request_bodies: dict[str, oai.RequestBody | oai.Reference],
) -> oai.RequestBody | ParseError | None:
    """Follow request-body component references with upstream's circularity check."""
    references_seen: list[str] = []
    while isinstance(body, oai.Reference) and body.ref not in references_seen:
        references_seen.append(body.ref)
        body = request_bodies.get(get_reference_simple_name(body.ref))
    if isinstance(body, oai.Reference):
        return ParseError(detail="Circular $ref in request body", data=body)
    if body is None and references_seen:
        return ParseError(detail=f"Could not resolve $ref {references_seen[-1]} in request body")
    return body


def lower_schema_type(  # noqa: PLR0911, PLR0912
    data: oai.Schema | oai.Reference,
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> TypeIR | PropertyError:
    """Lower the type-relevant portion of a typed Schema Object."""
    if isinstance(data, oai.Reference):
        path = parse_reference_path(data.ref)
        if isinstance(path, ParseError):
            return PropertyError(detail=path.detail, data=data)
        if not path.startswith("/components/schemas/"):
            return PropertyError(detail=f"$ref to {data.ref} not allowed in schemas", data=data)
        name = get_reference_simple_name(path)
        if name not in schema_components:
            return PropertyError(detail=f"Reference `{path}` not found.", data=data)
        return ReferenceType(schema_id=SchemaId(reference=path), suggested_name=name)

    if data.enum is not None:
        return LiteralType(tuple(cast("JSONScalar", item) for item in data.enum if _is_json_scalar(item)))
    if data.const is not None:
        return LiteralType((cast("JSONScalar", data.const),))

    for members, composite_type in (
        (data.oneOf, UnionType),
        (data.anyOf, UnionType),
        (data.allOf, IntersectionType),
    ):
        if members:
            lowered_members: list[TypeIR] = []
            for member in members:
                lowered = lower_schema_type(member, schema_components)
                if isinstance(lowered, PropertyError):
                    return lowered
                lowered_members.append(lowered)
            return composite_type(tuple(lowered_members))

    if isinstance(data.type, list):
        return _union(tuple(_primitive(item.value, data) for item in data.type if item.value in PrimitiveKind))
    if data.type is oai.DataType.ARRAY:
        if data.items is None:
            return ArrayType(UnknownType("the array schema declares no items"))
        items = lower_schema_type(data.items, schema_components)
        return items if isinstance(items, PropertyError) else ArrayType(items)
    if data.type is oai.DataType.OBJECT or (data.type is None and data.properties is not None):
        additional: TypeIR | None = None
        if isinstance(data.additionalProperties, (oai.Schema, oai.Reference)):
            lowered = lower_schema_type(data.additionalProperties, schema_components)
            if isinstance(lowered, PropertyError):
                return lowered
            additional = lowered
        return ObjectType(additional_properties=additional)
    if isinstance(data.type, oai.DataType) and data.type.value in PrimitiveKind:
        return _primitive(data.type.value, data)
    return UnknownType("the schema declares no supported type keyword")


def _primitive(type_value: str, schema: oai.Schema) -> PrimitiveType:
    """Build a primitive type from a typed Schema Object."""
    return PrimitiveType(
        kind=PrimitiveKind(type_value),
        format=schema.schema_format,
        minimum=schema.minimum,
        maximum=schema.maximum,
    )


def _union(options: tuple[TypeIR, ...]) -> TypeIR:
    """Flatten and deduplicate a union while preserving declaration order."""
    flattened: list[TypeIR] = []
    for option in options:
        candidates = option.options if isinstance(option, UnionType) else (option,)
        for candidate in candidates:
            if candidate not in flattened:
                flattened.append(candidate)
    if not flattened:
        return UnknownType("the schema declares no supported union members")
    return flattened[0] if len(flattened) == 1 else UnionType(tuple(flattened))


def _status_pattern(pattern: str) -> StatusPattern | ParseError:
    """Parse status patterns with the same precedence rules as upstream."""
    if pattern == "default":
        return StatusPattern(raw=pattern, minimum=None, maximum=None)
    if pattern.endswith("XX") and pattern[0].isdigit():
        first_digit = int(pattern[0])
        return StatusPattern(raw=pattern, minimum=first_digit * 100, maximum=first_digit * 100 + 99)
    try:
        code = int(pattern)
    except ValueError:
        return ParseError(
            detail=f"Invalid response status code pattern: {pattern}, response will be omitted from generated client",
        )
    return StatusPattern(raw=pattern, minimum=code, maximum=code)


def _response_sort_key(response: ResponseIR) -> tuple[int, int]:
    """Order exact statuses, ranges, then the default response."""
    status = response.status
    if status.minimum is None:
        return 2, 0
    return (1 if status.minimum != status.maximum else 0), status.minimum


def _is_json_scalar(value: object) -> bool:
    """Whether a value can be represented by a LiteralType."""
    return isinstance(value, (str, int, float, bool, type(None)))
