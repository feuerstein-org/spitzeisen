"""Parse a Smithy JSON AST into the small protocol IR used by Spitzeisen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

from spitzeisen.codegen.ir import (
    ArrayType,
    HTTPMethod,
    JSONValue,
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
from spitzeisen.codegen.parser.errors import ParseError

_DOCUMENTATION = "smithy.api#documentation"
_HTTP = "smithy.api#http"
_HTTP_ERROR = "smithy.api#httpError"
_HTTP_HEADER = "smithy.api#httpHeader"
_HTTP_LABEL = "smithy.api#httpLabel"
_HTTP_PAYLOAD = "smithy.api#httpPayload"
_HTTP_QUERY = "smithy.api#httpQuery"
_REQUIRED = "smithy.api#required"
_DEFAULT = "smithy.api#default"
_RANGE = "smithy.api#range"
_ENUM_VALUE = "smithy.api#enumValue"
_CONTENT_TYPE = "smithytranslate#contentType"

_PRIMITIVES: dict[str, PrimitiveType | ObjectType] = {
    "Blob": PrimitiveType(PrimitiveKind.STRING, format="binary"),
    "Boolean": PrimitiveType(PrimitiveKind.BOOLEAN),
    "Byte": PrimitiveType(PrimitiveKind.INTEGER),
    "Short": PrimitiveType(PrimitiveKind.INTEGER),
    "Integer": PrimitiveType(PrimitiveKind.INTEGER),
    "Long": PrimitiveType(PrimitiveKind.INTEGER),
    "BigInteger": PrimitiveType(PrimitiveKind.INTEGER),
    "Float": PrimitiveType(PrimitiveKind.NUMBER),
    "Double": PrimitiveType(PrimitiveKind.NUMBER),
    "BigDecimal": PrimitiveType(PrimitiveKind.NUMBER),
    "String": PrimitiveType(PrimitiveKind.STRING),
    "Timestamp": PrimitiveType(PrimitiveKind.STRING, format="date-time"),
    "Document": ObjectType(),
}


def _shape_name(shape_id: str) -> str:
    """Return the local portion of a Smithy shape ID."""
    return shape_id.rsplit("#", maxsplit=1)[-1]


def _empty_renames() -> dict[str, str]:
    """Create a typed service rename map for dataclass defaults."""
    return {}


def _traits(value: object) -> dict[str, Any]:
    """Read a JSON AST traits object without spreading casts through the parser."""
    if not isinstance(value, dict):
        return {}
    mapping = cast("dict[str, object]", value)
    raw = mapping.get("traits")
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _target(value: object, *, context: str) -> str:
    """Read a required Smithy target reference."""
    if not isinstance(value, dict):
        message = f"{context} does not declare a target shape"
        raise TypeError(message)
    target = cast("dict[str, object]", value).get("target")
    if not isinstance(target, str):
        message = f"{context} does not declare a target shape"
        raise TypeError(message)
    return target


def _members(shape: dict[str, Any]) -> dict[str, Any]:
    """Read an aggregate shape's members."""
    raw = shape.get("members")
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _range(traits: dict[str, Any]) -> tuple[float | None, float | None]:
    """Read Smithy's inclusive numeric bounds."""
    value = traits.get(_RANGE)
    if not isinstance(value, dict):
        return None, None
    bounds = cast("dict[str, object]", value)
    minimum = bounds.get("min")
    maximum = bounds.get("max")
    return (
        float(minimum) if isinstance(minimum, (int, float)) else None,
        float(maximum) if isinstance(maximum, (int, float)) else None,
    )


def _with_member_traits(schema: TypeIR, traits: dict[str, Any]) -> TypeIR:
    """Apply member-level constraints that refine a simple target shape."""
    if not isinstance(schema, PrimitiveType):
        return schema
    minimum, maximum = _range(traits)
    return PrimitiveType(
        kind=schema.kind,
        format=schema.format,
        minimum=minimum if minimum is not None else schema.minimum,
        maximum=maximum if maximum is not None else schema.maximum,
    )


def lower_target(  # noqa: C901, PLR0911
    target: str,
    shapes: dict[str, Any],
    member_traits: dict[str, Any] | None = None,
) -> TypeIR:
    """Lower a Smithy target shape into Spitzeisen's generator-neutral type tree."""
    traits = member_traits or {}
    if target.startswith("smithy.api#"):
        primitive = _PRIMITIVES.get(_shape_name(target))
        return _with_member_traits(primitive, traits) if primitive is not None else UnknownType(target)

    raw_shape = shapes.get(target)
    if not isinstance(raw_shape, dict):
        return UnknownType(f"unresolved Smithy shape {target}")
    shape = cast("dict[str, Any]", raw_shape)
    shape_type = shape.get("type")
    combined_traits = {**_traits(shape), **traits}

    simple_shapes = {
        "string": "String",
        "blob": "Blob",
        "boolean": "Boolean",
        "byte": "Byte",
        "short": "Short",
        "integer": "Integer",
        "long": "Long",
        "bigInteger": "BigInteger",
        "float": "Float",
        "double": "Double",
        "bigDecimal": "BigDecimal",
        "timestamp": "Timestamp",
        "document": "Document",
    }
    if shape_type in simple_shapes:
        primitive = _PRIMITIVES[simple_shapes[cast("str", shape_type)]]
        if isinstance(primitive, PrimitiveType) and any(name.endswith("#dateFormat") for name in combined_traits):
            primitive = PrimitiveType(PrimitiveKind.STRING, format="date")
        return _with_member_traits(primitive, combined_traits)
    if shape_type in {"enum", "intEnum"}:
        values: list[str | int | float | bool | None] = []
        for member in _members(shape).values():
            value = _traits(member).get(_ENUM_VALUE)
            if isinstance(value, (str, int, float, bool)) or value is None:
                values.append(value)
        return LiteralType(tuple(values))
    if shape_type in {"list", "set"}:
        return ArrayType(lower_target(_target(shape.get("member"), context=target), shapes))
    if shape_type == "map":
        return ObjectType(lower_target(_target(shape.get("value"), context=target), shapes))
    if shape_type == "union":
        options = tuple(
            lower_target(_target(member, context=f"{target}${name}"), shapes, _traits(member))
            for name, member in _members(shape).items()
        )
        return UnionType(options) if options else UnknownType(f"empty Smithy union {target}")
    if shape_type == "structure":
        return ReferenceType(schema_id=SchemaId(reference=target), suggested_name=_shape_name(target))
    return UnknownType(f"unsupported Smithy {shape_type!r} shape {target}")


def _binding(member_name: str, member: object) -> tuple[ParamLocationIR, str] | None:
    """Return the HTTP binding of an input member, if it is a public argument."""
    traits = _traits(member)
    for trait, location in (
        (_HTTP_QUERY, ParamLocationIR.QUERY),
        (_HTTP_HEADER, ParamLocationIR.HEADER),
        (_HTTP_LABEL, ParamLocationIR.PATH),
    ):
        if trait not in traits:
            continue
        raw_name = traits[trait]
        wire_name = member_name if trait == _HTTP_LABEL else raw_name
        if not isinstance(wire_name, str):
            message = f"HTTP binding {trait} on {member_name!r} must contain a string"
            raise TypeError(message)
        return location, wire_name
    return None


def _param(member_name: str, member: object, shapes: dict[str, Any]) -> ParamIR | None:
    """Lower one HTTP-bound input member."""
    binding = _binding(member_name, member)
    if binding is None:
        return None
    location, wire_name = binding
    traits = _traits(member)
    default = traits.get(_DEFAULT)
    return ParamIR(
        wire_name=wire_name,
        location=location,
        required=_REQUIRED in traits,
        schema=lower_target(_target(member, context=member_name), shapes, traits),
        content=(),
        style="form",
        explode=False,
        allow_reserved=False,
        description=str(traits.get(_DOCUMENTATION, "")),
        default=cast("JSONValue", default),
        has_default=_DEFAULT in traits,
        deprecated="smithy.api#deprecated" in traits,
    )


def _content_for_structure(target: str, shapes: dict[str, Any]) -> tuple[MediaTypeIR, ...]:
    """Find an output payload and its content type."""
    raw_shape = shapes.get(target)
    if not isinstance(raw_shape, dict):
        return ()
    shape = cast("dict[str, Any]", raw_shape)
    for member in _members(shape).values():
        traits = _traits(member)
        if _HTTP_PAYLOAD not in traits:
            continue
        media_type = traits.get(_CONTENT_TYPE, "application/json")
        return (
            MediaTypeIR(
                media_type=str(media_type),
                schema=lower_target(_target(member, context=f"payload of {target}"), shapes, traits),
            ),
        )
    return (MediaTypeIR("application/json", lower_target(target, shapes)),)


def _responses(operation: dict[str, Any], shapes: dict[str, Any], success_code: int) -> list[ResponseIR]:
    """Lower the operation output and modeled errors."""
    responses: list[ResponseIR] = []
    if "output" in operation:
        target = _target(operation["output"], context="operation output")
        responses.append(
            ResponseIR(
                status=StatusPattern(str(success_code), success_code, success_code),
                description=str(_traits(shapes.get(target)).get(_DOCUMENTATION, "")),
                content=_content_for_structure(target, shapes),
            ),
        )
    raw_errors = operation.get("errors", [])
    if isinstance(raw_errors, list):
        for error in cast("list[object]", raw_errors):
            target = _target(error, context="operation error")
            traits = _traits(shapes.get(target))
            status = traits.get(_HTTP_ERROR)
            if not isinstance(status, int):
                continue
            responses.append(
                ResponseIR(
                    status=StatusPattern(str(status), status, status),
                    description=str(traits.get(_DOCUMENTATION, "")),
                    content=_content_for_structure(target, shapes),
                ),
            )
    return sorted(responses, key=lambda item: item.status.minimum or 999)


def _request_body(input_target: str, shapes: dict[str, Any]) -> RequestBodyIR | None:
    """Represent input members not bound into the URI, query string, or headers."""
    raw_shape = shapes.get(input_target)
    if not isinstance(raw_shape, dict):
        return None
    unbound = [
        (name, member)
        for name, member in _members(cast("dict[str, Any]", raw_shape)).items()
        if _binding(name, member) is None
    ]
    if not unbound:
        return None
    payload = next((item for item in unbound if _HTTP_PAYLOAD in _traits(item[1])), None)
    if payload is not None:
        name, member = payload
        traits = _traits(member)
        content = (
            MediaTypeIR(
                str(traits.get(_CONTENT_TYPE, "application/json")),
                lower_target(_target(member, context=name), shapes, traits),
            ),
        )
        return RequestBodyIR(
            required=_REQUIRED in traits,
            description=str(traits.get(_DOCUMENTATION, "")),
            content=content,
        )
    return RequestBodyIR(
        required=any(_REQUIRED in _traits(member) for _, member in unbound),
        description="",
        content=(MediaTypeIR("application/json", lower_target(input_target, shapes)),),
    )


@dataclass
class ParsedOperation:
    """One Smithy operation lowered to the protocol facts used by SDK policy."""

    shape_id: str
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
    input_target: str | None = None
    output_target: str | None = None
    traits: dict[str, Any] = field(default_factory=dict[str, Any])
    param_traits: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])

    @property
    def params(self) -> tuple[ParamIR, ...]:
        """Return params in stable path/query/header order."""
        return tuple(self.path_params + self.query_params + self.header_params + self.cookie_params)

    @classmethod
    def from_shape(  # noqa: C901, PLR0915 - one pass keeps operation/member context together
        cls,
        shape_id: str,
        shape: dict[str, Any],
        shapes: dict[str, Any],
    ) -> ParsedOperation:
        """Build a parsed operation from a Smithy operation shape."""
        traits = _traits(shape)
        http = traits.get(_HTTP)
        if not isinstance(http, dict):
            message = f"Smithy operation {shape_id} has no @http trait"
            raise TypeError(message)
        http_trait = cast("dict[str, object]", http)
        raw_method = http_trait.get("method")
        raw_uri = http_trait.get("uri")
        if not isinstance(raw_method, str) or not isinstance(raw_uri, str):
            message = f"Smithy operation {shape_id} has an invalid @http trait"
            raise TypeError(message)
        try:
            method = HTTPMethod(raw_method.lower())
        except ValueError as err:
            message = f"Smithy operation {shape_id} uses unsupported HTTP method {raw_method!r}"
            raise ValueError(message) from err
        success_code = http_trait.get("code", 200)
        if not isinstance(success_code, int):
            message = f"Smithy operation {shape_id} has a non-integer success code"
            raise TypeError(message)

        params: list[ParamIR] = []
        param_traits: dict[str, dict[str, Any]] = {}
        request_body = None
        input_target = None
        if "input" in shape:
            input_target = _target(shape["input"], context=f"input of {shape_id}")
            raw_input = shapes.get(input_target)
            if not isinstance(raw_input, dict):
                message = f"input of {shape_id} must target a structure"
                raise TypeError(message)
            input_shape = cast("dict[str, Any]", raw_input)
            if input_shape.get("type") != "structure":
                message = f"input of {shape_id} must target a structure"
                raise ValueError(message)
            for name, member in _members(input_shape).items():
                parsed = _param(name, member, shapes)
                if parsed is not None:
                    params.append(parsed)
                    param_traits[parsed.wire_name] = _traits(member)
            request_body = _request_body(input_target, shapes)

        name = _shape_name(shape_id)
        docs = str(traits.get(_DOCUMENTATION, ""))
        tags = traits.get("smithy.api#tags", [])
        tag_values = cast("list[object]", tags) if isinstance(tags, list) else []
        operation = cls(
            shape_id=shape_id,
            path=raw_uri,
            method=method,
            description=docs,
            name=name,
            requires_security=bool(traits.get("smithy.api#auth")),
            tags=tuple(str(tag) for tag in tag_values),
            summary=docs.splitlines()[0] if docs else "",
            responses=_responses(shape, shapes, success_code),
            request_body=request_body,
            deprecated="smithy.api#deprecated" in traits,
            input_target=input_target,
            output_target=_target(shape["output"], context=f"output of {shape_id}") if "output" in shape else None,
            traits=traits,
            param_traits=param_traits,
        )
        by_location = {
            ParamLocationIR.PATH: operation.path_params,
            ParamLocationIR.QUERY: operation.query_params,
            ParamLocationIR.HEADER: operation.header_params,
            ParamLocationIR.COOKIE: operation.cookie_params,
        }
        for param in params:
            by_location[param.location].append(param)
        placeholders = re.findall(r"\{([^}+]+)\+?}", raw_uri)
        operation.path_params.sort(
            key=lambda param: (
                placeholders.index(param.wire_name) if param.wire_name in placeholders else len(placeholders)
            ),
        )
        if placeholders != [param.wire_name for param in operation.path_params]:
            message = f"Smithy @http URI labels do not match input bindings for {shape_id}"
            raise TypeError(message)
        return operation


@dataclass(frozen=True, slots=True)
class ParsedService:
    """One Smithy service and the operations directly bound to it."""

    shape_id: str
    name: str
    operation_ids: tuple[str, ...]
    renames: dict[str, str] = field(default_factory=_empty_renames)
    documentation: str = ""
    documentation_url: str | None = None


@dataclass
class ParsedSmithy:
    """A validated Smithy JSON AST and its parsed HTTP operations."""

    operations: tuple[ParsedOperation, ...]
    services: tuple[ParsedService, ...] = ()
    errors: tuple[ParseError, ...] = ()

    def operation_at(self, path: str, method: HTTPMethod) -> ParsedOperation | None:
        """Find an operation by wire path and method."""
        return next((item for item in self.operations if item.path == path and item.method is method), None)

    def operation_named(self, operation_id: str) -> ParsedOperation | None:
        """Find an operation by Smithy shape name, tolerating lower-camel OpenAPI IDs."""
        normalized = operation_id.casefold()
        return next((item for item in self.operations if item.name.casefold() == normalized), None)

    def service_named(self, service_id: str | None) -> ParsedService:
        """Select an explicit service, or infer the only service in the model."""
        if service_id is None:
            if len(self.services) == 1:
                return self.services[0]
            if not self.services:
                msg = "the Smithy model contains no service shape"
                raise ValueError(msg)
            choices = [service.shape_id for service in self.services]
            msg = f"the Smithy model contains multiple services; select one of {choices}"
            raise ValueError(msg)
        normalized = service_id.casefold()
        matching = [
            service
            for service in self.services
            if service.shape_id.casefold() == normalized or service.name.casefold() == normalized
        ]
        if len(matching) != 1:
            choices = [service.shape_id for service in self.services]
            msg = f"service {service_id!r} is absent or ambiguous; available services: {choices}"
            raise ValueError(msg)
        return matching[0]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedSmithy:
        """Parse a Smithy 2 JSON AST and reject converter placeholders."""
        if data.get("smithy") != "2.0":
            message = "Spitzeisen requires a Smithy 2.0 JSON AST"
            raise ValueError(message)
        raw_shapes = data.get("shapes")
        if not isinstance(raw_shapes, dict):
            message = "Smithy JSON AST must contain a shapes object"
            raise TypeError(message)
        shapes = cast("dict[str, Any]", raw_shapes)
        placeholders = [
            shape_id
            for shape_id, shape in shapes.items()
            if any(trait.endswith("#errorMessage") for trait in _traits(shape))
        ]
        if placeholders:
            message = f"OpenAPI conversion left unsupported Smithy placeholders: {sorted(placeholders)}"
            raise ValueError(message)

        operations: list[ParsedOperation] = []
        services: list[ParsedService] = []
        errors: list[ParseError] = []
        for shape_id, raw_shape in shapes.items():
            if not isinstance(raw_shape, dict):
                continue
            shape = cast("dict[str, Any]", raw_shape)
            if shape.get("type") == "service":
                raw_operations = shape.get("operations", [])
                operation_ids = (
                    tuple(
                        _target(item, context=f"operation of service {shape_id}")
                        for item in cast("list[object]", raw_operations)
                    )
                    if isinstance(raw_operations, list)
                    else ()
                )
                traits = _traits(shape)
                raw_renames = shape.get("rename", {})
                rename_items = cast("dict[object, object]", raw_renames) if isinstance(raw_renames, dict) else {}
                if not isinstance(raw_renames, dict) or not all(
                    isinstance(source, str) and isinstance(target, str) for source, target in rename_items.items()
                ):
                    message = f"service {shape_id} contains an invalid rename mapping"
                    raise TypeError(message)
                renames = cast("dict[str, str]", raw_renames)
                raw_external_docs = traits.get("smithy.api#externalDocumentation", {})
                external_docs = (
                    cast("dict[str, object]", raw_external_docs) if isinstance(raw_external_docs, dict) else {}
                )
                documentation_url = next(iter(external_docs.values()), None)
                services.append(
                    ParsedService(
                        shape_id=shape_id,
                        name=_shape_name(shape_id),
                        operation_ids=operation_ids,
                        renames=renames,
                        documentation=str(traits.get(_DOCUMENTATION, "")),
                        documentation_url=str(documentation_url) if documentation_url is not None else None,
                    ),
                )
            elif shape.get("type") == "operation":
                try:
                    operations.append(ParsedOperation.from_shape(shape_id, shape, shapes))
                except (TypeError, ValueError) as err:
                    errors.append(ParseError(header=f"WARNING parsing Smithy operation {shape_id}", detail=str(err)))
        return cls(operations=tuple(operations), services=tuple(services), errors=tuple(errors))
