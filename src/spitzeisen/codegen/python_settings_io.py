"""Strict, format-agnostic decoding for Python target settings."""

from __future__ import annotations

import keyword
import math
import re
from typing import cast

from spitzeisen.codegen.python_context import (
    PythonExternalModel,
    PythonInputAdapter,
    PythonSettings,
)
from spitzeisen.codegen.python_plan import JSONScalar, PythonImport, PythonTypeKind, PythonTypePlan

_SHAPE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*#[A-Za-z_][A-Za-z0-9_]*$")
_TYPE_KINDS: tuple[PythonTypeKind, ...] = (
    "bool",
    "bytes",
    "date_input",
    "datetime",
    "decimal",
    "dict",
    "document",
    "float",
    "int",
    "list",
    "literal",
    "set",
    "str",
    "symbol",
    "union",
)
_PRIMITIVE_TYPE_KINDS = frozenset(
    {
        "bool",
        "bytes",
        "date_input",
        "datetime",
        "decimal",
        "document",
        "float",
        "int",
        "str",
    }
)
_COMPOSITE_MIN_MEMBERS = 2


class PythonSettingsDocumentError(ValueError):
    """A Python target-settings document violates its structural contract."""


def python_settings_from_document(
    document: object,
    *,
    package: str,
    client_name: str,
    vendor: str | None = None,
) -> PythonSettings:
    """Decode target-only settings while keeping artifact identity owned by the caller."""
    raw = _object(document, "Python target settings")
    _fields(
        raw,
        "Python target settings",
        required=set(),
        optional={
            "external_models",
            "input_adapters",
            "protocol_preference",
            "enabled_integrations",
        },
    )
    return PythonSettings(
        package=package,
        client_name=client_name,
        vendor=vendor,
        external_models=_external_models(raw.get("external_models", {})),
        input_adapters=_input_adapters(raw.get("input_adapters", {})),
        protocol_preference=_shape_ids(raw.get("protocol_preference", []), "protocol_preference"),
        enabled_integrations=_unique_strings(
            raw.get("enabled_integrations", []),
            "enabled_integrations",
        ),
    )


def _external_models(value: object) -> dict[str, PythonExternalModel]:
    raw = _object(value, "external_models")
    result: dict[str, PythonExternalModel] = {}
    for configured_shape_id, configured_model in raw.items():
        shape_id = _shape_id(configured_shape_id, "external_models key")
        label = f"external_models[{shape_id!r}]"
        model = _object(configured_model, label)
        _fields(model, label, required={"module", "symbol"}, optional={"dependencies"})
        result[shape_id] = PythonExternalModel(
            module=_module(_required(model, "module", label), f"{label}.module"),
            symbol=_identifier(_required(model, "symbol", label), f"{label}.symbol"),
            dependencies=_dependencies(model.get("dependencies", []), f"{label}.dependencies"),
        )
    return result


def _input_adapters(value: object) -> dict[str, PythonInputAdapter]:
    raw = _object(value, "input_adapters")
    result: dict[str, PythonInputAdapter] = {}
    for adapter_id, configured_adapter in raw.items():
        _nonempty_string(adapter_id, "input_adapters key")
        label = f"input_adapters[{adapter_id!r}]"
        adapter = _object(configured_adapter, label)
        _fields(
            adapter,
            label,
            required={"function", "public_type"},
            optional={"dependencies"},
        )
        result[adapter_id] = PythonInputAdapter(
            function=_python_import(_required(adapter, "function", label), f"{label}.function"),
            public_type=_python_type(_required(adapter, "public_type", label), f"{label}.public_type"),
            dependencies=_dependencies(adapter.get("dependencies", []), f"{label}.dependencies"),
        )
    return result


def _python_import(value: object, label: str) -> PythonImport:
    raw = _object(value, label)
    _fields(raw, label, required={"module", "name"}, optional={"alias"})
    return PythonImport(
        module=_module(_required(raw, "module", label), f"{label}.module"),
        name=_identifier(_required(raw, "name", label), f"{label}.name"),
        alias=None if "alias" not in raw else _identifier(raw["alias"], f"{label}.alias"),
    )


def _python_type(value: object, label: str) -> PythonTypePlan:
    raw = _object(value, label)
    _fields(
        raw,
        label,
        required={"kind"},
        optional={"name", "module", "values", "members"},
    )
    kind = cast(
        "PythonTypeKind",
        _choice(_required(raw, "kind", label), _TYPE_KINDS, f"{label}.kind"),
    )
    if kind in _PRIMITIVE_TYPE_KINDS:
        _fields(raw, label, required={"kind"})
        return PythonTypePlan(kind)
    if kind == "symbol":
        _fields(raw, label, required={"kind", "name", "module"})
        return PythonTypePlan(
            kind,
            name=_identifier(_required(raw, "name", label), f"{label}.name"),
            module=_module(_required(raw, "module", label), f"{label}.module"),
        )
    if kind == "literal":
        _fields(raw, label, required={"kind", "values"})
        values = _literal_values(_required(raw, "values", label), f"{label}.values")
        if not values:
            msg = f"{label}.values must contain at least one literal"
            raise PythonSettingsDocumentError(msg)
        return PythonTypePlan(kind, values=values)

    _fields(raw, label, required={"kind", "members"})
    members = tuple(
        _python_type(member, f"{label}.members[{index}]")
        for index, member in enumerate(_array(_required(raw, "members", label), f"{label}.members"))
    )
    required_members = _COMPOSITE_MIN_MEMBERS if kind in {"dict", "union"} else 1
    valid_count = len(members) >= _COMPOSITE_MIN_MEMBERS if kind == "union" else len(members) == required_members
    if not valid_count:
        expectation = "at least two" if kind == "union" else str(required_members)
        msg = f"{label}.members for {kind!r} must contain {expectation} type descriptor(s)"
        raise PythonSettingsDocumentError(msg)
    return PythonTypePlan(kind, members=members)


def _literal_values(value: object, label: str) -> tuple[JSONScalar, ...]:
    result: list[JSONScalar] = []
    for index, item in enumerate(_array(value, label)):
        item_label = f"{label}[{index}]"
        if item is None or isinstance(item, str | bool | int) or (isinstance(item, float) and math.isfinite(item)):
            result.append(item)
        else:
            msg = f"{item_label} must be a finite JSON scalar"
            raise PythonSettingsDocumentError(msg)
    return tuple(result)


def _dependencies(value: object, label: str) -> tuple[str, ...]:
    return _unique_strings(value, label, reject_empty=True)


def _shape_ids(value: object, label: str) -> tuple[str, ...]:
    values = tuple(_shape_id(item, f"{label}[{index}]") for index, item in enumerate(_array(value, label)))
    _reject_duplicates(values, label)
    return values


def _unique_strings(value: object, label: str, *, reject_empty: bool = True) -> tuple[str, ...]:
    values = tuple(
        _nonempty_string(item, f"{label}[{index}]") if reject_empty else _string(item, f"{label}[{index}]")
        for index, item in enumerate(_array(value, label))
    )
    _reject_duplicates(values, label)
    return values


def _reject_duplicates(values: tuple[str, ...], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        msg = f"{label} contains duplicate values: {duplicates}"
        raise PythonSettingsDocumentError(msg)


def _fields(
    raw: dict[str, object],
    label: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required.difference(raw)
    if missing:
        msg = f"{label} is missing required fields: {sorted(missing)}"
        raise PythonSettingsDocumentError(msg)
    unknown = set(raw).difference(required | optional)
    if unknown:
        msg = f"{label} contains unknown fields: {sorted(unknown)}"
        raise PythonSettingsDocumentError(msg)


def _required(raw: dict[str, object], name: str, label: str) -> object:
    try:
        return raw[name]
    except KeyError:
        msg = f"{label} is missing required field {name!r}"
        raise PythonSettingsDocumentError(msg) from None


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be an object"
        raise PythonSettingsDocumentError(msg)
    raw = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in raw):
        msg = f"{label} must use string keys"
        raise PythonSettingsDocumentError(msg)
    return cast("dict[str, object]", raw)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{label} must be an array"
        raise PythonSettingsDocumentError(msg)
    return cast("list[object]", value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        msg = f"{label} must be a string"
        raise PythonSettingsDocumentError(msg)
    return value


def _nonempty_string(value: object, label: str) -> str:
    result = _string(value, label)
    if not result or result != result.strip():
        msg = f"{label} must be a non-empty string without surrounding whitespace"
        raise PythonSettingsDocumentError(msg)
    return result


def _identifier(value: object, label: str) -> str:
    result = _nonempty_string(value, label)
    if not result.isidentifier() or keyword.iskeyword(result):
        msg = f"{label} must be a valid non-keyword Python identifier, got {result!r}"
        raise PythonSettingsDocumentError(msg)
    return result


def _module(value: object, label: str) -> str:
    result = _nonempty_string(value, label)
    if any(not segment.isidentifier() or keyword.iskeyword(segment) for segment in result.split(".")):
        msg = f"{label} must be a dotted Python module name, got {result!r}"
        raise PythonSettingsDocumentError(msg)
    return result


def _shape_id(value: object, label: str) -> str:
    result = _nonempty_string(value, label)
    if not _SHAPE_ID.fullmatch(result):
        msg = f"{label} must be a Smithy ShapeId, got {result!r}"
        raise PythonSettingsDocumentError(msg)
    return result


def _choice[T](value: object, choices: tuple[T, ...], label: str) -> T:
    if value not in choices:
        msg = f"{label} must be one of {choices}, got {value!r}"
        raise PythonSettingsDocumentError(msg)
    return cast("T", value)
