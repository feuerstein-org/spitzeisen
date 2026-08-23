"""
Compile protocol-neutral OpenAPI IR plus SDK policy into a rendering plan.

OpenAPI describes what goes over the wire. The manifest describes the SDK we want to expose:
method names, pagination, rate-limit cost, response shape, and friendly parameter names.
This module combines them.
"""

from __future__ import annotations

import keyword
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from spitzeisen.codegen.ir import (
    ArrayType,
    IntersectionType,
    LiteralType,
    ObjectType,
    ParameterIR,
    ParameterLocation,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    TypeIR,
    UnionType,
)
from spitzeisen.codegen.manifest import Param

if TYPE_CHECKING:
    from spitzeisen.codegen.ir import JSONScalar, JSONValue
    from spitzeisen.codegen.manifest import Endpoint, Manifest, QueryStyle
    from spitzeisen.codegen.parser import Endpoint as ParsedEndpoint
    from spitzeisen.codegen.parser import GeneratorData

type WireLocation = Literal["query", "header"]

_STRUCTURAL_PARAMETERS = {"cursor", "page", "offset"}
_QUERY_STYLES = {"form", "spaceDelimited", "pipeDelimited"}


def _fail(message: str) -> NoReturn:
    """Stop policy compilation with a plain generation failure."""
    raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ParameterPlan:
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
    default: str = "None"

    @property
    def type(self) -> str:
        """Compatibility spelling used by the Python templates."""
        return self.annotation


@dataclass(frozen=True, slots=True)
class PageSizePlan:
    """The query parameter controlling page size and its accepted maximum."""

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

    @property
    def type(self) -> str:
        """Compatibility spelling used by the Python templates."""
        return self.annotation


@dataclass(frozen=True, slots=True)
class SortingPlan:
    """The two public sorting arguments and their vendor representation."""

    style: Literal["suffix", "param"]
    sort: SortArgumentPlan
    order: SortArgumentPlan


@dataclass(frozen=True, slots=True)
class EndpointPlan:
    """All facts needed to render one endpoint, with no raw OpenAPI state."""

    key: str
    path: str
    method_name: str
    model: str
    summary: str
    docs_url: str | None
    generate_model: bool
    shape: Literal["collection", "single"]
    not_found: Literal["raise", "empty"]
    cost: float
    pagination: Literal["none", "page_number"]
    results_key: str | None
    params: tuple[ParameterPlan, ...]
    query_params: tuple[ParameterPlan, ...]
    header_params: tuple[ParameterPlan, ...]
    path_params: tuple[ParameterPlan, ...]
    sorting: SortingPlan | None
    page_size: PageSizePlan | None
    example_args: tuple[str, ...]
    model_imports: tuple[str, ...]
    coerce_function_imports: tuple[str, ...]
    helpers: tuple[str, ...]

    @property
    def class_name(self) -> str:
        """Return the endpoint API class name."""
        return "".join(part.title() for part in self.key.split("_")) + "Api"

    @property
    def accessor(self) -> str:
        """Return the aggregate client's property name."""
        return f"{self.key}_api"

    @property
    def const_name(self) -> str:
        """Return the generated endpoint-spec constant name."""
        return f"{self.key.upper()}_ENDPOINT"


@dataclass(frozen=True, slots=True)
class ClientPlan:
    """The complete, renderer-ready SDK plan."""

    vendor: str
    base_url: str
    package: str
    client_name: str
    endpoints: tuple[EndpointPlan, ...]


def compile_manifest(manifest: Manifest, openapi: GeneratorData) -> ClientPlan:
    """Combine a validated manifest with parsed OpenAPI generator data."""
    endpoints = tuple(
        _compile_endpoint(endpoint, _select_operation(endpoint, openapi)) for endpoint in manifest.endpoints.values()
    )
    _validate_public_names(endpoints)
    return ClientPlan(
        vendor=manifest.vendor,
        base_url=manifest.base_url,
        package=manifest.package,
        client_name=manifest.client_name,
        endpoints=endpoints,
    )


def _select_operation(endpoint: Endpoint, openapi: GeneratorData) -> ParsedEndpoint:
    """Select an operation and catch manifest/spec drift before rendering."""
    operation = (
        openapi.operation_named(endpoint.operation_id)
        if endpoint.operation_id is not None
        else openapi.operation_at(endpoint.path, endpoint.method)
    )
    if operation is None:
        selector = (
            f"operationId {endpoint.operation_id!r}"
            if endpoint.operation_id is not None
            else f"{endpoint.method.value.upper()} {endpoint.path}"
        )
        message = f"{selector} is absent from the OpenAPI document; the vendor may have moved or renamed it"
        _fail(message)
    if operation.path != endpoint.path or operation.method is not endpoint.method:
        expected = f"{endpoint.method.value.upper()} {endpoint.path}"
        actual = f"{operation.method.value.upper()} {operation.path}"
        message = f"operationId {endpoint.operation_id!r} resolves to {actual}, not the manifest's {expected}"
        _fail(message)
    if operation.method.value != "get":
        message = (
            f"{operation.method.value.upper()} operations are parsed but this runtime currently "
            "generates GET requests only"
        )
        _fail(message)
    if operation.request_body is not None:
        message = "GET request bodies are preserved by the compiler but are not supported by the runtime renderer"
        _fail(message)
    return operation


def _compile_endpoint(endpoint: Endpoint, operation: ParsedEndpoint) -> EndpointPlan:
    """Compile one endpoint's parameters and SDK policy."""
    _validate_policy_references(endpoint, operation)
    parameters = _parameters(endpoint, operation)
    path_parameters = _path_parameters(endpoint, operation)
    sorting = _sorting(endpoint, operation)
    page_size = _page_size(endpoint, operation)
    query_parameters = tuple(parameter for parameter in parameters if parameter.location == "query")
    header_parameters = tuple(parameter for parameter in parameters if parameter.location == "header")
    model_imports = _model_imports(endpoint, operation)
    calls = [parameter.coercion for parameter in (*parameters, *path_parameters)]
    calls.extend("require_value(" for parameter in header_parameters if parameter.required)
    if sorting is not None:
        calls.extend((sorting.sort.coercion, sorting.order.coercion))
        if sorting.style == "suffix":
            calls.append("coerce_sort(")
    example_args = [parameter.name for parameter in path_parameters]
    example_args.extend(parameter.name for parameter in parameters if parameter.required)
    _validate_signature_names(endpoint, path_parameters, parameters, sorting)
    return EndpointPlan(
        key=endpoint.key,
        path=endpoint.path,
        method_name=endpoint.method_name,
        model=endpoint.model,
        summary=endpoint.summary or operation.summary,
        docs_url=endpoint.docs_url,
        generate_model=endpoint.generate_model,
        shape=endpoint.shape,
        not_found=endpoint.not_found,
        cost=endpoint.cost,
        pagination=endpoint.pagination,
        results_key=endpoint.results_key,
        params=parameters,
        query_params=query_parameters,
        header_params=header_parameters,
        path_params=path_parameters,
        sorting=sorting,
        page_size=page_size,
        example_args=tuple(example_args),
        model_imports=model_imports,
        coerce_function_imports=_coerce_function_imports(endpoint),
        helpers=_helpers_used(calls, model_imports),
    )


def _parameters(endpoint: Endpoint, operation: ParsedEndpoint) -> tuple[ParameterPlan, ...]:
    """Build public query and header arguments."""
    sources = tuple(
        (parameter.wire_name, parameter.location.value, parameter.required, parameter)
        for parameter in operation.parameters
        if parameter.location is not ParameterLocation.PATH
    )

    structural = set(_STRUCTURAL_PARAMETERS)
    structural.update(
        item for item in (endpoint.page_size_param, endpoint.sort_param, endpoint.order_param) if item is not None
    )
    plans: list[ParameterPlan] = []
    for wire_name, raw_location, required_by_spec, specification in sources:
        if raw_location == ParameterLocation.COOKIE.value:
            _fail(f"parameter {wire_name!r} uses unsupported location 'cookie'")
        if raw_location not in {"query", "header"}:
            _fail(f"parameter {wire_name!r} uses unsupported location {raw_location!r}")
        location = cast("WireLocation", raw_location)
        if specification.content:
            _fail(f"content-based parameter {wire_name!r} is not supported by the runtime renderer")
        if specification.allow_reserved:
            _fail(f"query parameter {wire_name!r} sets allowReserved, which the runtime cannot serialize faithfully")
        if (location == "query" and wire_name in structural) or wire_name in endpoint.exclude_params:
            continue
        override = endpoint.params.get(wire_name) or _default_param()
        name = override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        required = required_by_spec or override.required is True
        style, explode = _query_serialization(override, specification) if location == "query" else ("form", True)
        default = _parameter_default(override, specification, required)
        schema = specification.schema
        plans.append(
            ParameterPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, schema),
                description=(override.description or tidy(specification.description) or f"Filter on `{wire_name}`."),
                coercion=_coercion(override, name, schema),
                location=location,
                style=style,
                explode=explode,
                required=required,
                default=default,
            ),
        )
    _validate_unique_arguments(endpoint, plans)
    return tuple(plans)


def _path_parameters(endpoint: Endpoint, operation: ParsedEndpoint) -> tuple[ParameterPlan, ...]:
    """Build positional path arguments in path-template order."""
    placeholders = [name for _, name, _, _ in string.Formatter().parse(endpoint.path) if name]
    specifications = {
        parameter.wire_name: parameter
        for parameter in operation.parameters
        if parameter.location is ParameterLocation.PATH
    }
    plans: list[ParameterPlan] = []
    for wire_name in placeholders:
        specification = specifications.get(wire_name)
        override = endpoint.params.get(wire_name) or _default_param()
        name = endpoint.path_params.get(wire_name) or override.name or python_name(wire_name)
        _validate_argument_name(name, wire_name)
        schema = specification.schema if specification is not None else None
        plans.append(
            ParameterPlan(
                name=name,
                wire_name=wire_name,
                annotation=_annotation(override, schema),
                description=(
                    override.description
                    or (tidy(specification.description) if specification is not None else "")
                    or f"Path parameter `{wire_name}`."
                ),
                coercion=_coercion(override, name, schema),
                required=True,
            ),
        )
    _validate_unique_arguments(endpoint, plans)
    return tuple(plans)


def _annotation(override: Param, schema: TypeIR | None) -> str:
    """Resolve a public input annotation from policy first, then protocol type."""
    if override.type:
        return override.type
    match override.coercion_style:
        case "date":
            return "str | date | datetime"
        case "comma_list":
            return "list[str]"
        case "comma_choice_list":
            return f"list[{override.literal}]"
        case "choice":
            return str(override.literal)
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


def _coercion(override: Param, name: str, schema: TypeIR | None) -> str:
    """Build the expression converting a public argument into a wire value."""
    if override.coerce_function:
        literal = override.literal or "None"
        return f'{override.coerce_function}({name}, param_name="{name}", literal={literal})'
    style = override.coercion_style
    if style == "plain" and _is_date(schema):
        style = "date"
    match style:
        case "date":
            return f'coerce_date({name}, "{name}")'
        case "comma_list":
            return f'",".join({name}) if {name} else None'
        case "comma_choice_list":
            return f'coerce_choices({name}, {override.literal}, "{name}")'
        case "choice":
            return f'coerce_choice({name}, {override.literal}, "{name}")'
        case _:
            return name


def _query_serialization(override: Param, specification: ParameterIR | None) -> tuple[QueryStyle, bool]:
    """Resolve OpenAPI query serialization, with explicit manifest overrides."""
    raw_style = (specification.style or "form") if specification is not None else "form"
    style = override.style or raw_style
    if style not in _QUERY_STYLES:
        _fail(f"query parameter style {style!r} is unsupported")
    explode = (
        override.explode
        if override.explode is not None
        else (specification.explode if specification is not None else style == "form")
    )
    return cast("QueryStyle", style), explode


def _parameter_default(override: Param, specification: ParameterIR | None, required: bool) -> str:
    """Choose the Python default without weakening required OpenAPI parameters."""
    if required:
        return "None"
    if override.default is not None:
        return override.default
    if specification is not None and specification.has_default:
        return repr(specification.default)
    return "None"


def _sorting(endpoint: Endpoint, operation: ParsedEndpoint) -> SortingPlan | None:
    """Compile the manifest's sorting policy against its named OpenAPI parameters."""
    if endpoint.sort_style == "none":
        return None
    assert endpoint.sort_param is not None  # noqa: S101 - manifest validation guarantees it
    sort_parameter = _named_query_parameter(operation, endpoint.sort_param, "sorting")
    order_parameter = (
        _named_query_parameter(operation, endpoint.order_param, "sorting") if endpoint.order_param is not None else None
    )
    sort_default = sort_parameter.default if sort_parameter is not None and sort_parameter.has_default else None
    order_default = order_parameter.default if order_parameter is not None and order_parameter.has_default else None
    sort_schema = sort_parameter.schema if sort_parameter is not None else None
    order_schema = order_parameter.schema if order_parameter is not None else None
    if endpoint.sort_style == "suffix":
        sort_default, suffix_default = _split_suffix_default(sort_default)
        if order_default is None:
            order_default = suffix_default
        sort_schema, order_schema = _split_suffix_literals(sort_schema, order_schema)
    if endpoint.sort_default is not None:
        sort_default = endpoint.sort_default
    if endpoint.order_default is not None:
        order_default = endpoint.order_default
    if sort_default is None:
        _fail(f"sorting parameter {endpoint.sort_param!r} needs a default in OpenAPI or `sort_default`")
    if order_default is None:
        source = endpoint.order_param or f"the suffix on {endpoint.sort_param!r}"
        _fail(f"sorting direction {source!r} needs a default in OpenAPI or `order_default`")
    sort_override = endpoint.params.get(endpoint.sort_param)
    order_override = endpoint.params.get(endpoint.order_param) if endpoint.order_param is not None else None
    return SortingPlan(
        style=endpoint.sort_style,
        sort=_sort_argument(
            name="sort",
            wire_name=endpoint.sort_param,
            default=sort_default,
            schema=sort_schema,
            specification=sort_parameter,
            override=sort_override or _default_param(),
            explicit_literal=endpoint.sort_literal,
        ),
        order=_sort_argument(
            name="order",
            wire_name=endpoint.order_param,
            default=order_default,
            schema=order_schema,
            specification=order_parameter,
            override=order_override or _default_param(),
            explicit_literal=endpoint.order_literal,
        ),
    )


def _named_query_parameter(
    operation: ParsedEndpoint,
    wire_name: str | None,
    purpose: str,
) -> ParameterIR | None:
    """Find a policy-named parameter and report manifest/spec drift."""
    if wire_name is None:
        return None
    matching = next(
        (
            parameter
            for parameter in operation.parameters
            if parameter.location is ParameterLocation.QUERY and parameter.wire_name == wire_name
        ),
        None,
    )
    if matching is None:
        _fail(f"{purpose} parameter {wire_name!r} is absent from the OpenAPI operation")
    return matching


def _sort_argument(
    *,
    name: str,
    wire_name: str | None,
    default: JSONValue,
    schema: TypeIR | None,
    specification: ParameterIR | None,
    override: Param,
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
        annotation=explicit_literal or _annotation(override, schema),
        default=default,
        coercion=expression,
        style=style,
        explode=explode,
    )


def _split_suffix_default(value: JSONValue) -> tuple[JSONValue, str | None]:
    """Split a `field.direction` default without assuming direction vocabulary."""
    if value is None:
        return None, None
    text = str(value)
    if "." not in text:
        return text, None
    field, direction = text.rsplit(".", 1)
    return field, direction


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


def _page_size(endpoint: Endpoint, operation: ParsedEndpoint) -> PageSizePlan | None:
    """Compile page-size policy and its schema constraint."""
    if endpoint.page_size_param is None:
        return None
    matching = _named_query_parameter(operation, endpoint.page_size_param, "page-size")
    maximum = endpoint.max_page_size
    if maximum is None and matching is not None and isinstance(matching.schema, PrimitiveType):
        schema_maximum = matching.schema.maximum
        maximum = int(schema_maximum) if schema_maximum is not None else None
    if maximum is None:
        _fail(f"page-size parameter {endpoint.page_size_param!r} needs `max_page_size` or an OpenAPI maximum")
    return PageSizePlan(endpoint.page_size_param, maximum)


def _model_imports(endpoint: Endpoint, operation: ParsedEndpoint) -> tuple[str, ...]:
    """Collect model-owned aliases referenced by generated annotations or coercion."""
    names = {
        name
        for name in (
            endpoint.sort_literal,
            endpoint.order_literal,
            *(parameter.literal for parameter in endpoint.params.values()),
        )
        if name
    }
    for parameter in operation.parameters:
        names.update(_reference_names(parameter.schema))
    names.discard(endpoint.model)
    return tuple(sorted(names))


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


def _coerce_function_imports(endpoint: Endpoint) -> tuple[str, ...]:
    """Collect client-owned coercion functions referenced by the manifest."""
    return tuple(
        sorted(
            {
                parameter.coerce_function
                for parameter in endpoint.params.values()
                if parameter.coerce_function is not None
            },
        ),
    )


def _helpers_used(calls: list[str], model_imports: tuple[str, ...]) -> tuple[str, ...]:
    """Collect framework helpers actually called by one endpoint."""
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


def _validate_public_names(endpoints: tuple[EndpointPlan, ...]) -> None:
    """Reject aggregate-client property and class collisions."""
    for attribute in ("accessor", "class_name", "const_name"):
        values = [getattr(endpoint, attribute) for endpoint in endpoints]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            _fail(f"endpoint keys produce duplicate {attribute.replace('_', ' ')} values: {duplicates}")

    model_names = [endpoint.model for endpoint in endpoints if endpoint.generate_model]
    duplicate_models = sorted({name for name in model_names if model_names.count(name) > 1})
    if duplicate_models:
        message = (
            f"multiple generated endpoints use public response models {duplicate_models}; "
            "give each extension module an unambiguous model"
        )
        _fail(message)


def _validate_policy_references(endpoint: Endpoint, operation: ParsedEndpoint) -> None:
    """Catch manifest keys that would otherwise be silently ignored."""
    placeholders = set(_path_parameter_names(endpoint.path))
    unknown_path_names = sorted(set(endpoint.path_params) - placeholders)
    if unknown_path_names:
        _fail(f"path parameter mappings {unknown_path_names} do not occur in {endpoint.path!r}")
    available = {parameter.wire_name for parameter in operation.parameters}
    unknown_overrides = sorted(set(endpoint.params) - available)
    if unknown_overrides:
        _fail(f"parameter overrides {unknown_overrides} are absent from the selected OpenAPI operation")
    unknown_exclusions = sorted(set(endpoint.exclude_params) - available)
    if unknown_exclusions:
        _fail(f"excluded parameters {unknown_exclusions} are absent from the selected OpenAPI operation")


def _validate_signature_names(
    endpoint: Endpoint,
    path_parameters: tuple[ParameterPlan, ...],
    parameters: tuple[ParameterPlan, ...],
    sorting: SortingPlan | None,
) -> None:
    """Reject collisions across signature sections and generated control arguments."""
    names = [parameter.name for parameter in (*path_parameters, *parameters)]
    reserved: set[str] = {"max_results", "on_validation_error"} if endpoint.shape == "collection" else set()
    if sorting is not None:
        reserved.update({"sort", "order"})
    duplicates = sorted({name for name in names if names.count(name) > 1} | (set(names) & reserved))
    if duplicates:
        _fail(f"endpoint {endpoint.key!r} produces duplicate or reserved arguments {duplicates}")


def _validate_unique_arguments(endpoint: Endpoint, parameters: list[ParameterPlan]) -> None:
    """Reject friendly-name collisions within one signature section."""
    names = [parameter.name for parameter in parameters]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        _fail(f"endpoint {endpoint.key!r} maps multiple wire parameters to {duplicates}")


def _validate_argument_name(name: str, wire_name: str) -> None:
    """Require generated public arguments to be legal Python identifiers."""
    if name.isidentifier() and not keyword.iskeyword(name):
        return
    _fail(f"parameter {wire_name!r} maps to invalid Python argument {name!r}; configure a friendly `name`")


def _default_param() -> Param:
    """Create the neutral manifest override without a module-level mutable model."""
    return Param()


def _is_date(schema: TypeIR | None) -> bool:
    """Whether a type has OpenAPI's date format."""
    if isinstance(schema, PrimitiveType):
        return schema.format == "date"
    if isinstance(schema, UnionType):
        return any(_is_date(option) for option in schema.options)
    return False


def python_name(wire_name: str) -> str:
    """Convert a dotted wire parameter into the conventional Python spelling."""
    return wire_name.replace(".", "_").replace("-", "_")


def tidy(text: str) -> str:
    """Collapse vendor prose onto one docstring line."""
    return re.sub(r"\s+", " ", text or "").strip()


def _path_parameter_names(path: str) -> tuple[str, ...]:
    """Return placeholder names from an endpoint path."""
    return tuple(name for _, name, _, _ in string.Formatter().parse(path) if name)
