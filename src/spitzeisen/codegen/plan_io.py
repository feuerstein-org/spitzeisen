"""Serialize the versioned boundary between Smithy's frontend and Python rendering."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast

from spitzeisen.codegen.policy import (
    ClientPlan,
    OperationPlan,
    PageSizePlan,
    ParamPlan,
    SortArgumentPlan,
    SortingPlan,
)

PLAN_SCHEMA_VERSION = 1


def client_plan_document(client: ClientPlan) -> dict[str, Any]:
    """Return the JSON-compatible, versioned representation of a renderer plan."""
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "client": json.loads(json.dumps(asdict(client))),
    }


def client_plan_from_document(document: dict[str, Any]) -> ClientPlan:
    """Load a renderer plan emitted by the Smithy frontend."""
    version = document.get("schema_version")
    if version != PLAN_SCHEMA_VERSION:
        msg = f"unsupported client-plan schema version {version!r}; expected {PLAN_SCHEMA_VERSION}"
        raise ValueError(msg)
    raw_client = _mapping(document.get("client"), "client")
    return ClientPlan(
        service_id=_string(raw_client, "service_id"),
        vendor=_string(raw_client, "vendor"),
        package=_string(raw_client, "package"),
        client_name=_string(raw_client, "client_name"),
        operations=tuple(_operation(item) for item in _mappings(raw_client, "operations")),
        response_shapes=tuple(_strings(raw_client, "response_shapes")),
        model_aliases=_string_mapping(raw_client, "model_aliases"),
        model_type_overrides=_string_mapping(raw_client, "model_type_overrides"),
    )


def _operation(raw: dict[str, Any]) -> OperationPlan:
    return OperationPlan(
        key=_string(raw, "key"),
        path=_string(raw, "path"),
        method_name=_string(raw, "method_name"),
        model=_string(raw, "model"),
        summary=_string(raw, "summary"),
        docs_url=_optional_string(raw, "docs_url"),
        generate_model=_boolean(raw, "generate_model"),
        shape=cast("Any", _string(raw, "shape")),
        not_found=cast("Any", _string(raw, "not_found")),
        cost=_number(raw, "cost"),
        pagination=cast("Any", _string(raw, "pagination")),
        results_key=_optional_string(raw, "results_key"),
        page_param=_optional_string(raw, "page_param"),
        page_start=_integer(raw, "page_start"),
        page_step=_integer(raw, "page_step"),
        params=tuple(_param(item) for item in _mappings(raw, "params")),
        query_params=tuple(_param(item) for item in _mappings(raw, "query_params")),
        header_params=tuple(_param(item) for item in _mappings(raw, "header_params")),
        path_params=tuple(_param(item) for item in _mappings(raw, "path_params")),
        sorting=_sorting(raw.get("sorting")),
        page_size=_page_size(raw.get("page_size")),
        example_args=tuple(_strings(raw, "example_args")),
        model_imports=tuple(_strings(raw, "model_imports")),
        coerce_function_imports=tuple(_strings(raw, "coerce_function_imports")),
        helpers=tuple(_strings(raw, "helpers")),
    )


def _param(raw: dict[str, Any]) -> ParamPlan:
    return ParamPlan(
        name=_string(raw, "name"),
        wire_name=_string(raw, "wire_name"),
        annotation=_string(raw, "annotation"),
        description=_string(raw, "description"),
        coercion=_string(raw, "coercion"),
        location=cast("Any", _string(raw, "location")),
        style=cast("Any", _string(raw, "style")),
        explode=_boolean(raw, "explode"),
        required=_boolean(raw, "required"),
        client_default=_optional_string(raw, "client_default"),
    )


def _sorting(value: object) -> SortingPlan | None:
    if value is None:
        return None
    raw = _mapping(value, "sorting")
    return SortingPlan(
        style=cast("Any", _string(raw, "style")),
        sort=_sort_argument(_mapping(raw.get("sort"), "sorting.sort")),
        order=_sort_argument(_mapping(raw.get("order"), "sorting.order")),
    )


def _sort_argument(raw: dict[str, Any]) -> SortArgumentPlan:
    return SortArgumentPlan(
        wire_name=_optional_string(raw, "wire_name"),
        annotation=_string(raw, "annotation"),
        default=cast("Any", raw.get("default")),
        coercion=_string(raw, "coercion"),
        style=cast("Any", _string(raw, "style")),
        explode=_boolean(raw, "explode"),
    )


def _page_size(value: object) -> PageSizePlan | None:
    if value is None:
        return None
    raw = _mapping(value, "page_size")
    return PageSizePlan(wire_name=_string(raw, "wire_name"), maximum=_integer(raw, "maximum"))


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"client-plan member {name!r} must be an object"
        raise TypeError(msg)
    return cast("dict[str, Any]", value)


def _mappings(values: dict[str, Any], name: str) -> list[dict[str, Any]]:
    raw = values.get(name)
    if not isinstance(raw, list):
        msg = f"client-plan member {name!r} must be an array"
        raise TypeError(msg)
    return [_mapping(item, name) for item in cast("list[object]", raw)]


def _string(values: dict[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        msg = f"client-plan member {name!r} must be a string"
        raise TypeError(msg)
    return value


def _optional_string(values: dict[str, Any], name: str) -> str | None:
    value = values.get(name)
    if value is not None and not isinstance(value, str):
        msg = f"client-plan member {name!r} must be a string or null"
        raise TypeError(msg)
    return value


def _strings(values: dict[str, Any], name: str) -> list[str]:
    raw = values.get(name)
    if not isinstance(raw, list):
        msg = f"client-plan member {name!r} must be an array of strings"
        raise TypeError(msg)
    items = cast("list[object]", raw)
    if not all(isinstance(item, str) for item in items):
        msg = f"client-plan member {name!r} must be an array of strings"
        raise TypeError(msg)
    return cast("list[str]", items)


def _string_mapping(values: dict[str, Any], name: str) -> dict[str, str]:
    raw = _mapping(values.get(name), name)
    if not all(isinstance(value, str) for value in raw.values()):
        msg = f"client-plan member {name!r} must map strings to strings"
        raise TypeError(msg)
    return cast("dict[str, str]", raw)


def _boolean(values: dict[str, Any], name: str) -> bool:
    value = values.get(name)
    if not isinstance(value, bool):
        msg = f"client-plan member {name!r} must be a boolean"
        raise TypeError(msg)
    return value


def _number(values: dict[str, Any], name: str) -> float:
    value = values.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        msg = f"client-plan member {name!r} must be a number"
        raise TypeError(msg)
    return float(value)


def _integer(values: dict[str, Any], name: str) -> int:
    value = values.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"client-plan member {name!r} must be an integer"
        raise TypeError(msg)
    return value
