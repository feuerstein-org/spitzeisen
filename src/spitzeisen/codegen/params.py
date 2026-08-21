"""
Resolving a generated method's signature.

Parameters come from the OpenAPI document when there is one and from the manifest when there
is not, and either way the manifest gets the last word: it decides what a parameter is called
in Python, what type it accepts, and which coercion runs on the way to the wire. Generated
signatures can therefore use Python-friendly names without exposing vendor-specific wire names.
"""

import re
import string
from dataclasses import dataclass
from typing import Any, Literal, cast

from spitzeisen.codegen.manifest import Endpoint, Param, QueryStyle

# Handled by the template rather than the signature: pagination is the core's business.
STRUCTURAL = {"cursor", "page", "offset"}

SCHEMA_TYPES = {"integer": "int", "number": "float", "boolean": "bool", "string": "str"}
ParameterLocation = Literal["query", "header"]
_OPENAPI_PARAMETER_LOCATIONS = {"query", "header", "path"}


@dataclass(frozen=True, slots=True)
class ResolvedParam:
    """One argument of a generated method."""

    name: str
    wire_name: str
    type: str
    description: str
    coercion: str
    location: ParameterLocation = "query"
    style: QueryStyle = "form"
    explode: bool = True
    required: bool = False
    default: str = "None"


@dataclass(frozen=True, slots=True)
class ResolvedPageSize:
    """The query parameter controlling page size and the largest accepted value."""

    wire_name: str
    maximum: int


@dataclass(frozen=True, slots=True)
class ResolvedSortArgument:
    """One synthesized sorting argument and how it reaches the vendor query string."""

    wire_name: str | None
    type: str
    default: Any
    coercion: str
    style: QueryStyle = "form"
    explode: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedSorting:
    """The two public sorting arguments and the vendor's representation style."""

    style: Literal["suffix", "param"]
    sort: ResolvedSortArgument
    order: ResolvedSortArgument


def literal_of(values: list[Any]) -> str:
    """Render an enum as a `Literal[...]` annotation."""
    return "Literal[" + ", ".join(repr(value) for value in values) + "]"


def python_name(wire_name: str) -> str:
    """`execution_date.gte` -> `execution_date_gte`."""
    return wire_name.replace(".", "_")


def tidy(text: str) -> str:
    """Collapse a vendor description onto one line."""
    return re.sub(r"\s+", " ", text or "").strip()


def _resolve_parameter_reference(
    parameter: dict[str, Any],
    spec: dict[str, Any],
    *,
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Resolve a local OpenAPI Parameter Object reference without mutating the document."""
    reference = parameter.get("$ref")
    if reference is None:
        return parameter
    if not isinstance(reference, str) or not reference.startswith("#/"):
        msg = f"parameter reference {reference!r} must be a local JSON Pointer"
        raise ValueError(msg)
    if reference in seen:
        msg = f"circular OpenAPI parameter reference {reference!r}"
        raise ValueError(msg)

    target = spec
    for token in reference.removeprefix("#/").split("/"):
        target_value = target.get(token.replace("~1", "/").replace("~0", "~"))
        if target_value is None:
            msg = f"parameter reference {reference!r} was not found"
            raise ValueError(msg)
        if not isinstance(target_value, dict):
            msg = f"parameter reference {reference!r} does not resolve to an object"
            raise TypeError(msg)
        target = cast("dict[str, Any]", target_value)
    return _resolve_parameter_reference(target, spec, seen=seen | {reference})


def effective_parameters(
    path_item: dict[str, Any],
    operation: dict[str, Any],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge, dereference and validate the parameters that apply to one operation."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for source in (path_item, operation):
        raw_parameters = source.get("parameters", [])
        if not isinstance(raw_parameters, list):
            msg = f"OpenAPI parameters must be a list, got {type(raw_parameters).__name__}"
            raise TypeError(msg)
        parameters = cast("list[Any]", raw_parameters)
        for parameter in parameters:
            if not isinstance(parameter, dict):
                msg = f"OpenAPI parameter must be an object, got {type(parameter).__name__}"
                raise TypeError(msg)
            resolved = _resolve_parameter_reference(cast("dict[str, Any]", parameter), spec)
            name = resolved.get("name")
            location = resolved.get("in")
            if not isinstance(name, str) or not isinstance(location, str):
                msg = f"OpenAPI parameter {resolved!r} must declare string `name` and `in` values"
                raise TypeError(msg)
            if location not in _OPENAPI_PARAMETER_LOCATIONS:
                msg = f"OpenAPI parameter {name!r} has unsupported location {location!r}"
                raise ValueError(msg)
            if "content" in resolved:
                msg = f"OpenAPI parameter {name!r} content is unsupported"
                raise ValueError(msg)
            # OpenAPI says an operation-level declaration replaces a path-level declaration
            # with the same (name, in) pair. Assignment retains the path item's order.
            merged[(name, location)] = resolved
    return list(merged.values())


def annotation(param: Param, schema: dict[str, Any]) -> str:
    """The type a caller may pass, from the manifest override or failing that the schema."""
    if param.type:
        return param.type
    match param.coercion_style:
        case "date":
            return "str | date | datetime"
        case "comma_list":
            return "list[str]"
        case "comma_choice_list":
            return f"list[{param.literal}]"
        case "choice":
            return str(param.literal)
        case _:
            return schema_annotation(schema)


def schema_annotation(schema: dict[str, Any]) -> str:
    """The usable caller type for the OpenAPI schema subset query serialization handles."""
    if enum := schema.get("enum"):
        return literal_of(enum)
    if schema.get("format") == "date":
        return "str | date | datetime"
    if schema.get("type") == "array":
        items = schema.get("items", {})
        item_type = schema_annotation(cast("dict[str, Any]", items)) if isinstance(items, dict) else "str"
        return f"list[{item_type}]"
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties", {})
        value_type = schema_annotation(cast("dict[str, Any]", additional)) if isinstance(additional, dict) else "str"
        return f"dict[str, {value_type}]"
    return SCHEMA_TYPES.get(schema.get("type", "string"), "str")


def query_serialization(param: Param, specification: dict[str, Any]) -> tuple[QueryStyle, bool]:
    """Resolve OpenAPI's query serialization properties, with manifest overrides."""
    raw_style = specification.get("style", "form")
    style = param.style or raw_style
    if style not in {"form", "spaceDelimited", "pipeDelimited"}:
        msg = f"query parameter style {style!r} is unsupported"
        raise ValueError(msg)
    raw_explode = specification.get("explode", style == "form")
    explode = param.explode if param.explode is not None else raw_explode
    if not isinstance(explode, bool):
        msg = f"query parameter explode value {explode!r} must be boolean"
        raise TypeError(msg)
    return style, explode


def coercion(
    param: Param,
    name: str,
    schema: dict[str, Any],
) -> str:
    """The expression that turns the argument into its wire value."""
    if param.coerce_function:
        literal = param.literal or "None"
        result = f'{param.coerce_function}({name}, param_name="{name}", literal={literal})'
    else:
        coercion_style = param.coercion_style
        if coercion_style == "plain" and schema.get("format") == "date":
            coercion_style = "date"  # the vendor said it is a date even if the manifest did not
        match coercion_style:
            case "date":
                result = f'coerce_date({name}, "{name}")'
            case "comma_list":
                result = f'",".join({name}) if {name} else None'
            case "comma_choice_list":
                result = f'coerce_choices({name}, {param.literal}, "{name}")'
            case "choice":
                result = f'coerce_choice({name}, {param.literal}, "{name}")'
            case _:
                result = name
    return result


def resolve(endpoint: Endpoint, operation: dict[str, Any] | None) -> list[ResolvedParam]:
    """
    Build the argument list for one endpoint.

    Reads query and header parameters from `operation` when a spec is available, otherwise from
    the manifest's `declared_params`. Path parameters are excluded: they are passed positionally
    through `SpitzeisenEndpointSpec.url()`, not as request arguments.
    """
    sources: list[tuple[str, dict[str, Any], bool, ParameterLocation, dict[str, Any]]] = []
    if operation is not None:
        sources = [
            (param["name"], param.get("schema", {}), bool(param.get("required", False)), param["in"], param)
            for param in operation.get("parameters", [])
            if param.get("in") in {"query", "header"}
        ]
    else:
        sources = [(wire, {}, False, param.location, {}) for wire, param in endpoint.declared_params.items()]

    descriptions = {
        param["name"]: tidy(param.get("description", "")) for param in (operation or {}).get("parameters", [])
    }

    resolved: list[ResolvedParam] = []
    for wire_name, schema, spec_required, location, specification in sources:
        structural = set(STRUCTURAL)
        if endpoint.page_size_param:
            structural.add(endpoint.page_size_param)
        if endpoint.sort_param:
            structural.add(endpoint.sort_param)
        if endpoint.order_param:
            structural.add(endpoint.order_param)
        if (location == "query" and wire_name in structural) or wire_name in endpoint.exclude_params:
            continue
        override = endpoint.params.get(wire_name) or endpoint.declared_params.get(wire_name) or Param()
        name = override.name or python_name(wire_name)
        required = spec_required or override.required is True
        style, explode = query_serialization(override, specification) if location == "query" else ("form", True)
        resolved.append(
            ResolvedParam(
                name=name,
                wire_name=wire_name,
                type=annotation(override, schema),
                description=override.description or descriptions.get(wire_name) or f"Filter on `{wire_name}`.",
                coercion=coercion(override, name, schema),
                location=location,
                style=style,
                explode=explode,
                required=required,
                default=override.default if override.default is not None else "None",
            ),
        )
    return resolved


def resolve_path_params(endpoint: Endpoint, operation: dict[str, Any] | None) -> list[ResolvedParam]:
    """
    Build the positional arguments taken from the path template.

    These are not query parameters: they are substituted into the URL by `SpitzeisenEndpointSpec.url`,
    so they keep the vendor's placeholder name on the wire and the client's chosen name in
    Python — a vendor's `{id}` is often something more specific to its callers.
    """
    placeholders = [name for _, name, _, _ in string.Formatter().parse(endpoint.path) if name]
    parameter_specs = {
        param["name"]: param for param in (operation or {}).get("parameters", []) if param.get("in") == "path"
    }
    resolved: list[ResolvedParam] = []
    for wire_name in placeholders:
        parameter = parameter_specs.get(wire_name, {})
        override = endpoint.params.get(wire_name) or endpoint.declared_params.get(wire_name) or Param()
        name = endpoint.path_params.get(wire_name) or override.name or python_name(wire_name)
        resolved.append(
            ResolvedParam(
                name=name,
                wire_name=wire_name,
                type=annotation(override, parameter.get("schema", {})),
                description=override.description
                or tidy(parameter.get("description", ""))
                or f"Path parameter `{wire_name}`.",
                # A path value is substituted into the URL, so it still has to be coerced to a
                # string first — a date argument is as welcome here as in a query parameter.
                coercion=coercion(override, name, parameter.get("schema", {})),
            ),
        )
    return resolved


def sorting(endpoint: Endpoint, operation: dict[str, Any] | None) -> ResolvedSorting | None:
    """
    Resolve synthesized sorting arguments from explicit wire names and parameter schemas.

    OpenAPI can describe the named parameters but cannot mark them as sorting controls, so
    the manifest supplies their wire names. Types, closed value sets and defaults come from
    those parameters when available, with manifest settings filling any gaps.
    """
    if endpoint.sort_style == "none":
        return None

    if endpoint.sort_param is None:
        msg = f"sort_style={endpoint.sort_style!r} requires `sort_param`"
        raise ValueError(msg)
    sort_specification = _sorting_parameter(operation, endpoint.sort_param)
    sort_schema = dict(cast("dict[str, Any]", sort_specification.get("schema", {})))
    order_specification: dict[str, Any] = {}
    order_schema: dict[str, Any] = {}
    if endpoint.order_param is not None:
        order_specification = _sorting_parameter(operation, endpoint.order_param)
        order_schema = dict(cast("dict[str, Any]", order_specification.get("schema", {})))

    schema_sort_default = sort_schema.get("default")
    schema_order_default = order_schema.get("default")
    if endpoint.sort_style == "suffix":
        schema_sort_default, suffix_default = _split_suffix_default(schema_sort_default)
        if schema_order_default is None:
            schema_order_default = suffix_default
        _split_suffix_enum(sort_schema, order_schema)

    sort_default = endpoint.sort_default if endpoint.sort_default is not None else schema_sort_default
    order_default = endpoint.order_default if endpoint.order_default is not None else schema_order_default
    if sort_default is None:
        msg = f"sorting parameter {endpoint.sort_param!r} needs a default in OpenAPI or `sort_default`"
        raise ValueError(msg)
    if order_default is None:
        source = endpoint.order_param or f"the suffix on {endpoint.sort_param!r}"
        msg = f"sorting direction {source!r} needs a default in OpenAPI or `order_default`"
        raise ValueError(msg)

    sort_override = endpoint.params.get(endpoint.sort_param) or endpoint.declared_params.get(endpoint.sort_param)
    sort_arg = _sorting_argument(
        name="sort",
        wire_name=endpoint.sort_param,
        default=sort_default,
        schema=sort_schema,
        specification=sort_specification,
        override=sort_override or Param(),
        explicit_literal=endpoint.sort_literal,
    )
    order_override = (
        endpoint.params.get(endpoint.order_param) or endpoint.declared_params.get(endpoint.order_param)
        if endpoint.order_param
        else None
    )
    order_arg = _sorting_argument(
        name="order",
        wire_name=endpoint.order_param,
        default=order_default,
        schema=order_schema,
        specification=order_specification,
        override=order_override or Param(),
        explicit_literal=endpoint.order_literal,
    )
    return ResolvedSorting(endpoint.sort_style, sort_arg, order_arg)


def _sorting_parameter(operation: dict[str, Any] | None, wire_name: str) -> dict[str, Any]:
    """Find one explicitly named sorting parameter and catch manifest/spec drift."""
    matching = next(
        (
            param
            for param in (operation or {}).get("parameters", [])
            if param.get("in") == "query" and param.get("name") == wire_name
        ),
        None,
    )
    if operation is not None and matching is None:
        msg = f"sorting parameter {wire_name!r} is absent from the OpenAPI operation"
        raise ValueError(msg)
    return matching or {}


def _split_suffix_default(value: Any) -> tuple[Any, str | None]:
    """Split a declared `field.direction` default without assuming direction vocabulary."""
    if value is None:
        return None, None
    text = str(value)
    if "." not in text:
        return text, None
    field, direction = text.rsplit(".", 1)
    return field, direction


def _split_suffix_enum(sort_schema: dict[str, Any], order_schema: dict[str, Any]) -> None:
    """Turn a closed set of `field.direction` values into independent closed sets."""
    enum = sort_schema.get("enum")
    if not isinstance(enum, list) or not enum:
        return
    values = cast("list[object]", enum)
    if not all(isinstance(value, str) and "." in value for value in values):
        return
    split = [value.rsplit(".", 1) for value in cast("list[str]", enum)]
    sort_schema["enum"] = list(dict.fromkeys(field for field, _ in split))
    order_schema["enum"] = list(dict.fromkeys(direction for _, direction in split))


def _sorting_argument(
    *,
    name: str,
    wire_name: str | None,
    default: Any,
    schema: dict[str, Any],
    specification: dict[str, Any],
    override: Param,
    explicit_literal: str | None,
) -> ResolvedSortArgument:
    """Resolve one public sorting argument, including schema- or manifest-owned validation."""
    literal = explicit_literal or override.literal
    if literal is None and schema.get("enum"):
        literal = literal_of(cast("list[Any]", schema["enum"]))

    if override.coercion_style != "plain" or override.coerce_function:
        expression = coercion(override, name, schema)
    elif literal:
        expression = f'coerce_choice({name}, {literal}, "{name}")'
    else:
        expression = name

    style, explode = query_serialization(override, specification) if wire_name else ("form", True)
    return ResolvedSortArgument(
        wire_name=wire_name,
        type=explicit_literal or annotation(override, schema),
        default=default,
        coercion=expression,
        style=style,
        explode=explode,
    )


def page_size(endpoint: Endpoint, operation: dict[str, Any] | None) -> ResolvedPageSize | None:
    """
    Resolve the page-size query parameter declared by the manifest.

    The wire name must be explicit because OpenAPI does not identify which query parameter
    controls pagination. The manifest may also state the maximum; otherwise it is read from
    the matching OpenAPI parameter.
    """
    if endpoint.page_size_param is None:
        return None

    matching = next(
        (
            param
            for param in (operation or {}).get("parameters", [])
            if param.get("in") == "query" and param.get("name") == endpoint.page_size_param
        ),
        None,
    )
    if operation is not None and matching is None:
        msg = f"page-size parameter {endpoint.page_size_param!r} is absent from the OpenAPI operation"
        raise ValueError(msg)

    maximum = endpoint.max_page_size
    if maximum is None and matching is not None:
        schema = matching.get("schema", {})
        maximum = schema.get("maximum") or schema.get("max")
    if maximum is None:
        msg = (
            f"page-size parameter {endpoint.page_size_param!r} needs `max_page_size` in the manifest "
            "or a maximum in its OpenAPI schema"
        )
        raise ValueError(msg)
    return ResolvedPageSize(endpoint.page_size_param, int(maximum))
