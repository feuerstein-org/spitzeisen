"""
Compile protocol-neutral OpenAPI IR plus SDK policy into a rendering plan.

OpenAPI describes what goes over the wire. The manifest describes the SDK we want to expose:
method names, pagination, rate-limit cost, response shape, and friendly param names.
This module combines them.
"""

from __future__ import annotations

import ast
import keyword
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast, get_args

from spitzeisen.codegen.ir import (
    ArrayType,
    IntersectionType,
    LiteralType,
    ObjectType,
    ParamIR,
    ParamLocationIR,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    TypeIR,
    UnionType,
)
from spitzeisen.codegen.manifest import ManifestParam
from spitzeisen.params import QueryStyle

if TYPE_CHECKING:
    from spitzeisen.codegen.ir import JSONScalar, JSONValue
    from spitzeisen.codegen.manifest import (
        ActiveSortStyle,
        Manifest,
        ManifestOperation,
        NotFound,
        PaginationStyle,
        Shape,
    )
    from spitzeisen.codegen.parser import ParsedOpenAPI, ParsedOperation

type WireLocation = Literal["query", "header"]

_STRUCTURAL_PARAMS = {"cursor", "page", "offset"}
_QUERY_STYLES = frozenset(get_args(QueryStyle))


@dataclass(frozen=True, slots=True)
class ParamPlan:
    """One public Python argument and its wire representation."""

    name: str
    wire_name: str
    annotation: str
    description: str
    coercion: str
    location: WireLocation = "query"
    style: QueryStyle = "form"
    explode: bool = True
    required: bool = False
    client_default: str | None = "None"


@dataclass(frozen=True, slots=True)
class PageSizePlan:
    """The query param controlling page size and its accepted maximum."""

    wire_name: str
    maximum: int


@dataclass(frozen=True, slots=True)
class SortArgumentPlan:
    """One synthesized sorting argument."""

    wire_name: str | None
    annotation: str
    default: JSONValue
    coercion: str
    style: QueryStyle = "form"
    explode: bool = True


@dataclass(frozen=True, slots=True)
class SortingPlan:
    """The two public sorting arguments and their vendor representation."""

    style: ActiveSortStyle
    sort: SortArgumentPlan
    order: SortArgumentPlan


@dataclass(frozen=True, slots=True)
class OperationPlan:
    """All facts needed to render one operation, with no raw OpenAPI state."""

    key: str
    path: str
    method_name: str
    model: str
    summary: str
    docs_url: str | None
    generate_model: bool
    shape: Shape
    not_found: NotFound
    cost: float
    pagination: PaginationStyle
    results_key: str | None
    params: tuple[ParamPlan, ...]
    query_params: tuple[ParamPlan, ...]
    header_params: tuple[ParamPlan, ...]
    path_params: tuple[ParamPlan, ...]
    sorting: SortingPlan | None
    page_size: PageSizePlan | None
    example_args: tuple[str, ...]
    model_imports: tuple[str, ...]
    coerce_function_imports: tuple[str, ...]
    helpers: tuple[str, ...]

    @property
    def class_name(self) -> str:
        """Return the generated operation API class name."""
        return "".join(part.title() for part in self.key.split("_")) + "Api"

    @property
    def accessor(self) -> str:
        """Return the aggregate client's property name."""
        return f"{self.key}_api"

    @property
    def const_name(self) -> str:
        """Return the generated operation-spec constant name."""
        return f"{self.key.upper()}_OPERATION"


@dataclass(frozen=True, slots=True)
class ClientPlan:
    """The complete, renderer-ready SDK plan."""

    vendor: str
    base_url: str
    package: str
    client_name: str
    operations: tuple[OperationPlan, ...]


def compile_manifest(manifest: Manifest, openapi: ParsedOpenAPI) -> ClientPlan:
    """Combine a validated manifest with parsed OpenAPI operations."""
    operations = tuple(
        _compile_operation(manifest_op, _select_operation(manifest_op, openapi))
        for manifest_op in manifest.operations.values()
    )
    _validate_public_names(operations)
    return ClientPlan(
        vendor=manifest.vendor,
        base_url=manifest.base_url,
        package=manifest.package,
        client_name=manifest.client_name,
        operations=operations,
    )


def _select_operation(manifest_op: ManifestOperation, openapi: ParsedOpenAPI) -> ParsedOperation:
    """Select an operation and catch manifest/spec drift before rendering."""
    parsed_operation = (
        openapi.operation_named(manifest_op.operation_id)
        if manifest_op.operation_id is not None
        else openapi.operation_at(manifest_op.path, manifest_op.method)
    )
    if parsed_operation is None:
        selector = (
            f"operationId {manifest_op.operation_id!r}"
            if manifest_op.operation_id is not None
            else f"{manifest_op.method.value.upper()} {manifest_op.path}"
        )
        message = f"{selector} is absent from the OpenAPI document, the vendor may have moved or renamed it"
        raise ValueError(message)
    if parsed_operation.path != manifest_op.path or parsed_operation.method is not manifest_op.method:
        expected = f"{manifest_op.method.value.upper()} {manifest_op.path}"
        actual = f"{parsed_operation.method.value.upper()} {parsed_operation.path}"
        message = f"operationId {manifest_op.operation_id!r} resolves to {actual}, not the manifest's {expected}"
        raise ValueError(message)
    # TODO: Add support for everything else
    if parsed_operation.method.value != "get":
        message = (
            f"{parsed_operation.method.value.upper()} operations are parsed but this runtime currently "
            "generates GET requests only"
        )
        raise ValueError(message)
    if parsed_operation.request_body is not None:
        message = "GET request bodies are preserved by the compiler but are not supported by the runtime renderer"
        raise ValueError(message)
    return parsed_operation


def _compile_operation(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> OperationPlan:
    """Compile one operation's params and SDK policy."""
    _validate_policy_references(manifest_op, openapi_op)
    params = _params(manifest_op, openapi_op)
    path_params = _path_params(manifest_op, openapi_op)
    sorting = _sorting(manifest_op, openapi_op)
    page_size = _page_size(manifest_op, openapi_op)
    query_params = tuple(param for param in params if param.location == "query")
    header_params = tuple(param for param in params if param.location == "header")
    model_imports = _model_imports(manifest_op, openapi_op)
    calls = [param.coercion for param in (*params, *path_params)]
    calls.extend("require_value(" for _ in path_params)
    calls.extend("require_value(" for param in header_params if param.required)
    if sorting is not None:
        calls.extend((sorting.sort.coercion, sorting.order.coercion))
        if sorting.style == "suffix":
            calls.append("coerce_sort(")
    example_args = [param.name for param in path_params if param.client_default is None]
    example_args.extend(param.name for param in params if param.required)
    _validate_signature_names(manifest_op, path_params, params, sorting)
    return OperationPlan(
        key=manifest_op.key,
        path=manifest_op.path,
        method_name=manifest_op.method_name,
        model=manifest_op.model,
        summary=manifest_op.summary or openapi_op.summary,
        docs_url=manifest_op.docs_url,
        generate_model=manifest_op.generate_model,
        shape=manifest_op.shape,
        not_found=manifest_op.not_found,
        cost=manifest_op.cost,
        pagination=manifest_op.pagination,
        results_key=manifest_op.results_key,
        params=params,
        query_params=query_params,
        header_params=header_params,
        path_params=path_params,
        sorting=sorting,
        page_size=page_size,
        example_args=tuple(example_args),
        model_imports=model_imports,
        coerce_function_imports=_coerce_function_imports(manifest_op),
        helpers=_helpers_used(calls, model_imports),
    )


def _params(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> tuple[ParamPlan, ...]:
    """Build public query and header arguments."""
    params = tuple(
        (param.wire_name, param.location.value, param)
        for param in openapi_op.params
        if param.location is not ParamLocationIR.PATH
    )

    structural = set(_STRUCTURAL_PARAMS)
    structural.update(
        item
        for item in (
            manifest_op.page_size_param,
            manifest_op.sort_param,
            manifest_op.order_param,
        )
        if item is not None
    )
    plans: list[ParamPlan] = []
    for wire_name, raw_location, openapi_spec in params:
        if raw_location == ParamLocationIR.COOKIE.value:
            message = f"param {wire_name!r} uses unsupported location 'cookie'"
            raise ValueError(message)
        if raw_location not in {"query", "header"}:
            message = f"param {wire_name!r} uses unsupported location {raw_location!r}"
            raise ValueError(message)
        location = cast("WireLocation", raw_location)
        if openapi_spec.content:
            message = f"content-based param {wire_name!r} is not supported by the runtime renderer"
            raise ValueError(message)
        if openapi_spec.allow_reserved:
            message = f"query param {wire_name!r} sets allowReserved, which the runtime cannot serialize faithfully"
            raise ValueError(message)
        if (location == "query" and wire_name in structural) or wire_name in manifest_op.exclude_params:
            continue
        override = manifest_op.params.get(wire_name) or ManifestParam()
        name = override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        required = openapi_spec.required or override.required is True
        style, explode = _query_serialization(override, openapi_spec) if location == "query" else ("form", True)
        client_default = _param_default(override, openapi_spec, required, wire_name=wire_name)
        plans.append(
            ParamPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, openapi_spec.schema),
                description=(override.description or tidy(openapi_spec.description) or f"Path param `{wire_name}`."),
                coercion=_coercion(override, name, openapi_spec.schema),
                location=location,
                style=style,
                explode=explode,
                required=required,
                client_default=client_default,
            ),
        )
    _validate_unique_arguments(manifest_op, plans)
    return tuple(plans)


def _path_params(
    manifest_op: ManifestOperation,
    openapi_op: ParsedOperation,
) -> tuple[ParamPlan, ...]:
    """Build positional path arguments in path-template order."""
    # Extract variable parts from path:
    # e.g. ("/users/{user_id}/posts/{post_id}") yields: ["user_id", "post_id"]
    placeholders = [name for _, name, _, _ in string.Formatter().parse(manifest_op.path) if name]
    params = {param.wire_name: param for param in openapi_op.params if param.location is ParamLocationIR.PATH}
    plans: list[ParamPlan] = []
    for wire_name in placeholders:
        param = params.get(wire_name)
        override = manifest_op.params.get(wire_name) or ManifestParam()
        name = manifest_op.path_params.get(wire_name) or override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        schema = param.schema if param is not None else None
        client_default = _param_default(override, param, required=True, wire_name=wire_name)
        plans.append(
            ParamPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, schema),
                description=(
                    override.description
                    or (tidy(param.description) if param is not None else "")
                    or f"Path param `{wire_name}`."
                ),
                coercion=_coercion(override, name, schema),
                required=True,
                client_default=client_default,
            ),
        )
    _validate_unique_arguments(manifest_op, plans)
    return tuple(plans)


def _annotation(override: ManifestParam, schema: TypeIR | None) -> str:
    """Resolve a public input annotation from policy first, then protocol type."""
    if override.annotation:
        return override.annotation
    match override.coercion_style:
        case "date":
            return "str | date | datetime"
        case "comma_list":
            return "list[str]"
        case "comma_choice_list":
            return f"list[{_required_literal(override)}]"
        case "choice":
            return _required_literal(override)
        case _:
            return type_annotation(schema)


def type_annotation(schema: TypeIR | None) -> str:  # noqa: PLR0911
    """Render a caller-facing Python annotation for a neutral schema type."""
    if schema is None:
        return "str"
    if isinstance(schema, PrimitiveType):
        if schema.format == "date":
            return "str | date | datetime"
        return {
            PrimitiveKind.STRING: "str",
            PrimitiveKind.INTEGER: "int",
            PrimitiveKind.NUMBER: "float",
            PrimitiveKind.BOOLEAN: "bool",
            PrimitiveKind.NULL: "None",
        }[schema.kind]
    if isinstance(schema, LiteralType):
        return literal_of(schema.values)
    if isinstance(schema, ArrayType):
        return f"list[{type_annotation(schema.items)}]"
    if isinstance(schema, ObjectType):
        value = type_annotation(schema.additional_properties) if schema.additional_properties is not None else "object"
        return f"dict[str, {value}]"
    if isinstance(schema, ReferenceType):
        return schema.suggested_name
    if isinstance(schema, UnionType):
        annotations = list(dict.fromkeys(type_annotation(option) for option in schema.options))
        return " | ".join(annotations)
    if isinstance(schema, IntersectionType):
        reference = next((part for part in schema.parts if isinstance(part, ReferenceType)), None)
        return reference.suggested_name if reference is not None else "object"
    return "str"


def literal_of(values: tuple[JSONScalar, ...] | list[JSONScalar]) -> str:
    """Render a closed scalar set as a Literal annotation."""
    return "Literal[" + ", ".join(repr(value) for value in values) + "]"


def _coercion(override: ManifestParam, name: str, schema: TypeIR | None) -> str:
    """Build the expression converting a public argument into a wire value."""
    if override.coerce_function:
        # Custom functions may accept no client-owned Literal alias; pass None explicitly.
        literal = override.literal or "None"
        return f'{override.coerce_function}({name}, param_name="{name}", literal_type={literal})'
    style = override.coercion_style
    if style == "plain" and _is_date(schema):
        style = "date"
    match style:
        case "date":
            return f'coerce_date({name}, "{name}")'
        case "comma_list":
            return f'",".join({name}) if {name} else None'
        case "comma_choice_list":
            return f'coerce_choices({name}, {_required_literal(override)}, "{name}")'
        case "choice":
            return f'coerce_choice({name}, {_required_literal(override)}, "{name}")'
        case _:
            return name


def _required_literal(override: ManifestParam) -> str:
    """Return a required manifest Literal alias, reporting invalid policy at generation time."""
    if not override.literal:
        message = f"coercion_style={override.coercion_style!r} requires `literal` naming the Literal type"
        raise ValueError(message)
    return override.literal


def _query_serialization(
    override: ManifestParam,
    specification: ParamIR | None,
) -> tuple[QueryStyle, bool]:
    """Resolve OpenAPI query serialization, with explicit manifest overrides."""
    raw_style = (specification.style or "form") if specification is not None else "form"
    style = override.style or raw_style
    if style not in _QUERY_STYLES:
        message = f"query param style {style!r} is unsupported"
        raise ValueError(message)
    explode = (
        override.explode
        if override.explode is not None
        else (specification.explode if specification is not None else style == "form")
    )
    return cast("QueryStyle", style), explode


def _param_default(
    override: ManifestParam,
    specification: ParamIR | None,
    required: bool,
    *,
    wire_name: str,
) -> str | None:
    """Choose the SDK default while preserving the parameter's wire requiredness."""
    if override.client_default is not None:
        if required and ast.literal_eval(override.client_default) is None:
            message = f"required param {wire_name!r} cannot declare a client_default of None"
            raise ValueError(message)
        return override.client_default
    if required:
        return None
    if specification is not None and specification.has_default:
        return repr(specification.default)
    return "None"


def _sorting(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> SortingPlan | None:
    """Compile the manifest's sorting policy against its named OpenAPI params."""
    if manifest_op.sort_style == "none":
        return None
    sort_param = _named_query_param(openapi_op, manifest_op.sort_param, "sorting")
    order_param = (
        _named_query_param(openapi_op, manifest_op.order_param, "sorting")
        if manifest_op.order_param is not None
        else None
    )
    sort_default = sort_param.default if sort_param is not None and sort_param.has_default else None
    order_default = order_param.default if order_param is not None and order_param.has_default else None
    sort_type = sort_param.schema if sort_param is not None else None
    order_type = order_param.schema if order_param is not None else None
    if manifest_op.sort_style == "suffix":
        sort_default, suffix_default = _split_suffix_default(sort_default)
        if order_default is None:
            order_default = suffix_default
        sort_type, order_type = _split_suffix_literals(sort_type, order_type)
    if manifest_op.sort_default is not None:
        sort_default = manifest_op.sort_default
    if manifest_op.order_default is not None:
        order_default = manifest_op.order_default
    if sort_default is None:
        message = f"sorting param {manifest_op.sort_param!r} needs a default in OpenAPI or `sort_default`"
        raise ValueError(message)
    if order_default is None:
        source = manifest_op.order_param or f"the suffix on {manifest_op.sort_param!r}"
        message = f"sorting direction {source!r} needs a default in OpenAPI or `order_default`"
        raise ValueError(message)
    # manifest validation guarantees sort_param is not None
    # TODO: What if it is None? Where does SortingPlan get processed?
    sort_override = manifest_op.params.get(manifest_op.sort_param) if manifest_op.sort_param is not None else None
    order_override = manifest_op.params.get(manifest_op.order_param) if manifest_op.order_param is not None else None
    return SortingPlan(
        style=manifest_op.sort_style,
        sort=_sort_argument(
            name="sort",
            wire_name=manifest_op.sort_param,
            default=sort_default,
            schema=sort_type,
            specification=sort_param,
            override=sort_override or ManifestParam(),
            explicit_literal=manifest_op.sort_literal,
        ),
        order=_sort_argument(
            name="order",
            wire_name=manifest_op.order_param,
            default=order_default,
            schema=order_type,
            specification=order_param,
            override=order_override or ManifestParam(),
            explicit_literal=manifest_op.order_literal,
        ),
    )


def _named_query_param(
    openapi_op: ParsedOperation,
    wire_name: str | None,
    purpose: str,
) -> ParamIR | None:
    """Find a policy-named query param and report manifest/spec drift."""
    if wire_name is None:
        return None
    matching = next(
        (
            param
            for param in openapi_op.params
            if param.location is ParamLocationIR.QUERY and param.wire_name == wire_name
        ),
        None,
    )
    if matching is None:
        message = f"{purpose} param {wire_name!r} is absent from the OpenAPI operation"
        raise ValueError(message)
    return matching


def _sort_argument(
    *,
    name: str,
    wire_name: str | None,
    default: JSONValue,
    schema: TypeIR | None,
    specification: ParamIR | None,
    override: ManifestParam,
    explicit_literal: str | None,
) -> SortArgumentPlan:
    """Compile one public sorting argument."""
    literal = explicit_literal or override.literal
    if literal is None and isinstance(schema, LiteralType):
        literal = literal_of(schema.values)
    if override.coercion_style != "plain" or override.coerce_function:
        expression = _coercion(override, name, schema)
    elif literal:
        expression = f'coerce_choice({name}, {literal}, "{name}")'
    else:
        expression = name
    style, explode = _query_serialization(override, specification) if wire_name else ("form", True)
    return SortArgumentPlan(
        wire_name=wire_name,
        annotation=literal or _annotation(override, schema),
        default=default,
        coercion=expression,
        style=style,
        explode=explode,
    )


def _split_suffix_default(value: JSONValue) -> tuple[JSONValue, str | None]:
    """Split a `field.direction` default."""
    if value is None:
        return None, None
    text = str(value)
    if "." not in text:
        return text, None
    field, direction = text.rsplit(".", 1)
    return field, direction


# TODO: Look into this...
def _split_suffix_literals(
    sort_schema: TypeIR | None, order_schema: TypeIR | None
) -> tuple[TypeIR | None, TypeIR | None]:
    """Turn closed `field.direction` values into independent literal sets."""
    if not isinstance(sort_schema, LiteralType) or not sort_schema.values:
        return sort_schema, order_schema
    if not all(isinstance(value, str) and "." in value for value in sort_schema.values):
        return sort_schema, order_schema
    string_values = tuple(value for value in sort_schema.values if isinstance(value, str))
    split = [value.rsplit(".", 1) for value in string_values]
    fields = tuple(dict.fromkeys(field for field, _ in split))
    directions = tuple(dict.fromkeys(direction for _, direction in split))
    return LiteralType(fields), LiteralType(directions)


def _page_size(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> PageSizePlan | None:
    """Compile page-size policy and its schema constraint."""
    if manifest_op.page_size_param is None:
        return None
    matching = _named_query_param(openapi_op, manifest_op.page_size_param, "page-size")
    maximum = manifest_op.max_page_size
    if maximum is None and matching is not None and isinstance(matching.schema, PrimitiveType):
        schema_maximum = matching.schema.maximum
        maximum = int(schema_maximum) if schema_maximum is not None else None
    if maximum is None:
        message = f"page-size param {manifest_op.page_size_param!r} needs `max_page_size` or an OpenAPI maximum"
        raise ValueError(message)
    return PageSizePlan(manifest_op.page_size_param, maximum)


def _model_imports(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> tuple[str, ...]:
    """Collect model-owned aliases referenced by generated annotations or coercion."""
    names = {
        name
        for name in (
            manifest_op.sort_literal,
            manifest_op.order_literal,
            *(param.literal for param in manifest_op.params.values()),
        )
        if name
    }
    for param in openapi_op.params:
        names.update(_reference_names(param.schema))
    names.discard(manifest_op.model)
    return tuple(sorted(names))


# TODO: What's this for? Look into the typing classes/enums in general
def _reference_names(schema: TypeIR | None) -> set[str]:
    """Collect reference names from a type tree without resolving them."""
    if schema is None:
        return set()
    if isinstance(schema, ReferenceType):
        return {schema.suggested_name}
    if isinstance(schema, ArrayType):
        return _reference_names(schema.items)
    if isinstance(schema, ObjectType):
        return _reference_names(schema.additional_properties)
    if isinstance(schema, (UnionType, IntersectionType)):
        members = schema.options if isinstance(schema, UnionType) else schema.parts
        names: set[str] = set()
        for member in members:
            names.update(_reference_names(member))
        return names
    return set()


def _coerce_function_imports(manifest_op: ManifestOperation) -> tuple[str, ...]:
    """Collect client-owned coercion functions referenced by one manifest operation."""
    return tuple(
        sorted(
            {param.coerce_function for param in manifest_op.params.values() if param.coerce_function is not None},
        ),
    )


def _helpers_used(calls: list[str], model_imports: tuple[str, ...]) -> tuple[str, ...]:
    """Collect framework helpers actually called by one operation."""
    helper_for_call = {
        "coerce_date(": "coerce_date",
        "coerce_choices(": "coerce_choices",
        "coerce_choice(": "coerce_choice",
        "coerce_sort(": "coerce_sort",
        "require_value(": "require_value",
    }
    joined = " ".join(calls)
    found = {helper for call, helper in helper_for_call.items() if call in joined}
    always = {"build_header_params", "NoPagination", "serialize_query_param"}
    return tuple(sorted((found | always) - set(model_imports)))


def _validate_public_names(operations: tuple[OperationPlan, ...]) -> None:
    """Reject aggregate-client property and class collisions."""
    for attribute in ("accessor", "class_name", "const_name"):
        values = [getattr(operation, attribute) for operation in operations]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            message = f"operation keys produce duplicate {attribute.replace('_', ' ')} values: {duplicates}"
            raise ValueError(message)

    model_names = [operation.model for operation in operations if operation.generate_model]
    duplicate_models = sorted({name for name in model_names if model_names.count(name) > 1})
    if duplicate_models:
        message = (
            f"multiple generated operations use public response models {duplicate_models}; "
            "give each extension module an unambiguous model"
        )
        raise ValueError(message)


def _validate_policy_references(manifest_op: ManifestOperation, openapi_op: ParsedOperation) -> None:
    """Catch manifest keys that would otherwise be silently ignored."""
    placeholders = set(_path_param_names(manifest_op.path))
    unknown_path_names = sorted(set(manifest_op.path_params) - placeholders)
    if unknown_path_names:
        message = f"path param mappings {unknown_path_names} do not occur in {manifest_op.path!r}"
        raise ValueError(message)
    available = {param.wire_name for param in openapi_op.params}
    unknown_overrides = sorted(set(manifest_op.params) - available)
    if unknown_overrides:
        message = f"param overrides {unknown_overrides} are absent from the selected OpenAPI operation"
        raise ValueError(message)
    unknown_exclusions = sorted(set(manifest_op.exclude_params) - available)
    if unknown_exclusions:
        message = f"excluded params {unknown_exclusions} are absent from the selected OpenAPI operation"
        raise ValueError(message)


def _validate_signature_names(
    manifest_op: ManifestOperation,
    path_params: tuple[ParamPlan, ...],
    params: tuple[ParamPlan, ...],
    sorting: SortingPlan | None,
) -> None:
    """Reject collisions across signature sections and generated control arguments."""
    names = [param.name for param in (*path_params, *params)]
    reserved: set[str] = {"max_results", "on_validation_error"} if manifest_op.shape == "collection" else set()
    if sorting is not None:
        reserved.update({"sort", "order"})
    duplicates = sorted({name for name in names if names.count(name) > 1} | (set(names) & reserved))
    if duplicates:
        message = f"operation {manifest_op.key!r} produces duplicate or reserved arguments {duplicates}"
        raise ValueError(message)


def _validate_unique_arguments(manifest_op: ManifestOperation, params: list[ParamPlan]) -> None:
    """Reject friendly-name collisions within one signature section."""
    names = [param.name for param in params]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        message = f"operation {manifest_op.key!r} maps multiple wire params to {duplicates}"
        raise ValueError(message)


def _validate_argument_name(name: str, wire_name: str) -> None:
    """Require generated public arguments to be legal Python identifiers."""
    if name.isidentifier() and not keyword.iskeyword(name):
        return
    message = f"param {wire_name!r} maps to invalid Python argument {name!r}; configure a friendly `name`"
    raise ValueError(message)


def _is_date(schema: TypeIR | None) -> bool:
    """Whether a type has OpenAPI's date format."""
    if isinstance(schema, PrimitiveType):
        return schema.format == "date"
    if isinstance(schema, UnionType):
        return any(_is_date(option) for option in schema.options)
    return False


# TODO: Add more validation maybe? Whitespace etc.
def python_name(wire_name: str) -> str:
    """Convert a dotted wire param into the conventional Python spelling."""
    return wire_name.replace(".", "_").replace("-", "_")


def tidy(text: str) -> str:
    """Collapse vendor prose onto one docstring line."""
    return re.sub(r"\s+", " ", text or "").strip()


def _path_param_names(path: str) -> tuple[str, ...]:
    """Return placeholder names from an operation path."""
    return tuple(name for _, name, _, _ in string.Formatter().parse(path) if name)
