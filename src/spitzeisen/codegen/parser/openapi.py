"""
Parse typed OpenAPI objects into Spitzeisen generator data.

The control flow deliberately follows ``openapi_python_client.parser.openapi``: hydrate the
vendored Pydantic model, build component registries, create parsed operations, add
Path Item params afterwards, sort path params, and collect parser warnings.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, NewType, cast
from urllib.parse import unquote, urlparse

from spitzeisen.codegen import schema as oai
from spitzeisen.codegen.ir import (
    ArrayType,
    HTTPMethod,
    IntersectionType,
    JSONScalar,
    LiteralType,
    MediaTypeIR,
    ObjectType,
    ParamIR,
    ParamLocationIR,
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
from spitzeisen.codegen.parser.errors import ParamError, ParseError, PropertyError

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
class OperationCollection:
    """Parsed operations grouped under the first operation tag."""

    tag: str
    operations: list[ParsedOperation] = field(default_factory=list["ParsedOperation"])
    parse_errors: list[ParseError] = field(default_factory=list[ParseError])

    @staticmethod
    def from_data(
        *,
        data: dict[str, oai.PathItem],
        param_components: dict[ReferencePath, oai.Param],
        request_bodies: dict[str, oai.RequestBody | oai.Reference],
        responses: dict[str, oai.Response | oai.Reference],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> dict[str, OperationCollection]:
        """Parse OpenAPI paths into operation collections by tag."""
        operations_by_tag: dict[str, OperationCollection] = {}
        for path, path_data in data.items():
            for method in _METHODS:
                operation = getattr(path_data, method.value)
                if operation is None:
                    continue
                tags = (operation.tags or ["default"])[:1]
                collections = [operations_by_tag.setdefault(tag, OperationCollection(tag=tag)) for tag in tags]
                operation = ParsedOperation.from_data(
                    data=operation,
                    path=path,
                    method=method,
                    param_components=param_components,
                    request_bodies=request_bodies,
                    responses=responses,
                    schema_components=schema_components,
                )
                if not isinstance(operation, ParseError):
                    operation = operation.add_params(
                        data=path_data,
                        param_components=param_components,
                        schema_components=schema_components,
                    )
                if not isinstance(operation, ParseError):
                    operation = operation.sort_params()
                if isinstance(operation, ParseError):
                    operation.header = (
                        f"WARNING parsing {method.value.upper()} {path} within {'/'.join(tags)}. "
                        "Operation will not be generated."
                    )
                    for collection in collections:
                        collection.parse_errors.append(operation)
                    continue
                for parse_error in operation.errors:
                    parse_error.header = f"WARNING parsing {method.value.upper()} {path} within {'/'.join(tags)}."
                    for collection in collections:
                        collection.parse_errors.append(parse_error)
                for collection in collections:
                    collection.operations.append(operation)
        return operations_by_tag


def generate_operation_id(*, path: str, method: str) -> str:
    """Generate an operation ID using openapi-python-client's spelling."""
    clean_path = path.replace("{", "").replace("}", "").replace("/", "_")
    clean_path = clean_path.removeprefix("_").removesuffix("_")
    return f"{method}_{clean_path}"


@dataclass
class ParsedOperation:
    """One normalized OpenAPI operation carrying protocol IR for manifest compilation."""

    path: str
    method: HTTPMethod
    description: str
    name: str
    requires_security: bool
    tags: tuple[str, ...]
    summary: str = ""
    query_params: list[ParamIR] = field(default_factory=list[ParamIR])
    path_params: list[ParamIR] = field(default_factory=list[ParamIR])
    header_params: list[ParamIR] = field(default_factory=list[ParamIR])
    cookie_params: list[ParamIR] = field(default_factory=list[ParamIR])
    responses: list[ResponseIR] = field(default_factory=list[ResponseIR])
    request_body: RequestBodyIR | None = None
    errors: list[ParseError] = field(default_factory=list[ParseError])
    deprecated: bool = False
    security: tuple[dict[str, tuple[str, ...]], ...] = ()

    @property
    def params(self) -> tuple[ParamIR, ...]:
        """Return params in the same location order as upstream."""
        return tuple(
            self.path_params + self.query_params + self.header_params + self.cookie_params,
        )

    def add_params(
        self,
        *,
        data: oai.Operation | oai.PathItem,
        param_components: dict[ReferencePath, oai.Param],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> ParsedOperation | ParseError:
        """Add params, allowing earlier operation-level definitions to take precedence."""
        if data.params is None:
            return self
        operation = deepcopy(self)
        unique_params: set[tuple[str, oai.ParamLocation]] = set()
        params_by_location = {
            oai.ParamLocation.QUERY: operation.query_params,
            oai.ParamLocation.PATH: operation.path_params,
            oai.ParamLocation.HEADER: operation.header_params,
            oai.ParamLocation.COOKIE: operation.cookie_params,
        }
        for param_data in data.params:
            param = _param_from_reference(param_data, param_components)
            if isinstance(param, ParseError):
                return param
            if param.param_schema is None:
                continue
            identity = (param.name, param.param_in)
            if identity in unique_params:
                return ParseError(
                    data=data,
                    detail=(
                        "Params MUST NOT contain duplicates. A unique param is defined by a combination "
                        f"of a name and location. Duplicated params named `{param.name}` detected in "
                        f"`{param.param_in}`."
                    ),
                )
            unique_params.add(identity)
            existing = params_by_location[param.param_in]
            if any(item.wire_name == param.name for item in existing):
                continue
            lowered = _lower_param(param, schema_components)
            if isinstance(lowered, ParseError):
                return ParseError(
                    detail=f"cannot parse param of operation {operation.name}: {lowered.detail}",
                    data=lowered.data,
                )
            existing.append(lowered)
        return operation

    def sort_params(self) -> ParsedOperation | ParseError:
        """Sort path params into path-template order and validate the template."""
        operation = deepcopy(self)
        params_from_path = re.findall(_PATH_PARAM_REGEX, operation.path)
        try:
            operation.path_params.sort(key=lambda param: params_from_path.index(param.wire_name))
        except ValueError:
            pass
        if params_from_path != [param.wire_name for param in operation.path_params]:
            return ParseError(
                detail=f"Incorrect path templating for {operation.path} (Path params do not match with path)",
            )
        return operation

    @staticmethod
    def from_data(
        *,
        data: oai.Operation,
        path: str,
        method: HTTPMethod,
        param_components: dict[ReferencePath, oai.Param],
        request_bodies: dict[str, oai.RequestBody | oai.Reference],
        responses: dict[str, oai.Response | oai.Reference],
        schema_components: dict[str, oai.Schema | oai.Reference],
    ) -> ParsedOperation | ParseError:
        """Construct a parsed operation from one typed OpenAPI operation."""
        operation = ParsedOperation(
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
        result = operation.add_params(
            data=data,
            param_components=param_components,
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
class ParsedOpenAPI:
    """The parsed OpenAPI metadata, operations, and recoverable parser warnings."""

    title: str
    description: str | None
    version: str
    errors: list[ParseError]
    operation_collections_by_tag: dict[str, OperationCollection]

    @property
    def operations(self) -> tuple[ParsedOperation, ...]:
        """Return the flat operation sequence used by manifest selection."""
        return tuple(
            operation
            for collection in self.operation_collections_by_tag.values()
            for operation in collection.operations
        )

    def operation_at(self, path: str, method: HTTPMethod) -> ParsedOperation | None:
        """Find an operation by wire path and method."""
        return next((item for item in self.operations if item.path == path and item.method is method), None)

    def operation_named(self, operation_id: str) -> ParsedOperation | None:
        """Find an operation by explicit or generated operation ID."""
        return next((item for item in self.operations if item.name == operation_id), None)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ParsedOpenAPI:
        """Hydrate the vendored model and parse it using the upstream workflow."""
        openapi = oai.OpenAPI.model_validate(data)

        components = openapi.components
        schema_components = (components and components.schemas) or {}
        param_components, errors = _build_params((components and components.params) or {})
        request_bodies = (components and components.requestBodies) or {}
        responses = (components and components.responses) or {}
        operation_collections = OperationCollection.from_data(
            data=openapi.paths,
            param_components=param_components,
            request_bodies=request_bodies,
            responses=responses,
            schema_components=schema_components,
        )
        return ParsedOpenAPI(
            title=openapi.info.title,
            description=openapi.info.description,
            version=openapi.info.version,
            operation_collections_by_tag=operation_collections,
            errors=errors,
        )


def _build_params(
    components: dict[str, oai.Reference | oai.Param],
) -> tuple[dict[ReferencePath, oai.Param], list[ParseError]]:
    """Build the reusable param registry using upstream's accepted subset."""
    params: dict[ReferencePath, oai.Param] = {}
    errors: list[ParseError] = []
    for name, data in components.items():
        if isinstance(data, oai.Reference):
            errors.append(ParamError(data=data, detail="Reference params are not supported."))
            continue
        path = parse_reference_path(f"#/components/parameters/{name}")
        if isinstance(path, ParseError):
            errors.append(ParamError(detail=path.detail, data=data))
            continue
        params[path] = data
    return params, errors


def _param_from_reference(
    param: oai.Reference | oai.Param,
    params: dict[ReferencePath, oai.Param],
) -> oai.Param | ParamError:
    """Resolve one param reference from the component registry."""
    if isinstance(param, oai.Param):
        return param
    path = parse_reference_path(param.ref)
    if isinstance(path, ParseError):
        return ParamError(detail=path.detail)
    resolved = params.get(path)
    if resolved is None:
        return ParamError(detail=f"Reference `{path}` not found.")
    return resolved


def _lower_param(
    param: oai.Param,
    schema_components: dict[str, oai.Schema | oai.Reference],
) -> ParamIR | PropertyError:
    """Lower the schema-bearing part of a typed OpenAPI param."""
    assert param.param_schema is not None  # noqa: S101 - caller follows upstream's schema-less skip
    schema = lower_schema_type(param.param_schema, schema_components)
    if isinstance(schema, PropertyError):
        return schema
    content = _lower_content(param.content or {}, schema_components)
    if isinstance(content, PropertyError):
        return content
    param_schema = param.param_schema
    has_default = isinstance(param_schema, oai.Schema) and "default" in param_schema.model_fields_set
    default = cast("Any", param_schema.default) if isinstance(param_schema, oai.Schema) else None
    return ParamIR(
        wire_name=param.name,
        location=ParamLocationIR(param.param_in.value),
        required=param.required,
        schema=schema,
        content=content,
        style=param.style,
        explode=param.explode,
        allow_reserved=param.allowReserved,
        description=param.description or "",
        default=default,
        has_default=has_default,
        deprecated=param.deprecated,
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
            header="Operation requires a body, but none were parseable.",
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
