"""Read Spitzeisen's custom traits from an assembled Smithy JSON model."""

from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from spitzeisen.codegen.ir import JSONValue
    from spitzeisen.params import QueryStyle

SDK_OPERATION = "spitzeisen.api#sdkOperation"
PAGE_NUMBER_PAGINATION = "spitzeisen.api#pageNumberPagination"
SORTING = "spitzeisen.api#sorting"
PYTHON_PARAMETER = "spitzeisen.api#pythonParameter"
HIDDEN = "spitzeisen.api#hidden"
MODEL_PROPERTY = "spitzeisen.api#modelProperty"
SMITHY_PAGINATED = "smithy.api#paginated"
JSON_NAME = "smithy.api#jsonName"
EXTERNAL_DOCUMENTATION = "smithy.api#externalDocumentation"

type Shape = Literal["collection", "single"]
type NotFound = Literal["raise", "empty"]
type PaginationStyle = Literal["none", "page_number"]
type ActiveSortStyle = Literal["suffix", "param"]
type CoercionStyle = Literal["plain", "date", "comma_list", "comma_choice_list", "choice"]

_COERCION_STYLES = frozenset({"plain", "date", "comma_list", "comma_choice_list", "choice"})
_QUERY_STYLES = frozenset({"form", "spaceDelimited", "pipeDelimited"})


@dataclass(frozen=True, slots=True)
class ParamPolicy:
    """Python presentation attached to one Smithy input member."""

    name: str | None = None
    style: QueryStyle | None = None
    explode: bool | None = None
    coercion_style: CoercionStyle = "plain"
    coerce_function: str | None = None
    literal: str | None = None
    annotation: str | None = None
    client_default: str | None = None
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    """Optional SDK presentation attached to a Smithy operation."""

    key: str | None = None
    method_name: str | None = None
    model: str | None = None
    docs_url: str | None = None
    generate_model: bool = True
    shape: Shape | None = None
    not_found: NotFound = "raise"
    cost: float = 1.0
    results_key: str | None = None
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class PageNumberPolicy:
    """Page-number pagination attached to a Smithy operation."""

    page_param: str = "page"
    page_size_param: str | None = None
    results_key: str | None = None
    max_page_size: int | None = None
    start: int = 1
    step: int = 1


@dataclass(frozen=True, slots=True)
class SortingPolicy:
    """Sorting behavior attached to a Smithy operation."""

    style: ActiveSortStyle
    sort_param: str
    order_param: str | None = None
    sort_literal: str | None = None
    order_literal: str | None = None
    sort_default: JSONValue = None
    order_default: JSONValue = None
    has_sort_default: bool = False
    has_order_default: bool = False


@dataclass(frozen=True, slots=True)
class ModelCustomizations:
    """Pydantic backend settings derived from response-member traits."""

    aliases: dict[str, str]
    type_overrides: dict[str, str]


def operation_policy(traits: dict[str, Any]) -> OperationPolicy:
    """Parse the optional @sdkOperation trait."""
    raw = _structured_trait(traits, SDK_OPERATION)
    cost = _number(raw, "cost", 1.0)
    if cost <= 0:
        msg = "@sdkOperation cost must be greater than zero"
        raise TypeError(msg)
    shape = _optional_choice(raw, "shape", {"collection", "single"})
    not_found = _choice(raw, "notFound", {"raise", "empty"}, "raise")
    return OperationPolicy(
        key=_optional_string(raw, "name"),
        method_name=_optional_string(raw, "methodName"),
        model=_optional_string(raw, "responseModel"),
        docs_url=_optional_string(raw, "documentationUrl"),
        generate_model=_boolean(raw, "generateModel", default=True),
        shape=cast("Shape | None", shape),
        not_found=cast("NotFound", not_found),
        cost=cost,
        results_key=_optional_string(raw, "resultPath"),
        hidden=HIDDEN in traits,
    )


def page_number_policy(traits: dict[str, Any]) -> PageNumberPolicy | None:
    """Parse @pageNumberPagination when present."""
    if PAGE_NUMBER_PAGINATION not in traits:
        return None
    raw = _structured_trait(traits, PAGE_NUMBER_PAGINATION)
    maximum = _optional_integer(raw, "maxPageSize")
    start = _integer(raw, "start", 1)
    step = _integer(raw, "step", 1)
    if maximum is not None and maximum < 1:
        msg = "@pageNumberPagination maxPageSize must be at least one"
        raise ValueError(msg)
    if maximum is not None and "pageSize" not in raw:
        msg = "@pageNumberPagination maxPageSize requires pageSize"
        raise ValueError(msg)
    if step < 1:
        msg = "@pageNumberPagination step must be at least one"
        raise ValueError(msg)
    return PageNumberPolicy(
        page_param=_string(raw, "page", "page"),
        page_size_param=_optional_string(raw, "pageSize"),
        results_key=_optional_string(raw, "items"),
        max_page_size=maximum,
        start=start,
        step=step,
    )


def sorting_policy(traits: dict[str, Any]) -> SortingPolicy | None:
    """Parse @sorting when present."""
    if SORTING not in traits:
        return None
    raw = _structured_trait(traits, SORTING)
    style = _choice(raw, "style", {"suffix", "param"})
    sort_param = _string(raw, "sort")
    order_param = _optional_string(raw, "order")
    if style == "suffix" and order_param is not None:
        msg = "@sorting style 'suffix' cannot declare order"
        raise ValueError(msg)
    if style == "param" and order_param is None:
        msg = "@sorting style 'param' requires order"
        raise ValueError(msg)
    if sort_param == order_param:
        msg = "@sorting sort and order must name different members"
        raise ValueError(msg)
    return SortingPolicy(
        style=cast("ActiveSortStyle", style),
        sort_param=sort_param,
        order_param=order_param,
        sort_literal=_optional_identifier(raw, "sortLiteral"),
        order_literal=_optional_identifier(raw, "orderLiteral"),
        sort_default=cast("JSONValue", raw.get("sortDefault")),
        order_default=cast("JSONValue", raw.get("orderDefault")),
        has_sort_default="sortDefault" in raw,
        has_order_default="orderDefault" in raw,
    )


def param_policy(traits: dict[str, Any]) -> ParamPolicy:
    """Parse @pythonParameter and @hidden from one input member."""
    raw = _structured_trait(traits, PYTHON_PARAMETER)
    coercion = _choice(raw, "coercion", _COERCION_STYLES, "plain")
    literal = _optional_identifier(raw, "literal")
    function = _optional_identifier(raw, "function")
    annotation = _optional_string(raw, "annotation")
    if annotation is not None and not _is_annotation(annotation):
        msg = "@pythonParameter annotation must be a Python annotation"
        raise ValueError(msg)
    if coercion in {"choice", "comma_choice_list"} and literal is None:
        msg = f"@pythonParameter coercion={coercion!r} requires literal"
        raise ValueError(msg)
    if function is not None and coercion != "plain":
        msg = "@pythonParameter function cannot be combined with a non-plain coercion"
        raise ValueError(msg)
    style = _optional_choice(raw, "style", _QUERY_STYLES)
    return ParamPolicy(
        name=_optional_string(raw, "name"),
        style=cast("QueryStyle | None", style),
        explode=_optional_boolean(raw, "explode"),
        coercion_style=cast("CoercionStyle", coercion),
        coerce_function=function,
        literal=literal,
        annotation=annotation,
        client_default=repr(raw["clientDefault"]) if "clientDefault" in raw else None,
        hidden=HIDDEN in traits,
    )


def model_customizations(model: dict[str, Any]) -> ModelCustomizations:  # noqa: C901, PLR0912
    """Collect @modelProperty traits into datamodel-code-generator mappings."""
    aliases: dict[str, str] = {}
    type_overrides: dict[str, str] = {}
    raw_shapes = model.get("shapes", {})
    if not isinstance(raw_shapes, dict):
        return ModelCustomizations(aliases, type_overrides)
    for shape_id, raw_shape in cast("dict[str, object]", raw_shapes).items():
        if not isinstance(raw_shape, dict):
            continue
        shape = cast("dict[str, object]", raw_shape)
        if shape.get("type") != "structure":
            continue
        members = shape.get("members", {})
        if not isinstance(members, dict):
            continue
        shape_name = shape_id.rsplit("#", maxsplit=1)[-1]
        for member_name, raw_member in cast("dict[str, object]", members).items():
            if not isinstance(raw_member, dict):
                continue
            member = cast("dict[str, object]", raw_member)
            raw_traits = member.get("traits", {})
            if not isinstance(raw_traits, dict):
                continue
            traits = cast("dict[str, object]", raw_traits)
            if MODEL_PROPERTY not in traits:
                continue
            trait = traits[MODEL_PROPERTY]
            if not isinstance(trait, dict):
                msg = f"@modelProperty on {shape_id}${member_name} must be an object"
                raise TypeError(msg)
            values = cast("dict[str, object]", trait)
            wire_name = traits.get(JSON_NAME, member_name)
            if not isinstance(wire_name, str):
                wire_name = member_name
            key = f"{shape_name}.{wire_name}"
            if "name" in values:
                aliases[key] = _string(values, "name")
            if "type" in values:
                type_overrides[key] = _string(values, "type")
    return ModelCustomizations(aliases, type_overrides)


def _structured_trait(traits: dict[str, Any], name: str) -> dict[str, object]:
    value = traits.get(name, {})
    if not isinstance(value, dict):
        msg = f"@{name.rsplit('#', maxsplit=1)[-1]} must contain an object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _string(values: dict[str, object], key: str, default: str | None = None) -> str:
    value = values.get(key, default)
    if not isinstance(value, str) or not value:
        msg = f"trait member {key!r} must be a non-empty string"
        raise TypeError(msg)
    return value


def _optional_string(values: dict[str, object], key: str) -> str | None:
    return _string(values, key) if key in values else None


def _optional_identifier(values: dict[str, object], key: str) -> str | None:
    value = _optional_string(values, key)
    if value is not None and (not value.isidentifier() or keyword.iskeyword(value)):
        msg = f"trait member {key!r} must be a valid Python identifier"
        raise ValueError(msg)
    return value


def _boolean(values: dict[str, object], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        msg = f"trait member {key!r} must be a boolean"
        raise TypeError(msg)
    return value


def _optional_boolean(values: dict[str, object], key: str) -> bool | None:
    return _boolean(values, key, default=False) if key in values else None


def _number(values: dict[str, object], key: str, default: float) -> float:
    value = values.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        msg = f"trait member {key!r} must be a number"
        raise TypeError(msg)
    return float(value)


def _integer(values: dict[str, object], key: str, default: int) -> int:
    value = values.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"trait member {key!r} must be an integer"
        raise TypeError(msg)
    return value


def _optional_integer(values: dict[str, object], key: str) -> int | None:
    return _integer(values, key, 0) if key in values else None


def _choice(values: dict[str, object], key: str, allowed: frozenset[str] | set[str], default: str | None = None) -> str:
    value = _string(values, key, default)
    if value not in allowed:
        msg = f"trait member {key!r} must be one of {sorted(allowed)}, got {value!r}"
        raise ValueError(msg)
    return value


def _optional_choice(values: dict[str, object], key: str, allowed: frozenset[str] | set[str]) -> str | None:
    return _choice(values, key, allowed) if key in values else None


def _is_annotation(source: str) -> bool:
    try:
        expression = ast.parse(source, mode="eval")
    except SyntaxError:
        return False
    allowed = (
        ast.Expression,
        ast.Name,
        ast.Load,
        ast.Attribute,
        ast.Subscript,
        ast.BinOp,
        ast.BitOr,
        ast.Constant,
        ast.Tuple,
        ast.List,
        ast.UnaryOp,
        ast.USub,
    )
    return all(isinstance(node, allowed) for node in ast.walk(expression))
