"""Compile one assembled, trait-enriched Smithy model into a Python rendering plan."""

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
from spitzeisen.codegen.traits import (
    SMITHY_PAGINATED,
    ActiveSortStyle,
    ModelCustomizations,
    NotFound,
    PaginationStyle,
    ParamPolicy,
    Shape,
    operation_policy,
    page_number_policy,
    param_policy,
    sorting_policy,
)
from spitzeisen.params import QueryStyle

if TYPE_CHECKING:
    from spitzeisen.codegen.ir import JSONScalar, JSONValue
    from spitzeisen.codegen.parser import ParsedOperation, ParsedService, ParsedSmithy

type WireLocation = Literal["query", "header"]

_QUERY_STYLES = frozenset(get_args(QueryStyle))


@dataclass(frozen=True, slots=True)
class TargetSettings:
    """Python code-generation settings that do not describe the service itself."""

    package: str
    client_name: str
    service: str | None = None
    vendor: str | None = None


@dataclass(frozen=True, slots=True)
class OperationSettings:
    """Smithy-derived operation policy used by the existing renderer."""

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
    page_param: str | None
    page_start: int
    page_step: int
    page_size_param: str | None
    max_page_size: int | None
    sort_style: ActiveSortStyle | None
    sort_param: str | None
    order_param: str | None
    sort_literal: str | None
    order_literal: str | None
    sort_default: JSONValue
    order_default: JSONValue
    has_sort_default: bool
    has_order_default: bool
    params: dict[str, ParamPolicy]


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

    @property
    def runtime_annotation(self) -> str:
        """Annotation accepted by the opt-in strict input validator."""
        return f"{self.annotation} | None" if self.client_default == "None" else self.annotation


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
    """All facts needed to render one operation, with no raw Smithy state."""

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
    page_param: str | None
    page_start: int
    page_step: int
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
    package: str
    client_name: str
    operations: tuple[OperationPlan, ...]
    model_aliases: dict[str, str]
    model_type_overrides: dict[str, str]


def compile_model(
    target: TargetSettings,
    smithy: ParsedSmithy,
    customizations: ModelCustomizations | None = None,
) -> ClientPlan:
    """Compile the selected service closure and its custom traits."""
    _validate_target(target)
    service = smithy.service_named(target.service)
    by_id = {operation.shape_id: operation for operation in smithy.operations}
    missing = [shape_id for shape_id in service.operation_ids if shape_id not in by_id]
    if missing:
        message = f"service {service.shape_id} contains operations Spitzeisen could not parse: {missing}"
        raise ValueError(message)
    selected = (by_id[shape_id] for shape_id in service.operation_ids)
    operations = tuple(
        _compile_operation(settings, operation)
        for operation in selected
        if (settings := _operation_settings(operation, service)) is not None
    )
    if not operations:
        msg = f"service {service.shape_id} contains no visible operations"
        raise ValueError(msg)
    _validate_public_names(operations)
    model_settings = customizations or ModelCustomizations({}, {})
    return ClientPlan(
        vendor=target.vendor or _humanize_service_name(service.name),
        package=target.package,
        client_name=target.client_name,
        operations=operations,
        model_aliases=model_settings.aliases,
        model_type_overrides=model_settings.type_overrides,
    )


def _validate_operation(operation: ParsedOperation) -> None:
    """Reject protocol features the current renderer cannot implement."""
    if operation.method.value != "get":
        message = (
            f"{operation.method.value.upper()} operations are parsed but this runtime currently "
            "generates GET requests only"
        )
        raise ValueError(message)
    if operation.request_body is not None:
        message = "GET request bodies are preserved by the compiler but are not supported by the runtime renderer"
        raise ValueError(message)


def _operation_settings(operation: ParsedOperation, service: ParsedService) -> OperationSettings | None:
    """Derive renderer policy from standard and custom traits."""
    presentation = operation_policy(operation.traits)
    if presentation.hidden:
        return None
    _validate_operation(operation)
    if SMITHY_PAGINATED in operation.traits:
        msg = f"operation {operation.shape_id} uses cursor-based @paginated, which the current runtime does not support"
        raise ValueError(
            msg,
        )
    page = page_number_policy(operation.traits)
    sorting = sorting_policy(operation.traits)
    inferred_model, inferred_shape = _infer_response(operation)
    model = presentation.model or inferred_model
    if model is None:
        msg = f"operation {operation.shape_id} has no inferable response model; set @sdkOperation(responseModel: ...)"
        raise ValueError(
            msg,
        )
    shape = presentation.shape or ("collection" if page is not None else inferred_shape)
    if page is not None and shape != "collection":
        msg = f"operation {operation.shape_id} cannot combine page-number pagination with shape={shape!r}"
        raise ValueError(msg)
    if sorting is not None and shape != "collection":
        msg = f"operation {operation.shape_id} cannot combine a single response with sorting"
        raise ValueError(msg)
    if presentation.not_found == "empty" and shape != "single":
        msg = f"operation {operation.shape_id} can use notFound='empty' only with a single response"
        raise ValueError(msg)
    method_name = presentation.method_name or _snake_case(operation.name)
    key = presentation.key or _operation_key(method_name, operation.method.value)
    policies = {
        param.wire_name: param_policy(operation.param_traits.get(param.wire_name, {})) for param in operation.params
    }
    return OperationSettings(
        key=key,
        path=operation.path,
        method_name=method_name,
        model=model,
        summary=operation.summary,
        docs_url=presentation.docs_url or service.documentation_url,
        generate_model=presentation.generate_model,
        shape=shape,
        not_found=presentation.not_found,
        cost=presentation.cost,
        pagination="page_number" if page is not None else "none",
        results_key=(page.results_key if page is not None else None) or presentation.results_key,
        page_param=page.page_param if page is not None else None,
        page_start=page.start if page is not None else 1,
        page_step=page.step if page is not None else 1,
        page_size_param=page.page_size_param if page is not None else None,
        max_page_size=page.max_page_size if page is not None else None,
        sort_style=sorting.style if sorting is not None else None,
        sort_param=sorting.sort_param if sorting is not None else None,
        order_param=sorting.order_param if sorting is not None else None,
        sort_literal=sorting.sort_literal if sorting is not None else None,
        order_literal=sorting.order_literal if sorting is not None else None,
        sort_default=sorting.sort_default if sorting is not None else None,
        order_default=sorting.order_default if sorting is not None else None,
        has_sort_default=sorting.has_sort_default if sorting is not None else False,
        has_order_default=sorting.has_order_default if sorting is not None else False,
        params=policies,
    )


def _infer_response(operation: ParsedOperation) -> tuple[str | None, Shape]:
    """Infer the public model and cardinality from the successful JSON payload."""
    response = next((item for item in operation.responses if item.status.is_success), None)
    schema = response.content[0].schema if response is not None and response.content else None
    if isinstance(schema, ArrayType):
        item = schema.items
        return (item.suggested_name if isinstance(item, ReferenceType) else None), "collection"
    if isinstance(schema, ReferenceType):
        return schema.suggested_name, "single"
    return None, "single"


def _compile_operation(settings: OperationSettings, operation: ParsedOperation) -> OperationPlan:
    """Compile one Smithy operation and its traits."""
    _validate_policy_references(settings)
    params = _params(settings, operation)
    path_params = _path_params(settings, operation)
    sorting = _sorting(settings, operation)
    page_size = _page_size(settings, operation)
    query_params = tuple(param for param in params if param.location == "query")
    header_params = tuple(param for param in params if param.location == "header")
    model_imports = _model_imports(settings, operation)
    calls = [param.coercion for param in (*params, *path_params)]
    calls.extend("require_value(" for _ in path_params)
    calls.extend("require_value(" for param in header_params if param.required)
    if sorting is not None:
        calls.extend((sorting.sort.coercion, sorting.order.coercion))
        if sorting.style == "suffix":
            calls.append("coerce_sort(")
    example_args = [param.name for param in path_params if param.client_default is None]
    example_args.extend(param.name for param in params if param.required)
    _validate_signature_names(settings, path_params, params, sorting)
    return OperationPlan(
        key=settings.key,
        path=settings.path,
        method_name=settings.method_name,
        model=settings.model,
        summary=settings.summary,
        docs_url=settings.docs_url,
        generate_model=settings.generate_model,
        shape=settings.shape,
        not_found=settings.not_found,
        cost=settings.cost,
        pagination=settings.pagination,
        results_key=settings.results_key,
        page_param=settings.page_param,
        page_start=settings.page_start,
        page_step=settings.page_step,
        params=params,
        query_params=query_params,
        header_params=header_params,
        path_params=path_params,
        sorting=sorting,
        page_size=page_size,
        example_args=tuple(example_args),
        model_imports=model_imports,
        coerce_function_imports=_coerce_function_imports(settings),
        helpers=_helpers_used(calls, model_imports),
    )


def _params(settings: OperationSettings, operation: ParsedOperation) -> tuple[ParamPlan, ...]:
    """Build public query and header arguments."""
    params = tuple(
        (param.wire_name, param.location.value, param)
        for param in operation.params
        if param.location is not ParamLocationIR.PATH
    )

    structural = {
        item
        for item in (
            settings.page_param,
            settings.page_size_param,
            settings.sort_param,
            settings.order_param,
        )
        if item is not None
    }
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
        override = settings.params.get(wire_name) or ParamPolicy()
        if (location == "query" and wire_name in structural) or override.hidden:
            continue
        name = override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        required = openapi_spec.required
        style, explode = _query_serialization(override, openapi_spec) if location == "query" else ("form", True)
        client_default = _param_default(override, openapi_spec, required, wire_name=wire_name)
        plans.append(
            ParamPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, openapi_spec.schema),
                description=(tidy(openapi_spec.description) or f"Query param `{wire_name}`."),
                coercion=_coercion(override, name, openapi_spec.schema),
                location=location,
                style=style,
                explode=explode,
                required=required,
                client_default=client_default,
            ),
        )
    _validate_unique_arguments(settings, plans)
    return tuple(plans)


def _path_params(
    settings: OperationSettings,
    operation: ParsedOperation,
) -> tuple[ParamPlan, ...]:
    """Build positional path arguments in path-template order."""
    # Extract variable parts from path:
    # e.g. ("/users/{user_id}/posts/{post_id}") yields: ["user_id", "post_id"]
    placeholders = [name for _, name, _, _ in string.Formatter().parse(settings.path) if name]
    params = {param.wire_name: param for param in operation.params if param.location is ParamLocationIR.PATH}
    plans: list[ParamPlan] = []
    for wire_name in placeholders:
        param = params.get(wire_name)
        override = settings.params.get(wire_name) or ParamPolicy()
        name = override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        schema = param.schema if param is not None else None
        client_default = _param_default(override, param, required=True, wire_name=wire_name)
        plans.append(
            ParamPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, schema),
                description=((tidy(param.description) if param is not None else "") or f"Path param `{wire_name}`."),
                coercion=_coercion(override, name, schema),
                required=True,
                client_default=client_default,
            ),
        )
    _validate_unique_arguments(settings, plans)
    return tuple(plans)


def _annotation(override: ParamPolicy, schema: TypeIR | None) -> str:
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


def _coercion(override: ParamPolicy, name: str, schema: TypeIR | None) -> str:
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


def _required_literal(override: ParamPolicy) -> str:
    """Return a required client-owned Literal alias."""
    if not override.literal:
        message = f"coercion={override.coercion_style!r} requires @pythonParameter literal"
        raise ValueError(message)
    return override.literal


def _query_serialization(
    override: ParamPolicy,
    specification: ParamIR | None,
) -> tuple[QueryStyle, bool]:
    """Resolve generic HTTP query serialization, with explicit trait overrides."""
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
    override: ParamPolicy,
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


def _sorting(settings: OperationSettings, operation: ParsedOperation) -> SortingPlan | None:
    """Compile @sorting against its named Smithy input members."""
    if settings.sort_style is None:
        return None
    sort_param = _named_query_param(operation, settings.sort_param, "sorting")
    order_param = (
        _named_query_param(operation, settings.order_param, "sorting") if settings.order_param is not None else None
    )
    sort_default = sort_param.default if sort_param is not None and sort_param.has_default else None
    order_default = order_param.default if order_param is not None and order_param.has_default else None
    sort_type = sort_param.schema if sort_param is not None else None
    order_type = order_param.schema if order_param is not None else None
    if settings.sort_style == "suffix":
        sort_default, suffix_default = _split_suffix_default(sort_default)
        if order_default is None:
            order_default = suffix_default
        sort_type, order_type = _split_suffix_literals(sort_type, order_type)
    if settings.has_sort_default:
        sort_default = settings.sort_default
    if settings.has_order_default:
        order_default = settings.order_default
    if sort_default is None:
        message = f"sorting param {settings.sort_param!r} needs a Smithy @default or @sorting sortDefault"
        raise ValueError(message)
    if order_default is None:
        source = settings.order_param or f"the suffix on {settings.sort_param!r}"
        message = f"sorting direction {source!r} needs a Smithy @default or @sorting orderDefault"
        raise ValueError(message)
    sort_override = settings.params.get(settings.sort_param) if settings.sort_param is not None else None
    order_override = settings.params.get(settings.order_param) if settings.order_param is not None else None
    return SortingPlan(
        style=settings.sort_style,
        sort=_sort_argument(
            name="sort",
            wire_name=settings.sort_param,
            default=sort_default,
            schema=sort_type,
            specification=sort_param,
            override=sort_override or ParamPolicy(),
            explicit_literal=settings.sort_literal,
        ),
        order=_sort_argument(
            name="order",
            wire_name=settings.order_param,
            default=order_default,
            schema=order_type,
            specification=order_param,
            override=order_override or ParamPolicy(),
            explicit_literal=settings.order_literal,
        ),
    )


def _named_query_param(
    operation: ParsedOperation,
    wire_name: str | None,
    purpose: str,
) -> ParamIR | None:
    """Find a trait-named query member and report model drift."""
    if wire_name is None:
        return None
    matching = next(
        (
            param
            for param in operation.params
            if param.location is ParamLocationIR.QUERY and param.wire_name == wire_name
        ),
        None,
    )
    if matching is None:
        message = f"{purpose} param {wire_name!r} is absent from the Smithy operation"
        raise ValueError(message)
    return matching


def _sort_argument(
    *,
    name: str,
    wire_name: str | None,
    default: JSONValue,
    schema: TypeIR | None,
    specification: ParamIR | None,
    override: ParamPolicy,
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


def _page_size(settings: OperationSettings, operation: ParsedOperation) -> PageSizePlan | None:
    """Compile page-size policy and its Smithy constraint."""
    if settings.page_size_param is None:
        return None
    matching = _named_query_param(operation, settings.page_size_param, "page-size")
    maximum = settings.max_page_size
    if maximum is None and matching is not None and isinstance(matching.schema, PrimitiveType):
        schema_maximum = matching.schema.maximum
        maximum = int(schema_maximum) if schema_maximum is not None else None
    if maximum is None:
        message = (
            f"page-size param {settings.page_size_param!r} needs @pageNumberPagination maxPageSize "
            "or a Smithy @range maximum"
        )
        raise ValueError(message)
    return PageSizePlan(settings.page_size_param, maximum)


def _model_imports(settings: OperationSettings, operation: ParsedOperation) -> tuple[str, ...]:
    """Collect model-owned aliases referenced by generated annotations or coercion."""
    names = {
        name
        for name in (
            settings.sort_literal,
            settings.order_literal,
            *(param.literal for param in settings.params.values()),
        )
        if name
    }
    for param in operation.params:
        names.update(_reference_names(param.schema))
    names.discard(settings.model)
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


def _coerce_function_imports(settings: OperationSettings) -> tuple[str, ...]:
    """Collect client-owned coercion functions referenced by operation input traits."""
    return tuple(
        sorted(
            {param.coerce_function for param in settings.params.values() if param.coerce_function is not None},
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


def _validate_policy_references(settings: OperationSettings) -> None:
    """Reject response paths the runtime cannot traverse faithfully."""
    if settings.results_key is not None and "." in settings.results_key:
        msg = "nested resultPath/items paths are not supported by the current runtime"
        raise ValueError(msg)


def _validate_signature_names(
    settings: OperationSettings,
    path_params: tuple[ParamPlan, ...],
    params: tuple[ParamPlan, ...],
    sorting: SortingPlan | None,
) -> None:
    """Reject collisions across signature sections and generated control arguments."""
    names = [param.name for param in (*path_params, *params)]
    reserved: set[str] = {"max_results", "on_validation_error"} if settings.shape == "collection" else set()
    if sorting is not None:
        reserved.update({"sort", "order"})
    duplicates = sorted({name for name in names if names.count(name) > 1} | (set(names) & reserved))
    if duplicates:
        message = f"operation {settings.key!r} produces duplicate or reserved arguments {duplicates}"
        raise ValueError(message)


def _validate_unique_arguments(settings: OperationSettings, params: list[ParamPlan]) -> None:
    """Reject friendly-name collisions within one signature section."""
    names = [param.name for param in params]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        message = f"operation {settings.key!r} maps multiple wire params to {duplicates}"
        raise ValueError(message)


def _validate_argument_name(name: str, wire_name: str) -> None:
    """Require generated public arguments to be legal Python identifiers."""
    if name.isidentifier() and not keyword.iskeyword(name):
        return
    message = f"param {wire_name!r} maps to invalid Python argument {name!r}; apply @pythonParameter(name: ...)"
    raise ValueError(message)


def _is_date(schema: TypeIR | None) -> bool:
    """Whether a type has Smithy's projected date format."""
    if isinstance(schema, PrimitiveType):
        return schema.format == "date"
    if isinstance(schema, UnionType):
        return any(_is_date(option) for option in schema.options)
    return False


# TODO: Add more validation maybe? Whitespace etc.
def python_name(wire_name: str) -> str:
    """Convert a dotted wire param into the conventional Python spelling."""
    return wire_name.replace(".", "_").replace("-", "_")


def _snake_case(name: str) -> str:
    """Convert a Smithy UpperCamel or lowerCamel identifier to snake_case."""
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).replace("-", "_").lower()


def _operation_key(method_name: str, http_method: str) -> str:
    """Derive a concise operation module name without losing explicit names."""
    prefix = f"{http_method}_"
    return (
        method_name[len(prefix) :] if method_name.startswith(prefix) and len(method_name) > len(prefix) else method_name
    )


def _humanize_service_name(name: str) -> str:
    """Produce a readable vendor label for a generated public client docstring."""
    base = name.removesuffix("Service").removesuffix("Api") or name
    return _snake_case(base).replace("_", " ")


def _validate_target(target: TargetSettings) -> None:
    """Validate the small target-language configuration outside the Smithy model."""
    if not all(part.isidentifier() and not keyword.iskeyword(part) for part in target.package.split(".")):
        msg = f"package {target.package!r} must be an importable dotted Python name"
        raise ValueError(msg)
    if not target.client_name.isidentifier() or keyword.iskeyword(target.client_name):
        msg = f"client name {target.client_name!r} must be a valid Python identifier"
        raise ValueError(msg)


def tidy(text: str) -> str:
    """Collapse vendor prose onto one docstring line."""
    return re.sub(r"\s+", " ", text or "").strip()
