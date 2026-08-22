"""
Lower validated OpenAPI documents into Spitzeisen's neutral IR.

Parameter precedence and status-pattern ordering are adapted from openapi-python-client 0.29.0.
The algorithms were rewritten against Spitzeisen's policy-free IR; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote, urldefrag, urljoin

from spitzeisen.codegen.diagnostics import SourceLocation, error
from spitzeisen.codegen.ir import (
    ArrayType,
    DocumentIR,
    HTTPMethod,
    IntersectionType,
    JSONValue,
    LiteralType,
    MediaTypeIR,
    ObjectType,
    OpenAPIDialect,
    OperationIR,
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

if TYPE_CHECKING:
    from spitzeisen.codegen.document import OpenAPIDocument, OpenAPINode
    from spitzeisen.codegen.ir import JSONScalar

_PATH_PARAMETER = re.compile(r"{([^{}]+)}")
_ANNOTATION_KEYS = {
    "$comment",
    "$id",
    "$schema",
    "default",
    "deprecated",
    "description",
    "examples",
    "example",
    "externalDocs",
    "readOnly",
    "title",
    "writeOnly",
    "xml",
}


def lower_document(document: OpenAPIDocument) -> DocumentIR:
    """Lower all operations from a validated document."""
    info = _mapping(document.data.get("info"), document.root.child("info").location, "Info Object")
    paths = _mapping(document.data.get("paths", {}), document.root.child("paths").location, "Paths Object")
    operations: list[OperationIR] = []
    seen_operation_ids: dict[str, SourceLocation] = {}

    for path in paths:
        if not path.startswith("/"):
            continue
        path_node = document.node("paths", path)
        path_item = _mapping(path_node.resolved, path_node.location, "Path Item Object")
        for method in HTTPMethod:
            if method.value not in path_item:
                continue
            operation_node = path_node.child(method.value)
            operation = _lower_operation(document, path, method, path_node, operation_node)
            if operation.operation_id is not None:
                previous = seen_operation_ids.get(operation.operation_id)
                if previous is not None:
                    code = "openapi.operation-id"
                    message = f"operationId {operation.operation_id!r} is also declared at {previous}"
                    raise error(
                        code,
                        message,
                        operation.source,
                    )
                seen_operation_ids[operation.operation_id] = operation.source
            operations.append(operation)

    return DocumentIR(
        source=SourceLocation(document.base_uri),
        dialect=document.dialect,
        version=document.version,
        title=_string(info.get("title")),
        description=_string(info.get("description")),
        operations=tuple(operations),
    )


def _lower_operation(
    document: OpenAPIDocument,
    path: str,
    method: HTTPMethod,
    path_node: OpenAPINode,
    operation_node: OpenAPINode,
) -> OperationIR:
    operation = _mapping(operation_node.resolved, operation_node.location, "Operation Object")
    parameters = _effective_parameters(document, path, path_node, operation_node)
    request_body = (
        _lower_request_body(operation_node.child("requestBody"), document.dialect)
        if "requestBody" in operation
        else None
    )
    responses_value = _mapping(
        operation.get("responses"),
        operation_node.child("responses").location,
        "Responses Object",
    )
    responses = tuple(
        sorted(
            (
                _lower_response(operation_node.child("responses", str(status)), str(status), document.dialect)
                for status in responses_value
            ),
            key=lambda response: _status_sort_key(response.status),
        ),
    )
    operation_id = operation.get("operationId")
    tags = operation.get("tags", [])
    security = operation.get("security", [])
    return OperationIR(
        source=operation_node.location,
        method=method,
        path=path,
        operation_id=operation_id if isinstance(operation_id, str) else None,
        tags=(
            tuple(item for item in cast("list[object]", tags) if isinstance(item, str))
            if isinstance(tags, list)
            else ()
        ),
        summary=_string(operation.get("summary")),
        description=_string(operation.get("description")),
        parameters=parameters,
        request_body=request_body,
        responses=responses,
        deprecated=operation.get("deprecated") is True,
        security=_security_requirements(security),
        extensions={key: cast("JSONValue", value) for key, value in operation.items() if key.startswith("x-")},
    )


def _effective_parameters(
    document: OpenAPIDocument,
    path: str,
    path_node: OpenAPINode,
    operation_node: OpenAPINode,
) -> tuple[ParameterIR, ...]:
    """
    Apply OpenAPI's operation-over-path precedence for `(name, in)` pairs.

    This follows the precedence used by openapi-python-client: operation parameters are
    collected first, then path-item parameters with an already-seen identity are ignored.
    """
    effective: list[ParameterIR] = []
    seen: set[tuple[str, ParameterLocation]] = set()
    for owner in (operation_node, path_node):
        owner_value = _mapping(owner.resolved, owner.location, "parameter owner")
        parameters = owner_value.get("parameters", [])
        if not isinstance(parameters, list):
            code = "openapi.parameters"
            message = "parameters must be an array"
            raise error(code, message, owner.child("parameters").location)
        parameter_values = cast("list[object]", parameters)
        for index in range(len(parameter_values)):
            parameter = _lower_parameter(owner.child("parameters", index), document.dialect)
            identity = (parameter.wire_name, parameter.location)
            if identity in seen:
                continue
            seen.add(identity)
            effective.append(parameter)

    path_names = _PATH_PARAMETER.findall(path)
    declared_path = [parameter for parameter in effective if parameter.location is ParameterLocation.PATH]
    if set(path_names) != {parameter.wire_name for parameter in declared_path}:
        code = "openapi.path-parameters"
        message = (
            f"path placeholders {path_names!r} do not match declared path parameters "
            f"{[parameter.wire_name for parameter in declared_path]!r}"
        )
        raise error(
            code,
            message,
            operation_node.location,
        )
    by_name = {parameter.wire_name: parameter for parameter in declared_path}
    ordered_path = [by_name[name] for name in path_names]
    non_path = [parameter for parameter in effective if parameter.location is not ParameterLocation.PATH]
    return *ordered_path, *non_path


def _lower_parameter(node: OpenAPINode, dialect: OpenAPIDialect) -> ParameterIR:
    parameter = _mapping(node.resolved, node.location, "Parameter Object")
    name = parameter.get("name")
    location_value = parameter.get("in")
    if not isinstance(name, str) or not isinstance(location_value, str):
        code = "openapi.parameter"
        message = "a parameter needs string `name` and `in` fields"
        raise error(code, message, node.location)
    try:
        location = ParameterLocation(location_value)
    except ValueError as exc:
        code = "openapi.parameter-location"
        message = f"unsupported parameter location {location_value!r}"
        raise error(code, message, node.location) from exc

    schema_value = parameter.get("schema")
    schema = (
        lower_schema_type(
            schema_value,
            source=SourceLocation(node.location.uri, f"{node.location.pointer}/schema"),
            base_uri=_resolved_base_uri(node),
            dialect=dialect,
        )
        if schema_value is not None
        else None
    )
    content = _lower_content(
        parameter.get("content", {}),
        source=SourceLocation(node.location.uri, f"{node.location.pointer}/content"),
        base_uri=_resolved_base_uri(node),
        dialect=dialect,
    )
    style_default, explode_default = {
        ParameterLocation.QUERY: ("form", True),
        ParameterLocation.COOKIE: ("form", True),
        ParameterLocation.PATH: ("simple", False),
        ParameterLocation.HEADER: ("simple", False),
    }[location]
    style = parameter.get("style")
    explode = parameter.get("explode")
    schema_mapping: Mapping[str, Any] = (
        cast("Mapping[str, Any]", schema_value) if isinstance(schema_value, dict) else dict[str, Any]()
    )
    return ParameterIR(
        wire_name=name,
        location=location,
        required=parameter.get("required") is True,
        schema=schema,
        content=content,
        style=style if isinstance(style, str) else style_default,
        explode=explode if isinstance(explode, bool) else explode_default,
        allow_reserved=parameter.get("allowReserved") is True,
        description=_string(parameter.get("description")),
        default=cast("JSONValue", schema_mapping.get("default")),
        has_default="default" in schema_mapping,
        deprecated=parameter.get("deprecated") is True,
        source=node.location,
    )


def _lower_request_body(node: OpenAPINode, dialect: OpenAPIDialect) -> RequestBodyIR:
    body = _mapping(node.resolved, node.location, "Request Body Object")
    return RequestBodyIR(
        required=body.get("required") is True,
        description=_string(body.get("description")),
        content=_lower_content(
            body.get("content", {}),
            source=SourceLocation(node.location.uri, f"{node.location.pointer}/content"),
            base_uri=_resolved_base_uri(node),
            dialect=dialect,
        ),
        source=node.location,
    )


def _lower_response(node: OpenAPINode, status: str, dialect: OpenAPIDialect) -> ResponseIR:
    response = _mapping(node.resolved, node.location, "Response Object")
    return ResponseIR(
        status=_status_pattern(status, node.location),
        description=_string(response.get("description")),
        content=_lower_content(
            response.get("content", {}),
            source=SourceLocation(node.location.uri, f"{node.location.pointer}/content"),
            base_uri=_resolved_base_uri(node),
            dialect=dialect,
        ),
        source=node.location,
    )


def _lower_content(
    value: Any,
    *,
    source: SourceLocation,
    base_uri: str,
    dialect: OpenAPIDialect,
) -> tuple[MediaTypeIR, ...]:
    content = _mapping(value, source, "Content Object")
    lowered: list[MediaTypeIR] = []
    for media_type, media_value in content.items():
        media = _mapping(media_value, source, "Media Type Object")
        media_source = SourceLocation(source.uri, f"{source.pointer}/{_escape(media_type)}")
        schema_value = media.get("schema")
        lowered.append(
            MediaTypeIR(
                media_type=media_type,
                schema=(
                    lower_schema_type(
                        schema_value,
                        source=SourceLocation(media_source.uri, f"{media_source.pointer}/schema"),
                        base_uri=base_uri,
                        dialect=dialect,
                    )
                    if schema_value is not None
                    else None
                ),
                source=media_source,
            ),
        )
    return tuple(lowered)


def lower_schema_type(  # noqa: C901, PLR0911, PLR0912
    value: Any,
    *,
    source: SourceLocation,
    base_uri: str,
    dialect: OpenAPIDialect,
) -> TypeIR:
    """Lower the type-relevant portion of an OpenAPI Schema Object."""
    if value is True:
        return UnknownType("an unconstrained true schema")
    if value is False:
        return UnknownType("an unsatisfiable false schema")
    schema = _mapping(value, source, "Schema Object")

    reference = schema.get("$ref")
    if isinstance(reference, str):
        referenced = _reference_type(reference, base_uri)
        structural_siblings = {
            key: item for key, item in schema.items() if key != "$ref" and key not in _ANNOTATION_KEYS
        }
        if dialect is OpenAPIDialect.V3_0 or not structural_siblings:
            return referenced
        sibling_type = lower_schema_type(structural_siblings, source=source, base_uri=base_uri, dialect=dialect)
        return IntersectionType((referenced, sibling_type))

    enum = schema.get("enum")
    if isinstance(enum, list):
        return LiteralType(_json_scalars(cast("Sequence[object]", enum)))
    if "const" in schema and isinstance(schema["const"], (str, int, float, bool, type(None))):
        return LiteralType((schema["const"],))

    composite: TypeIR | None = None
    for keyword, composite_type in (
        ("oneOf", UnionType),
        ("anyOf", UnionType),
        ("allOf", IntersectionType),
    ):
        members = schema.get(keyword)
        if isinstance(members, list) and members:
            member_values = cast("list[object]", members)
            lowered = tuple(
                lower_schema_type(member, source=source, base_uri=base_uri, dialect=dialect) for member in member_values
            )
            composite = composite_type(lowered)
            break

    type_value = schema.get("type")
    if isinstance(type_value, list):
        type_values = cast("list[object]", type_value)
        primitive_options = tuple(
            _primitive(item, schema)
            for item in type_values
            if isinstance(item, str) and item in {kind.value for kind in PrimitiveKind}
        )
        if primitive_options:
            composite = UnionType(primitive_options)
    elif type_value == "array":
        items = schema.get("items", True)
        composite = ArrayType(lower_schema_type(items, source=source, base_uri=base_uri, dialect=dialect))
    elif type_value == "object" or (type_value is None and "properties" in schema):
        additional = schema.get("additionalProperties")
        composite = ObjectType(
            lower_schema_type(additional, source=source, base_uri=base_uri, dialect=dialect)
            if isinstance(additional, (dict, bool))
            else None,
        )
    elif isinstance(type_value, str) and type_value in {kind.value for kind in PrimitiveKind}:
        composite = _primitive(type_value, schema)

    if composite is None:
        composite = UnknownType("the schema declares no supported type keyword")
    if dialect is OpenAPIDialect.V3_0 and schema.get("nullable") is True:
        return _union((composite, PrimitiveType(PrimitiveKind.NULL)))
    return composite


def _primitive(type_value: str, schema: Mapping[str, Any]) -> PrimitiveType:
    """Build a primitive type from validated schema values."""
    format_value = schema.get("format")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    return PrimitiveType(
        kind=PrimitiveKind(type_value),
        format=format_value if isinstance(format_value, str) else None,
        minimum=float(minimum) if isinstance(minimum, (int, float)) else None,
        maximum=float(maximum) if isinstance(maximum, (int, float)) else None,
    )


def _reference_type(reference: str, base_uri: str) -> ReferenceType:
    """Build a stable reference without eagerly expanding recursive schemas."""
    absolute = f"{base_uri}{reference}" if reference.startswith("#") else urljoin(base_uri, reference)
    uri, fragment = urldefrag(absolute)
    pointer = unquote(fragment)
    final = pointer.rsplit("/", maxsplit=1)[-1] if pointer else uri.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return ReferenceType(schema_id=SchemaId(uri=uri, pointer=pointer), suggested_name=unquote(final))


def _union(options: tuple[TypeIR, ...]) -> TypeIR:
    """Flatten and deduplicate a union while preserving declaration order."""
    flattened: list[TypeIR] = []
    for option in options:
        candidates = option.options if isinstance(option, UnionType) else (option,)
        for candidate in candidates:
            if candidate not in flattened:
                flattened.append(candidate)
    return flattened[0] if len(flattened) == 1 else UnionType(tuple(flattened))


def _status_pattern(raw: str, location: SourceLocation) -> StatusPattern:
    """Parse exact, ranged, and default response keys."""
    if raw == "default":
        return StatusPattern(raw=raw, minimum=None, maximum=None)
    normalized = raw.upper()
    status_pattern_length = 3
    if len(normalized) == status_pattern_length and normalized[0].isdigit() and normalized[1:] == "XX":
        first = int(normalized[0]) * 100
        return StatusPattern(raw=raw, minimum=first, maximum=first + 99)
    try:
        status = int(raw)
    except ValueError as exc:
        code = "openapi.response-status"
        message = f"invalid response status pattern {raw!r}"
        raise error(code, message, location) from exc
    return StatusPattern(raw=raw, minimum=status, maximum=status)


def _status_sort_key(status: StatusPattern) -> tuple[int, int]:
    """Order exact statuses before ranges and the default response last."""
    if status.is_default:
        return (2, 0)
    assert status.minimum is not None  # noqa: S101 - established by is_default
    return (1 if status.minimum != status.maximum else 0, status.minimum)


def _resolved_base_uri(node: OpenAPINode) -> str:
    """Return the URI against which references inside a resolved node are interpreted."""
    try:
        reference = node.reference
    except KeyError:
        reference = None
    if reference is None:
        return node.document.base_uri
    absolute = (
        f"{node.document.base_uri}{reference}"
        if reference.startswith("#")
        else urljoin(node.document.base_uri, reference)
    )
    return urldefrag(absolute)[0]


def _security_requirements(value: Any) -> tuple[dict[str, tuple[str, ...]], ...]:
    """Normalize OpenAPI security requirements without interpreting auth policy."""
    if not isinstance(value, list):
        return ()
    requirements: list[dict[str, tuple[str, ...]]] = []
    for item in cast("list[object]", value):
        if not isinstance(item, dict):
            continue
        requirement = cast("dict[object, object]", item)
        requirements.append(
            {
                key: tuple(scope for scope in cast("list[object]", scopes) if isinstance(scope, str))
                for key, scopes in requirement.items()
                if isinstance(key, str) and isinstance(scopes, list)
            },
        )
    return tuple(requirements)


def _mapping(value: Any, location: SourceLocation, name: str) -> Mapping[str, Any]:
    """Require an object where validation or reference resolution promised one."""
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        if all(isinstance(key, str) for key in mapping):
            return cast("Mapping[str, Any]", mapping)
    code = "openapi.object"
    message = f"{name} must be an object"
    raise error(code, message, location)


def _string(value: Any) -> str:
    """Return an optional OpenAPI string as a convenient empty-string value."""
    return value if isinstance(value, str) else ""


def _escape(token: str) -> str:
    """Escape one JSON Pointer token."""
    return token.replace("~", "~0").replace("/", "~1")


def _is_json_scalar(value: object) -> bool:
    """Whether a value can be represented by a LiteralType."""
    return isinstance(value, (str, int, float, bool, type(None)))


def _json_scalars(values: Sequence[object]) -> tuple[JSONScalar, ...]:
    """Narrow a validated enum to the recursive JSON scalar alias."""
    return tuple(cast("JSONScalar", value) for value in values if _is_json_scalar(value))
