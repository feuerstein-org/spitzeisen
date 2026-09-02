"""Strict JSON I/O for the target-neutral service-plan contract."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from spitzeisen.codegen.service_plan import (
    DefaultValue,
    EnumValuePlan,
    EventStreamsPlan,
    HttpBindingLocation,
    HttpBindingPlan,
    HttpErrorPlan,
    HttpPlan,
    JSONValue,
    MemberPlan,
    OperationPlan,
    Service,
    ServicePlan,
    ShapeKind,
    ShapePlan,
    TraitNodes,
)

SHAPE_KINDS: tuple[ShapeKind, ...] = (
    "bigDecimal",
    "bigInteger",
    "blob",
    "boolean",
    "byte",
    "document",
    "double",
    "enum",
    "float",
    "intEnum",
    "integer",
    "list",
    "long",
    "map",
    "set",
    "short",
    "string",
    "structure",
    "timestamp",
    "union",
)
HTTP_BINDING_LOCATIONS: tuple[HttpBindingLocation, ...] = (
    "document",
    "header",
    "label",
    "payload",
    "prefix_headers",
    "query",
    "query_params",
    "response_code",
    "unbound",
)
_SHAPE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*#[A-Za-z_][A-Za-z0-9_]*(?:\$[A-Za-z_][A-Za-z0-9_]*)?$")
_IMPLICIT_PRELUDE_TARGETS = frozenset(
    {
        "smithy.api#BigDecimal",
        "smithy.api#BigInteger",
        "smithy.api#Blob",
        "smithy.api#Boolean",
        "smithy.api#Byte",
        "smithy.api#Document",
        "smithy.api#Double",
        "smithy.api#Float",
        "smithy.api#Integer",
        "smithy.api#Long",
        "smithy.api#PrimitiveBoolean",
        "smithy.api#PrimitiveByte",
        "smithy.api#PrimitiveDouble",
        "smithy.api#PrimitiveFloat",
        "smithy.api#PrimitiveInteger",
        "smithy.api#PrimitiveLong",
        "smithy.api#PrimitiveShort",
        "smithy.api#Short",
        "smithy.api#String",
        "smithy.api#Timestamp",
        "smithy.api#Unit",
    }
)


class ServicePlanError(ValueError):
    """The service-plan document violates its structural contract."""


def service_plan_document(plan: ServicePlan) -> dict[str, Any]:
    """Return the canonical, unversioned JSON-compatible service-plan document."""
    return {
        "service": _service_document(plan.service),
        "operations": {shape_id: _operation_document(operation) for shape_id, operation in plan.operations.items()},
        "shapes": {shape_id: _shape_document(shape) for shape_id, shape in plan.shapes.items()},
        "extensions": _json_copy(plan.extensions, "extensions"),
    }


def service_plan_from_document(document: object) -> ServicePlan:
    """Parse and semantically validate a service plan without accepting extra fields."""
    raw = _object(document, "service plan")
    _fields(raw, "service plan", required={"service", "operations", "shapes", "extensions"})
    plan = ServicePlan(
        service=_service(_required(raw, "service", "service plan")),
        operations=_entity_map(_required(raw, "operations", "service plan"), "operations", _operation),
        shapes=_entity_map(_required(raw, "shapes", "service plan"), "shapes", _shape),
        extensions=_json_object(_required(raw, "extensions", "service plan"), "extensions"),
    )
    _validate_references(plan)
    return plan


def dumps_service_plan(plan: ServicePlan, *, indent: int | None = 2) -> str:
    """Serialize a service plan as deterministic standards-compliant JSON."""
    document = json.dumps(service_plan_document(plan), indent=indent, sort_keys=True, allow_nan=False)
    return document + ("\n" if indent else "")


def loads_service_plan(source: str | bytes | bytearray) -> ServicePlan:
    """Load a service plan from JSON text."""
    try:
        document: object = json.loads(source)
    except json.JSONDecodeError as error:
        msg = f"invalid service-plan JSON: {error.msg}"
        raise ServicePlanError(msg) from error
    return service_plan_from_document(document)


def read_service_plan(path: Path) -> ServicePlan:
    """Load a service plan from disk."""
    return loads_service_plan(path.read_text(encoding="utf-8"))


def write_service_plan(path: Path, plan: ServicePlan) -> None:
    """Write a canonical service-plan document to disk."""
    path.write_text(dumps_service_plan(plan), encoding="utf-8")


def _service_document(service: Service) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": service.id,
        "version": service.version,
        "protocols": list(service.protocols),
        "auth_schemes": list(service.auth_schemes),
        "operations": list(service.operations),
        "external_documentation": dict(service.external_documentation),
        "traits": _json_copy(service.traits, "service.traits"),
        "extensions": _json_copy(service.extensions, "service.extensions"),
        "policies": _json_copy(service.policies, "service.policies"),
    }
    if service.documentation is not None:
        result["documentation"] = service.documentation
    if service.title is not None:
        result["title"] = service.title
    return result


def _operation_document(operation: OperationPlan) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": operation.id,
        "input": operation.input,
        "output": operation.output,
        "errors": list(operation.errors),
        "auth_schemes": list(operation.auth_schemes),
        "external_documentation": dict(operation.external_documentation),
        "traits": _json_copy(operation.traits, f"{operation.id}.traits"),
        "extensions": _json_copy(operation.extensions, f"{operation.id}.extensions"),
        "policies": _json_copy(operation.policies, f"{operation.id}.policies"),
    }
    if operation.documentation is not None:
        result["documentation"] = operation.documentation
    if operation.http is not None:
        result["http"] = _http_document(operation.http)
    if operation.event_streams is not None:
        result["event_streams"] = _event_streams_document(operation.event_streams)
    return result


def _http_document(http: HttpPlan) -> dict[str, Any]:
    return {
        "method": http.method,
        "uri": http.uri,
        "code": http.code,
        "request_bindings": [_binding_document(binding) for binding in http.request_bindings],
        "response_bindings": [_binding_document(binding) for binding in http.response_bindings],
        "error_bindings": {
            shape_id: {
                "code": error.code,
                "bindings": [_binding_document(binding) for binding in error.bindings],
            }
            for shape_id, error in http.error_bindings.items()
        },
    }


def _binding_document(binding: HttpBindingPlan) -> dict[str, Any]:
    return {
        "member_id": binding.member_id,
        "location": binding.location,
        "name": binding.name,
        "greedy": binding.greedy,
    }


def _event_streams_document(event_streams: EventStreamsPlan) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if event_streams.input is not None:
        result["input"] = event_streams.input
    if event_streams.output is not None:
        result["output"] = event_streams.output
    return result


def _shape_document(shape: ShapePlan) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": shape.id,
        "kind": shape.kind,
        "recursive": shape.recursive,
        "traits": _json_copy(shape.traits, f"{shape.id}.traits"),
        "extensions": _json_copy(shape.extensions, f"{shape.id}.extensions"),
        "constraints": _json_copy(shape.constraints, f"{shape.id}.constraints"),
        "policies": _json_copy(shape.policies, f"{shape.id}.policies"),
        "members": [_member_document(member) for member in shape.members],
        "enum_values": [_enum_value_document(value) for value in shape.enum_values],
    }
    if shape.documentation is not None:
        result["documentation"] = shape.documentation
    return result


def _member_document(member: MemberPlan) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": member.id,
        "name": member.name,
        "target": member.target,
        "required": member.required,
        "client_nullable": member.client_nullable,
        "default": _default_document(member.default),
        "traits": _json_copy(member.traits, f"{member.id}.traits"),
        "extensions": _json_copy(member.extensions, f"{member.id}.extensions"),
        "constraints": _json_copy(member.constraints, f"{member.id}.constraints"),
        "policies": _json_copy(member.policies, f"{member.id}.policies"),
    }
    if member.documentation is not None:
        result["documentation"] = member.documentation
    return result


def _default_document(default: DefaultValue) -> dict[str, Any]:
    result: dict[str, Any] = {"present": default.present}
    if default.present:
        result["value"] = _json_copy(default.value, "default.value")
    return result


def _enum_value_document(value: EnumValuePlan) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": value.name,
        "value": value.value,
        "traits": _json_copy(value.traits, f"enum value {value.name}.traits"),
        "extensions": _json_copy(value.extensions, f"enum value {value.name}.extensions"),
    }
    if value.documentation is not None:
        result["documentation"] = value.documentation
    return result


def _service(value: object) -> Service:
    raw = _object(value, "service")
    _fields(
        raw,
        "service",
        required={
            "id",
            "version",
            "protocols",
            "auth_schemes",
            "operations",
            "external_documentation",
            "traits",
            "extensions",
            "policies",
        },
        optional={"documentation", "title"},
    )
    return Service(
        id=_shape_id(_required(raw, "id", "service"), "service.id"),
        version=_string(_required(raw, "version", "service"), "service.version"),
        protocols=_shape_ids(_required(raw, "protocols", "service"), "service.protocols"),
        auth_schemes=_shape_ids(_required(raw, "auth_schemes", "service"), "service.auth_schemes"),
        operations=_shape_ids(_required(raw, "operations", "service"), "service.operations"),
        title=_optional_string(raw, "title", "service"),
        documentation=_optional_string(raw, "documentation", "service"),
        external_documentation=_string_map(
            _required(raw, "external_documentation", "service"),
            "service.external_documentation",
        ),
        traits=_trait_nodes(_required(raw, "traits", "service"), "service.traits"),
        extensions=_trait_nodes(_required(raw, "extensions", "service"), "service.extensions"),
        policies=_json_object(_required(raw, "policies", "service"), "service.policies"),
    )


def _operation(value: object, label: str) -> OperationPlan:
    raw = _object(value, label)
    _fields(
        raw,
        label,
        required={
            "id",
            "input",
            "output",
            "errors",
            "auth_schemes",
            "external_documentation",
            "traits",
            "extensions",
            "policies",
        },
        optional={"documentation", "http", "event_streams"},
    )
    return OperationPlan(
        id=_shape_id(_required(raw, "id", label), f"{label}.id"),
        input=_shape_id(_required(raw, "input", label), f"{label}.input"),
        output=_shape_id(_required(raw, "output", label), f"{label}.output"),
        errors=_shape_ids(_required(raw, "errors", label), f"{label}.errors"),
        auth_schemes=_shape_ids(_required(raw, "auth_schemes", label), f"{label}.auth_schemes"),
        documentation=_optional_string(raw, "documentation", label),
        external_documentation=_string_map(
            _required(raw, "external_documentation", label),
            f"{label}.external_documentation",
        ),
        http=None if "http" not in raw else _http(raw["http"], f"{label}.http"),
        event_streams=(
            None if "event_streams" not in raw else _event_streams(raw["event_streams"], f"{label}.event_streams")
        ),
        traits=_trait_nodes(_required(raw, "traits", label), f"{label}.traits"),
        extensions=_trait_nodes(_required(raw, "extensions", label), f"{label}.extensions"),
        policies=_json_object(_required(raw, "policies", label), f"{label}.policies"),
    )


def _http(value: object, label: str) -> HttpPlan:
    raw = _object(value, label)
    _fields(
        raw,
        label,
        required={"method", "uri", "code", "request_bindings", "response_bindings", "error_bindings"},
    )
    error_raw = _object(_required(raw, "error_bindings", label), f"{label}.error_bindings")
    return HttpPlan(
        method=_string(_required(raw, "method", label), f"{label}.method"),
        uri=_string(_required(raw, "uri", label), f"{label}.uri"),
        code=_integer(_required(raw, "code", label), f"{label}.code"),
        request_bindings=_bindings(_required(raw, "request_bindings", label), f"{label}.request_bindings"),
        response_bindings=_bindings(_required(raw, "response_bindings", label), f"{label}.response_bindings"),
        error_bindings={
            _shape_id(shape_id, f"{label}.error_bindings key"): _http_error(
                error,
                f"{label}.error_bindings[{shape_id!r}]",
            )
            for shape_id, error in error_raw.items()
        },
    )


def _http_error(value: object, label: str) -> HttpErrorPlan:
    raw = _object(value, label)
    _fields(raw, label, required={"code", "bindings"})
    return HttpErrorPlan(
        code=_integer(_required(raw, "code", label), f"{label}.code"),
        bindings=_bindings(_required(raw, "bindings", label), f"{label}.bindings"),
    )


def _bindings(value: object, label: str) -> tuple[HttpBindingPlan, ...]:
    return tuple(_binding(item, f"{label}[{index}]") for index, item in enumerate(_array(value, label)))


def _event_streams(value: object, label: str) -> EventStreamsPlan:
    raw = _object(value, label)
    _fields(raw, label, required=set(), optional={"input", "output"})
    if not raw:
        msg = f"{label} must identify at least one event-stream member"
        raise ServicePlanError(msg)
    return EventStreamsPlan(
        input=_optional_shape_id(raw, "input", label),
        output=_optional_shape_id(raw, "output", label),
    )


def _binding(value: object, label: str) -> HttpBindingPlan:
    raw = _object(value, label)
    _fields(raw, label, required={"member_id", "location", "name", "greedy"})
    location = _string(_required(raw, "location", label), f"{label}.location")
    if location not in HTTP_BINDING_LOCATIONS:
        msg = f"{label}.location must be one of {HTTP_BINDING_LOCATIONS}, got {location!r}"
        raise ServicePlanError(msg)
    greedy = _boolean(_required(raw, "greedy", label), f"{label}.greedy")
    if greedy and location != "label":
        msg = f"{label}.greedy may only be true for an HTTP label binding"
        raise ServicePlanError(msg)
    return HttpBindingPlan(
        member_id=_shape_id(_required(raw, "member_id", label), f"{label}.member_id"),
        location=location,
        name=_string(_required(raw, "name", label), f"{label}.name"),
        greedy=greedy,
    )


def _shape(value: object, label: str) -> ShapePlan:
    raw = _object(value, label)
    _fields(
        raw,
        label,
        required={
            "id",
            "kind",
            "recursive",
            "traits",
            "extensions",
            "constraints",
            "policies",
            "members",
            "enum_values",
        },
        optional={"documentation"},
    )
    kind = _string(_required(raw, "kind", label), f"{label}.kind")
    if kind not in SHAPE_KINDS:
        msg = f"{label}.kind must be an exact Smithy shape kind, got {kind!r}"
        raise ServicePlanError(msg)
    shape = ShapePlan(
        id=_shape_id(_required(raw, "id", label), f"{label}.id"),
        kind=kind,
        recursive=_boolean(_required(raw, "recursive", label), f"{label}.recursive"),
        documentation=_optional_string(raw, "documentation", label),
        traits=_trait_nodes(_required(raw, "traits", label), f"{label}.traits"),
        extensions=_trait_nodes(_required(raw, "extensions", label), f"{label}.extensions"),
        constraints=_trait_nodes(_required(raw, "constraints", label), f"{label}.constraints"),
        policies=_json_object(_required(raw, "policies", label), f"{label}.policies"),
        members=tuple(
            _member(item, f"{label}.members[{index}]")
            for index, item in enumerate(_array(_required(raw, "members", label), f"{label}.members"))
        ),
        enum_values=tuple(
            _enum_value(item, f"{label}.enum_values[{index}]")
            for index, item in enumerate(_array(_required(raw, "enum_values", label), f"{label}.enum_values"))
        ),
    )
    _validate_shape(shape, label)
    return shape


def _member(value: object, label: str) -> MemberPlan:
    raw = _object(value, label)
    _fields(
        raw,
        label,
        required={
            "id",
            "name",
            "target",
            "required",
            "client_nullable",
            "default",
            "traits",
            "extensions",
            "constraints",
            "policies",
        },
        optional={"documentation"},
    )
    return MemberPlan(
        id=_shape_id(_required(raw, "id", label), f"{label}.id"),
        name=_string(_required(raw, "name", label), f"{label}.name"),
        target=_shape_id(_required(raw, "target", label), f"{label}.target"),
        required=_boolean(_required(raw, "required", label), f"{label}.required"),
        client_nullable=_boolean(_required(raw, "client_nullable", label), f"{label}.client_nullable"),
        default=_default(_required(raw, "default", label), f"{label}.default"),
        documentation=_optional_string(raw, "documentation", label),
        traits=_trait_nodes(_required(raw, "traits", label), f"{label}.traits"),
        extensions=_trait_nodes(_required(raw, "extensions", label), f"{label}.extensions"),
        constraints=_trait_nodes(_required(raw, "constraints", label), f"{label}.constraints"),
        policies=_json_object(_required(raw, "policies", label), f"{label}.policies"),
    )


def _default(value: object, label: str) -> DefaultValue:
    raw = _object(value, label)
    present = _boolean(_required(raw, "present", label), f"{label}.present")
    _fields(raw, label, required={"present", "value"} if present else {"present"})
    return DefaultValue(present=present, value=_json_copy(raw["value"], f"{label}.value") if present else None)


def _enum_value(value: object, label: str) -> EnumValuePlan:
    raw = _object(value, label)
    _fields(raw, label, required={"name", "value", "traits", "extensions"}, optional={"documentation"})
    enum_value = _required(raw, "value", label)
    if not isinstance(enum_value, str | int) or isinstance(enum_value, bool):
        msg = f"{label}.value must be a string or integer"
        raise ServicePlanError(msg)
    return EnumValuePlan(
        name=_string(_required(raw, "name", label), f"{label}.name"),
        value=enum_value,
        documentation=_optional_string(raw, "documentation", label),
        traits=_trait_nodes(_required(raw, "traits", label), f"{label}.traits"),
        extensions=_trait_nodes(_required(raw, "extensions", label), f"{label}.extensions"),
    )


def _validate_shape(shape: ShapePlan, label: str) -> None:
    member_count = len(shape.members)
    if shape.kind in {"list", "set"} and member_count != 1:
        msg = f"{label} with kind {shape.kind!r} must contain exactly one member"
        raise ServicePlanError(msg)
    if shape.kind == "map" and (
        member_count != len({"key", "value"}) or {member.name for member in shape.members} != {"key", "value"}
    ):
        msg = f"{label} with kind 'map' must contain key and value members"
        raise ServicePlanError(msg)
    if shape.kind in {"list", "set"} and shape.members[0].name != "member":
        msg = f"{label} with kind {shape.kind!r} must name its member 'member'"
        raise ServicePlanError(msg)
    if shape.kind not in {"enum", "intEnum", "list", "set", "map", "structure", "union"} and shape.members:
        msg = f"{label} with kind {shape.kind!r} cannot contain members"
        raise ServicePlanError(msg)
    if shape.kind not in {"enum", "intEnum"} and shape.enum_values:
        msg = f"{label} with kind {shape.kind!r} cannot contain enum_values"
        raise ServicePlanError(msg)
    if shape.kind == "enum" and any(not isinstance(value.value, str) for value in shape.enum_values):
        msg = f"{label} enum values must be strings"
        raise ServicePlanError(msg)
    if shape.kind == "intEnum" and any(not isinstance(value.value, int) for value in shape.enum_values):
        msg = f"{label} intEnum values must be integers"
        raise ServicePlanError(msg)
    if shape.kind in {"enum", "intEnum"}:
        _validate_enum_members(shape, label)
    names = [member.name for member in shape.members]
    if len(names) != len(set(names)):
        msg = f"{label} contains duplicate member names"
        raise ServicePlanError(msg)


def _validate_enum_members(shape: ShapePlan, label: str) -> None:
    if len(shape.members) != len(shape.enum_values):
        msg = f"{label} enum members and enum_values must have the same length"
        raise ServicePlanError(msg)
    for index, (member, enum_value) in enumerate(zip(shape.members, shape.enum_values, strict=True)):
        if member.name != enum_value.name:
            msg = f"{label} enum member and enum_values names differ at index {index}"
            raise ServicePlanError(msg)
        if member.target != "smithy.api#Unit":
            msg = f"{label} enum member {member.id!r} must target 'smithy.api#Unit'"
            raise ServicePlanError(msg)
        if member.traits.get("smithy.api#enumValue") != enum_value.value:
            msg = f"{label} enum member {member.id!r} does not match its enum_values value"
            raise ServicePlanError(msg)


def _validate_references(plan: ServicePlan) -> None:  # noqa: C901, PLR0912
    missing_operations = set(plan.service.operations).difference(plan.operations)
    if missing_operations:
        msg = f"service references missing operations: {sorted(missing_operations)}"
        raise ServicePlanError(msg)
    unexpected_operations = set(plan.operations).difference(plan.service.operations)
    if unexpected_operations:
        msg = f"service plan contains operations not selected by the service: {sorted(unexpected_operations)}"
        raise ServicePlanError(msg)
    for key, operation in plan.operations.items():
        if key != operation.id:
            msg = f"operation map key {key!r} does not match id {operation.id!r}"
            raise ServicePlanError(msg)
        for direction, shape_id in (("input", operation.input), ("output", operation.output)):
            if shape_id not in plan.shapes:
                msg = f"operation {operation.id!r} references missing {direction} shape {shape_id!r}"
                raise ServicePlanError(msg)
        missing_errors = set(operation.errors).difference(plan.shapes)
        if missing_errors:
            msg = f"operation {operation.id!r} references missing error shapes: {sorted(missing_errors)}"
            raise ServicePlanError(msg)
        input_shape = _operation_structure(plan, operation, operation.input, "input")
        output_shape = _operation_structure(plan, operation, operation.output, "output")
        error_shapes = {
            shape_id: _operation_structure(plan, operation, shape_id, "error") for shape_id in operation.errors
        }
        if operation.http is not None:
            _validate_binding_ownership(
                operation.http.request_bindings,
                input_shape,
                f"operation {operation.id!r} request bindings",
            )
            _validate_binding_ownership(
                operation.http.response_bindings,
                output_shape,
                f"operation {operation.id!r} response bindings",
            )
            error_binding_ids = set(operation.http.error_bindings)
            if error_binding_ids != set(operation.errors):
                msg = (
                    f"operation {operation.id!r} HTTP error binding IDs must exactly match modeled errors; "
                    f"got {sorted(error_binding_ids)}, expected {sorted(operation.errors)}"
                )
                raise ServicePlanError(msg)
            for error_id, error in operation.http.error_bindings.items():
                _validate_binding_ownership(
                    error.bindings,
                    error_shapes[error_id],
                    f"operation {operation.id!r} error bindings for {error_id!r}",
                )
        if operation.event_streams is not None:
            for direction, member_id, owner in (
                ("input", operation.event_streams.input, input_shape),
                ("output", operation.event_streams.output, output_shape),
            ):
                if member_id is not None:
                    _validate_member_ownership(
                        member_id,
                        owner,
                        f"operation {operation.id!r} {direction} event stream",
                    )
    for key, shape in plan.shapes.items():
        if key != shape.id:
            msg = f"shape map key {key!r} does not match id {shape.id!r}"
            raise ServicePlanError(msg)
        for member in shape.members:
            expected_id = f"{shape.id}${member.name}"
            if member.id != expected_id:
                msg = f"member id {member.id!r} does not match its owner and name {expected_id!r}"
                raise ServicePlanError(msg)
            if member.target not in plan.shapes and member.target not in _IMPLICIT_PRELUDE_TARGETS:
                msg = f"member {member.id!r} references missing target shape {member.target!r}"
                raise ServicePlanError(msg)


def _operation_structure(
    plan: ServicePlan,
    operation: OperationPlan,
    shape_id: str,
    role: str,
) -> ShapePlan:
    shape = plan.shapes[shape_id]
    if shape.kind != "structure":
        msg = f"operation {operation.id!r} {role} shape {shape_id!r} must be a structure"
        raise ServicePlanError(msg)
    return shape


def _validate_binding_ownership(
    bindings: tuple[HttpBindingPlan, ...],
    owner: ShapePlan,
    label: str,
) -> None:
    member_ids = [binding.member_id for binding in bindings]
    if len(member_ids) != len(set(member_ids)):
        msg = f"{label} contain duplicate member bindings"
        raise ServicePlanError(msg)
    for member_id in member_ids:
        _validate_member_ownership(member_id, owner, label)


def _validate_member_ownership(member_id: str, owner: ShapePlan, label: str) -> None:
    if member_id not in {member.id for member in owner.members}:
        msg = f"{label} reference member {member_id!r} outside {owner.id!r}"
        raise ServicePlanError(msg)


def _entity_map[T](value: object, label: str, parser: Callable[[object, str], T]) -> dict[str, T]:
    raw = _object(value, label)
    return {
        _shape_id(shape_id, f"{label} key"): parser(item, f"{label}[{shape_id!r}]") for shape_id, item in raw.items()
    }


def _trait_nodes(value: object, label: str) -> TraitNodes:
    raw = _object(value, label)
    return {
        _shape_id(shape_id, f"{label} key"): _json_copy(node, f"{label}[{shape_id!r}]")
        for shape_id, node in raw.items()
    }


def _json_object(value: object, label: str) -> TraitNodes:
    raw = _object(value, label)
    return {key: _json_copy(node, f"{label}.{key}") for key, node in raw.items()}


def _string_map(value: object, label: str) -> dict[str, str]:
    raw = _object(value, label)
    return {key: _string(item, f"{label}.{key}") for key, item in raw.items()}


def _fields(raw: dict[str, object], label: str, *, required: set[str], optional: set[str] | None = None) -> None:
    optional = optional or set()
    missing = required.difference(raw)
    if missing:
        msg = f"{label} is missing required fields: {sorted(missing)}"
        raise ServicePlanError(msg)
    unknown = set(raw).difference(required | optional)
    if unknown:
        msg = f"{label} contains unknown fields: {sorted(unknown)}"
        raise ServicePlanError(msg)


def _required(raw: dict[str, object], name: str, label: str) -> object:
    try:
        return raw[name]
    except KeyError:
        msg = f"{label} is missing required field {name!r}"
        raise ServicePlanError(msg) from None


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be an object"
        raise ServicePlanError(msg)
    raw = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in raw):
        msg = f"{label} must use string keys"
        raise ServicePlanError(msg)
    return cast("dict[str, object]", raw)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{label} must be an array"
        raise ServicePlanError(msg)
    return cast("list[object]", value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        msg = f"{label} must be a string"
        raise ServicePlanError(msg)
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{label} must be a boolean"
        raise ServicePlanError(msg)
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{label} must be an integer"
        raise ServicePlanError(msg)
    return value


def _shape_id(value: object, label: str) -> str:
    shape_id = _string(value, label)
    if not _SHAPE_ID.fullmatch(shape_id):
        msg = f"{label} must be a Smithy ShapeId, got {shape_id!r}"
        raise ServicePlanError(msg)
    return shape_id


def _shape_ids(value: object, label: str) -> tuple[str, ...]:
    result = tuple(_shape_id(item, f"{label}[{index}]") for index, item in enumerate(_array(value, label)))
    duplicates = sorted({shape_id for shape_id in result if result.count(shape_id) > 1})
    if duplicates:
        msg = f"{label} contains duplicate ShapeIds: {duplicates}"
        raise ServicePlanError(msg)
    return result


def _optional_shape_id(raw: dict[str, object], name: str, label: str) -> str | None:
    return None if name not in raw else _shape_id(raw[name], f"{label}.{name}")


def _optional_string(raw: dict[str, object], name: str, label: str) -> str | None:
    return None if name not in raw else _string(raw[name], f"{label}.{name}")


def _json_copy(value: object, label: str) -> JSONValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"{label} must contain only finite JSON numbers"
            raise ServicePlanError(msg)
        return value
    if isinstance(value, list):
        return [_json_copy(item, f"{label}[]") for item in cast("list[object]", value)]
    if isinstance(value, dict):
        raw = _object(cast("object", value), label)
        return {key: _json_copy(item, f"{label}.{key}") for key, item in raw.items()}
    msg = f"{label} must be a JSON value"
    raise ServicePlanError(msg)
