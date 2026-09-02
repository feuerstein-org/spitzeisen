"""Lower target-neutral Smithy semantics into renderer-ready Python facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from spitzeisen.codegen.python_context import PythonGenerationContext, PythonSettings, create_generation_context
from spitzeisen.codegen.python_plan import (
    PythonCoercionPlan,
    PythonDefaultPlan,
    PythonNotFound,
    PythonOperationPlan,
    PythonPageSizePlan,
    PythonPaginationStyle,
    PythonParameterPlan,
    PythonPlan,
    PythonQueryStyle,
    PythonResponseCardinality,
    PythonSortArgumentPlan,
    PythonSortingPlan,
    PythonTypePlan,
    PythonWireLocation,
)
from spitzeisen.codegen.python_protocols import ProtocolSerializationError
from spitzeisen.codegen.python_symbols import PYTHON_MODEL_FIELD_TRAIT, PYTHON_OPERATION_TRAIT, snake_case

if TYPE_CHECKING:
    from spitzeisen.codegen.python_integrations import PythonIntegration
    from spitzeisen.codegen.python_protocols import PythonProtocolRegistry
    from spitzeisen.codegen.service_plan import (
        HttpBindingPlan,
        JSONValue,
        MemberPlan,
        OperationPlan,
        ServicePlan,
        ShapePlan,
    )

_QUERY_STYLES: tuple[PythonQueryStyle, ...] = ("form", "spaceDelimited", "pipeDelimited")
_SUPPORTED_REQUEST_LOCATIONS = frozenset({"header", "label", "query", "query_params"})


class PythonLoweringError(ValueError):
    """The Python target cannot represent valid neutral Smithy semantics."""


def lower_service_plan(
    plan: ServicePlan,
    settings: PythonSettings,
    *,
    available_integrations: tuple[PythonIntegration, ...] = (),
    protocol_registry: PythonProtocolRegistry | None = None,
) -> PythonPlan:
    """Resolve Python settings, extensions, symbols, protocol, and renderer capabilities."""
    context = create_generation_context(
        plan,
        settings,
        available_integrations=available_integrations,
        protocol_registry=protocol_registry,
    )
    referenced_shapes = {
        *context.service_plan.shapes,
        *(member.target for shape in context.service_plan.shapes.values() for member in shape.members),
    }
    missing_external_shapes = set(settings.external_models).difference(referenced_shapes)
    if missing_external_shapes:
        msg = f"Python external models reference shapes absent from ServicePlan: {sorted(missing_external_shapes)}"
        raise PythonLoweringError(msg)

    context.symbols.allocator.allocate("close", key="runtime:close", scope="operation_methods")
    operations: list[PythonOperationPlan] = []
    response_shapes: list[str] = []
    for operation_id in context.service_plan.service.operations:
        operation = context.service_plan.operation(operation_id)
        if _policy_flag(operation.policies, "exclude_operation", operation.id):
            continue
        lowered = _lower_operation(context, operation)
        operations.append(lowered)
        if lowered.generate_model and lowered.response_shape not in response_shapes:
            response_shapes.append(lowered.response_shape)
    if not operations:
        msg = f"service {context.service_plan.service.id} contains no Python-visible operations"
        raise PythonLoweringError(msg)

    return PythonPlan(
        service_id=context.service_plan.service.id,
        protocol_id=context.protocol.trait_id,
        vendor=settings.vendor or _default_vendor(context.service_plan.service.id),
        package=settings.package,
        client_name=settings.client_name,
        operations=tuple(operations),
        response_shapes=tuple(response_shapes),
        model_aliases=_model_aliases(context),
        dependencies=tuple(sorted(context.dependencies)),
    )


def _lower_operation(  # noqa: C901, PLR0912, PLR0915
    context: PythonGenerationContext,
    operation: OperationPlan,
) -> PythonOperationPlan:
    if operation.event_streams is not None:
        msg = f"Python runtime does not support event streams required by {operation.id}"
        raise PythonLoweringError(msg)
    http = operation.http
    if http is None:
        msg = f"Python runtime requires an HTTP binding for operation {operation.id}"
        raise PythonLoweringError(msg)
    if http.method.upper() != "GET":
        msg = f"Python runtime supports GET only; {operation.id} uses {http.method.upper()}"
        raise PythonLoweringError(msg)
    locations = {binding.location for binding in http.request_bindings}
    unsupported = sorted(locations.difference(_SUPPORTED_REQUEST_LOCATIONS))
    if unsupported:
        msg = f"Python runtime cannot serialize request bindings {unsupported} on {operation.id}"
        raise PythonLoweringError(msg)
    if "smithy_pagination" in operation.policies:
        msg = f"Python runtime does not yet support standard Smithy pagination on {operation.id}"
        raise PythonLoweringError(msg)

    python = _extension(operation.extensions, PYTHON_OPERATION_TRAIT, operation.id)
    preferred_module = _optional_string(python, "module") or snake_case(_shape_name(operation.id))
    key = context.symbols.allocator.allocate(
        preferred_module,
        key=f"operation-module:{operation.id}",
        scope="operation_modules",
    )
    preferred_method = _optional_string(python, "method") or snake_case(_shape_name(operation.id))
    method_name = context.symbols.allocator.allocate(
        preferred_method,
        key=f"operation-method:{operation.id}",
        scope="operation_methods",
    )
    class_name = context.symbols.allocator.allocate(
        f"{key}_api",
        key=f"operation-class:{operation.id}",
        scope="operation_classes",
        style="pascal",
    )
    preferred_accessor = _optional_string(python, "accessor") or f"{key}_api"
    accessor = context.symbols.allocator.allocate(
        preferred_accessor,
        key=f"operation-accessor:{operation.id}",
        scope="client_attributes",
    )

    response_shape, cardinality, results_key = _response_selection(context, operation)
    model_symbol = context.symbols.to_shape_symbol(response_shape)
    context.dependencies.update(model_symbol.dependencies)
    generate_model = response_shape not in context.settings.external_models

    input_shape = context.service_plan.shape(operation.input)
    bound = _bound_input_members(input_shape, http.request_bindings, operation.id)
    page_policy = _policy(operation.policies, "page_number_pagination", operation.id)
    sorting_policy = _policy(operation.policies, "sorting", operation.id)
    structural_ids = _structural_member_ids(bound, page_policy, sorting_policy, operation.id)

    parameter_scope = f"parameters:{operation.id}"
    synthetic_keys = {
        "max_results": f"synthetic:{operation.id}:max_results",
        "on_validation_error": f"synthetic:{operation.id}:on_validation_error",
        "order": f"sorting:{operation.id}:order",
        "sort": f"sorting:{operation.id}:sort",
    }
    for synthetic, identity in synthetic_keys.items():
        context.symbols.allocator.allocate(
            synthetic,
            key=identity,
            scope=parameter_scope,
        )
    parameters: list[PythonParameterPlan] = []
    bindings_by_member = {binding.member_id: binding for binding in http.request_bindings}
    for member in input_shape.members:
        binding = bindings_by_member.get(member.id)
        if binding is None or member.id in structural_ids:
            continue
        if _policy_flag(member.policies, "exclude_parameter", member.id):
            if binding.location == "label":
                msg = f"excluded parameter {member.id} is required to expand an HTTP path label"
                raise PythonLoweringError(msg)
            if member.required and not member.client_nullable:
                msg = f"excluded parameter {member.id} is a required non-nullable HTTP input"
                raise PythonLoweringError(msg)
            continue
        parameters.append(_lower_parameter(context, member, binding, parameter_scope))
    path_params = tuple(parameter for parameter in parameters if parameter.location == "path")
    query_params = tuple(parameter for parameter in parameters if parameter.location == "query")
    header_params = tuple(parameter for parameter in parameters if parameter.location == "header")
    public_params = (*query_params, *header_params)

    pagination, page_param, page_start, page_step, page_size = _lower_page_number(
        context,
        operation,
        bound,
        page_policy,
    )
    sorting = _lower_sorting(context, operation, bound, sorting_policy, parameter_scope)
    if sorting is not None and cardinality != "collection":
        msg = f"operation {operation.id} cannot combine a single response with sorting"
        raise PythonLoweringError(msg)

    example_arguments = [
        parameter.name for parameter in (*path_params, *public_params) if parameter.default.is_required
    ]
    if sorting is not None:
        example_arguments.extend(
            argument.name for argument in (sorting.sort, sorting.order) if argument.default.is_required
        )
    not_found_policy = _policy(operation.policies, "not_found", operation.id)
    not_found = _choice(not_found_policy, "behavior", ("absent", "raise"), default="raise")
    cost_policy = _policy(operation.policies, "rate_limit_cost", operation.id)
    cost = _optional_number(cost_policy, "units", default=1.0)
    if cost <= 0:
        msg = f"rate_limit_cost.units on {operation.id} must be greater than zero"
        raise PythonLoweringError(msg)
    errors = tuple(context.symbols.to_shape_symbol(shape_id).name for shape_id in operation.errors)
    return PythonOperationPlan(
        shape_id=operation.id,
        key=key,
        path=_python_uri(operation),
        method_name=method_name,
        class_name=class_name,
        accessor=accessor,
        const_name=f"{key.upper()}_OPERATION",
        response_shape=response_shape,
        model=model_symbol.name,
        model_module=model_symbol.module,
        summary=operation.documentation or f"Call {method_name}.",
        docs_url=_documentation_url(operation.external_documentation),
        generate_model=generate_model,
        response_cardinality=cardinality,
        not_found=cast("PythonNotFound", not_found),
        cost=cost,
        pagination=pagination,
        results_key=results_key,
        page_param=page_param,
        page_start=page_start,
        page_step=page_step,
        params=public_params,
        query_params=query_params,
        header_params=header_params,
        path_params=path_params,
        sorting=sorting,
        page_size=page_size,
        smithy_pagination=None,
        example_args=tuple(example_arguments),
        errors=errors,
    )


def _lower_parameter(
    context: PythonGenerationContext,
    member: MemberPlan,
    binding: HttpBindingPlan,
    scope: str,
) -> PythonParameterPlan:
    name = context.symbols.to_member_name(member, scope=scope)
    python_type = context.symbols.to_parameter_type(member.target)
    target = context.service_plan.shapes.get(member.target)
    adapter_policy = _policy(member.policies, "input_adapter", member.id)
    if adapter_policy:
        adapter_id = _required_string(adapter_policy, "id", f"input adapter on {member.id}")
        try:
            adapter = context.settings.input_adapters[adapter_id]
        except KeyError:
            msg = f"Python input adapter {adapter_id!r} required by {member.id} is not registered"
            raise PythonLoweringError(msg) from None
        python_type = adapter.public_type
        coercion = PythonCoercionPlan("custom", function=adapter.function)
        context.dependencies.update(adapter.dependencies)
    else:
        try:
            coercion = context.protocol.parameter_coercion(
                context.service_plan,
                member,
                target,
                binding,
            )
        except ProtocolSerializationError as error:
            raise PythonLoweringError(str(error)) from error
        if python_type.kind == "literal":
            coercion = PythonCoercionPlan("choice", literal_type=python_type)
        elif (
            python_type.kind in {"list", "set"}
            and len(python_type.members) == 1
            and python_type.members[0].kind == "literal"
        ):
            coercion = PythonCoercionPlan("choices", literal_type=python_type.members[0])

    default = _parameter_default(member)
    style, explode, allow_reserved = _query_encoding(member, target, binding)
    location_by_binding: dict[str, PythonWireLocation] = {
        "header": "header",
        "label": "path",
        "query": "query",
        "query_params": "query",
    }
    try:
        location = location_by_binding[binding.location]
    except KeyError:
        msg = f"unsupported Python request binding {binding.location!r} on {member.id}"
        raise PythonLoweringError(msg) from None
    return PythonParameterPlan(
        member_id=member.id,
        name=name,
        wire_name=binding.name,
        type=python_type,
        description=member.documentation or f"Value for {binding.name}.",
        coercion=coercion,
        location=location,
        greedy=binding.greedy,
        style=style,
        explode=explode,
        allow_reserved=allow_reserved,
        required=default.is_required,
        default=default,
    )


def _parameter_default(member: MemberPlan) -> PythonDefaultPlan:
    configured = _policy(member.policies, "client_default", member.id)
    if configured:
        if "value" not in configured:
            msg = f"client_default on {member.id} must preserve an explicit value, including null"
            raise PythonLoweringError(msg)
        return PythonDefaultPlan("value", configured["value"])
    if member.default.present:
        return PythonDefaultPlan("value", member.default.value)
    if not member.client_nullable:
        return PythonDefaultPlan("required")
    return PythonDefaultPlan("value", None)


def _response_selection(  # noqa: C901, PLR0912, PLR0915
    context: PythonGenerationContext,
    operation: OperationPlan,
) -> tuple[str, PythonResponseCardinality, str | None]:
    output = context.service_plan.shape(operation.output)
    payloads = (
        ()
        if operation.http is None
        else tuple(binding for binding in operation.http.response_bindings if binding.location == "payload")
    )
    if len(payloads) > 1:
        msg = f"operation {operation.id} has multiple HTTP response payload bindings"
        raise PythonLoweringError(msg)
    payload_member = None if not payloads else _member_by_id(output, payloads[0].member_id)

    result = _policy(operation.policies, "result", operation.id)
    configured_path = _string_array(result, "path", owner=f"result on {operation.id}") if result else ()
    traversed: list[MemberPlan] = []
    selected_shape_id = output.id
    selected_shape: ShapePlan | None = output
    if configured_path:
        for member_name in configured_path:
            if selected_shape is None:
                msg = (
                    f"result path on {operation.id} cannot traverse {member_name!r} through "
                    f"scalar shape {selected_shape_id!r}"
                )
                raise PythonLoweringError(msg)
            try:
                member = selected_shape.member(member_name)
            except KeyError as error:
                msg = f"result path member {member_name!r} is absent from {selected_shape.id}"
                raise PythonLoweringError(msg) from error
            traversed.append(member)
            selected_shape_id = member.target
            selected_shape = context.service_plan.shapes.get(member.target)
    elif payload_member is not None:
        traversed.append(payload_member)
        selected_shape_id = payload_member.target
        selected_shape = context.service_plan.shapes.get(payload_member.target)

    runtime_path = list(traversed)
    if payload_member is not None:
        if not runtime_path or runtime_path[0].id != payload_member.id:
            msg = f"result path on {operation.id} does not begin at HTTP payload member {payload_member.id}"
            raise PythonLoweringError(msg)
        runtime_path.pop(0)
    if len(runtime_path) > 1:
        path = [member.name for member in runtime_path]
        msg = f"Python runtime cannot extract nested result path {path!r} on {operation.id}"
        raise PythonLoweringError(msg)
    results_key = None if not runtime_path else _json_name(runtime_path[0])

    selected_kind = None if selected_shape is None else selected_shape.kind
    inferred: PythonResponseCardinality = "collection" if selected_kind in {"list", "set"} else "single"
    cardinality = _choice(result, "cardinality", ("collection", "single"), default=inferred)
    if selected_shape is not None and selected_shape.kind in {"list", "set"} and cardinality != "collection":
        msg = f"result cardinality on {operation.id} contradicts collection shape {selected_shape.id}"
        raise PythonLoweringError(msg)
    response_shape = selected_shape_id
    if selected_shape is not None and selected_shape.kind in {"list", "set"}:
        if len(selected_shape.members) != 1:
            msg = f"response collection {selected_shape.id} must have exactly one member"
            raise PythonLoweringError(msg)
        response_shape = selected_shape.members[0].target
    elif cardinality == "collection" and selected_kind != "document":
        msg = f"result cardinality on {operation.id} declares non-collection shape {selected_shape_id} as a collection"
        raise PythonLoweringError(msg)
    if (
        selected_shape is not None
        and selected_shape.kind == "document"
        and response_shape not in context.settings.external_models
    ):
        msg = f"document result {selected_shape.id} on {operation.id} requires a configured Python external model"
        raise PythonLoweringError(msg)
    if response_shape not in context.settings.external_models:
        response_definition = context.service_plan.shapes.get(response_shape)
        response_kind = "Smithy prelude scalar" if response_definition is None else response_definition.kind
        if response_definition is None or response_definition.kind != "structure":
            msg = (
                f"Python renderer requires a structure response model for {operation.id}; "
                f"result value shape {response_shape!r} is {response_kind!r}. "
                "Configure an external Python model for this shape or install a target integration that supports it."
            )
            raise PythonLoweringError(msg)
    return response_shape, cast("PythonResponseCardinality", cardinality), results_key


def _bound_input_members(
    input_shape: ShapePlan,
    bindings: tuple[HttpBindingPlan, ...],
    operation_id: str,
) -> dict[str, tuple[MemberPlan, HttpBindingPlan]]:
    result: dict[str, tuple[MemberPlan, HttpBindingPlan]] = {}
    for binding in bindings:
        member = _member_by_id(input_shape, binding.member_id)
        if member.id in result:
            msg = f"operation {operation_id} binds input member {member.id} more than once"
            raise PythonLoweringError(msg)
        result[member.id] = (member, binding)
    return result


def _structural_member_ids(
    bound: dict[str, tuple[MemberPlan, HttpBindingPlan]],
    page: dict[str, JSONValue],
    sorting: dict[str, JSONValue],
    operation_id: str,
) -> set[str]:
    result: set[str] = set()
    for key in ("page_member", "page_size_member"):
        reference = _optional_string(page, key)
        if reference is not None:
            result.add(_bound_member_by_id(bound, reference, operation_id, "pagination")[0].id)
    for key in ("sort_member", "order_member"):
        reference = _optional_string(sorting, key)
        if reference is not None:
            result.add(_bound_member_by_id(bound, reference, operation_id, "sorting")[0].id)
    return result


def _lower_page_number(
    context: PythonGenerationContext,
    operation: OperationPlan,
    bound: dict[str, tuple[MemberPlan, HttpBindingPlan]],
    page: dict[str, JSONValue],
) -> tuple[PythonPaginationStyle, str | None, int, int, PythonPageSizePlan | None]:
    if not page:
        return "none", None, 1, 1, None
    page_reference = _required_string(page, "page_member", f"pagination on {operation.id}")
    _, page_binding = _bound_member_by_id(bound, page_reference, operation.id, "pagination")
    _require_query_binding(page_binding, operation.id, "page-number member")
    page_start = cast("int", _optional_integer(page, "start", default=1))
    page_step = cast("int", _optional_integer(page, "step", default=1))
    if page_step < 1:
        msg = f"pagination step on {operation.id} must be at least one"
        raise PythonLoweringError(msg)
    page_size: PythonPageSizePlan | None = None
    size_reference = _optional_string(page, "page_size_member")
    if size_reference is None:
        return "page_number", page_binding.name, page_start, page_step, None
    size_member, size_binding = _bound_member_by_id(bound, size_reference, operation.id, "pagination")
    _require_query_binding(size_binding, operation.id, "page-size member")
    maximum = _range_maximum(context, size_member)
    if maximum is None:
        msg = f"pagination page-size member {size_member.id} needs smithy.api#range.max"
        raise PythonLoweringError(msg)
    if maximum < 1:
        msg = f"pagination maximum on {operation.id} must be positive"
        raise PythonLoweringError(msg)
    page_size = PythonPageSizePlan(size_binding.name, maximum)
    return "page_number", page_binding.name, page_start, page_step, page_size


def _lower_sorting(
    context: PythonGenerationContext,
    operation: OperationPlan,
    bound: dict[str, tuple[MemberPlan, HttpBindingPlan]],
    policy: dict[str, JSONValue],
    scope: str,
) -> PythonSortingPlan | None:
    if not policy:
        return None
    encoding = _choice(policy, "encoding", ("separate", "suffix"), default=None)
    sort_reference = _required_string(policy, "sort_member", f"sorting on {operation.id}")
    sort_member, sort_binding = _bound_member_by_id(bound, sort_reference, operation.id, "sorting")
    _require_query_binding(sort_binding, operation.id, "sort member")
    sort_name = context.symbols.allocator.allocate("sort", key=f"sorting:{operation.id}:sort", scope=scope)
    order_name = context.symbols.allocator.allocate("order", key=f"sorting:{operation.id}:order", scope=scope)
    sort_style, sort_explode, sort_allow_reserved = _query_encoding(
        sort_member,
        context.service_plan.shapes.get(sort_member.target),
        sort_binding,
    )

    if encoding == "suffix":
        if _optional_string(policy, "order_member") is not None:
            msg = f"suffix sorting on {operation.id} cannot bind a separate order member"
            raise PythonLoweringError(msg)
        separator = _optional_string(policy, "separator") or "."
        if not separator:
            msg = f"suffix sorting separator on {operation.id} cannot be empty"
            raise PythonLoweringError(msg)
        values = _literal_values(context.service_plan.shape(sort_member.target))
        pairs = [_split_sort_value(value, separator, sort_member.id) for value in values]
        sort_values = tuple(dict.fromkeys(item[0] for item in pairs))
        order_values = tuple(dict.fromkeys(item[1] for item in pairs))
        combined_default = _parameter_default(sort_member)
        if combined_default.is_required or not isinstance(combined_default.value, str):
            msg = f"suffix sorting on {operation.id} needs a string client or Smithy default"
            raise PythonLoweringError(msg)
        default_sort, default_order = _split_sort_value(combined_default.value, separator, sort_member.id)
        sort_type = PythonTypePlan("literal", values=sort_values)
        order_type = PythonTypePlan("literal", values=order_values)
        return PythonSortingPlan(
            style="suffix",
            separator=separator,
            sort=PythonSortArgumentPlan(
                name=sort_name,
                wire_name=sort_binding.name,
                type=sort_type,
                default=PythonDefaultPlan("value", default_sort),
                coercion=PythonCoercionPlan("choice", literal_type=sort_type),
                style=sort_style,
                explode=sort_explode,
                allow_reserved=sort_allow_reserved,
            ),
            order=PythonSortArgumentPlan(
                name=order_name,
                wire_name=None,
                type=order_type,
                default=PythonDefaultPlan("value", default_order),
                coercion=PythonCoercionPlan("choice", literal_type=order_type),
            ),
        )

    order_reference = _required_string(policy, "order_member", f"sorting on {operation.id}")
    order_member, order_binding = _bound_member_by_id(bound, order_reference, operation.id, "sorting")
    _require_query_binding(order_binding, operation.id, "sort-order member")
    sort_default = _parameter_default(sort_member)
    order_default = _parameter_default(order_member)
    sort_type = context.symbols.to_parameter_type(sort_member.target)
    order_type = context.symbols.to_parameter_type(order_member.target)
    order_style, order_explode, order_allow_reserved = _query_encoding(
        order_member,
        context.service_plan.shapes.get(order_member.target),
        order_binding,
    )
    return PythonSortingPlan(
        style="separate",
        separator=None,
        sort=PythonSortArgumentPlan(
            name=sort_name,
            wire_name=sort_binding.name,
            type=sort_type,
            default=sort_default,
            coercion=_choice_coercion(sort_type),
            style=sort_style,
            explode=sort_explode,
            allow_reserved=sort_allow_reserved,
        ),
        order=PythonSortArgumentPlan(
            name=order_name,
            wire_name=order_binding.name,
            type=order_type,
            default=order_default,
            coercion=_choice_coercion(order_type),
            style=order_style,
            explode=order_explode,
            allow_reserved=order_allow_reserved,
        ),
    )


def _choice_coercion(python_type: PythonTypePlan) -> PythonCoercionPlan:
    return (
        PythonCoercionPlan("choice", literal_type=python_type)
        if python_type.kind == "literal"
        else PythonCoercionPlan("identity")
    )


def _literal_values(shape: ShapePlan) -> tuple[str, ...]:
    values = tuple(value.value for value in shape.enum_values)
    if shape.kind != "enum" or not values or not all(isinstance(value, str) for value in values):
        msg = f"suffix sorting shape {shape.id} must be an enum with string values"
        raise PythonLoweringError(msg)
    return cast("tuple[str, ...]", values)


def _split_sort_value(value: str, separator: str, member_id: str) -> tuple[str, str]:
    if separator not in value:
        msg = f"suffix sorting value {value!r} on {member_id} must contain separator {separator!r}"
        raise PythonLoweringError(msg)
    field, direction = value.rsplit(separator, maxsplit=1)
    if not field or not direction:
        msg = f"invalid suffix sorting value {value!r} on {member_id}"
        raise PythonLoweringError(msg)
    return field, direction


def _query_encoding(
    member: MemberPlan,
    target: ShapePlan | None,
    binding: HttpBindingPlan,
) -> tuple[PythonQueryStyle, bool, bool]:
    policy = _policy(member.policies, "query_encoding", member.id)
    if not policy:
        if binding.location == "query_params" and (target is None or target.kind != "map"):
            msg = f"HTTP query-params binding on {member.id} must target a map shape"
            raise PythonLoweringError(msg)
        # Smithy HTTP lists use repeated names, while query-params maps expose each
        # map key as a name. Both are OpenAPI ``form`` + ``explode`` semantics.
        return "form", True, False
    if binding.location not in {"query", "query_params"}:
        msg = f"query_encoding on {member.id} requires a query binding, got {binding.location!r}"
        raise PythonLoweringError(msg)
    style_value = policy.get("style", "form")
    if style_value == "deepObject":
        msg = f"Python runtime does not support deepObject query encoding on {member.id}"
        raise PythonLoweringError(msg)
    if style_value not in _QUERY_STYLES:
        msg = f"query_encoding style on {member.id} must be one of {_QUERY_STYLES} or 'deepObject'"
        raise PythonLoweringError(msg)
    explode = _optional_boolean(policy, "explode", default=True)
    allow_reserved = _optional_boolean(policy, "allow_reserved", default=False)
    if allow_reserved:
        msg = f"Python runtime cannot preserve reserved query characters requested by {member.id}"
        raise PythonLoweringError(msg)
    return style_value, explode, allow_reserved


def _bound_member_by_id(
    bound: dict[str, tuple[MemberPlan, HttpBindingPlan]],
    member_id: str,
    operation_id: str,
    policy: str,
) -> tuple[MemberPlan, HttpBindingPlan]:
    try:
        return bound[member_id]
    except KeyError:
        msg = f"{policy} member {member_id!r} on {operation_id} is not an HTTP-bound input member"
        raise PythonLoweringError(msg) from None


def _member_by_id(shape: ShapePlan, member_id: str) -> MemberPlan:
    try:
        return shape.member(member_id)
    except KeyError as error:
        msg = f"HTTP binding references {member_id!r}, which is absent from {shape.id}"
        raise PythonLoweringError(msg) from error


def _range_maximum(context: PythonGenerationContext, member: MemberPlan) -> int | None:
    candidates = [(member.constraints, member.id)]
    target = context.service_plan.shapes.get(member.target)
    if target is not None:
        candidates.append((target.constraints, target.id))
    for constraints, owner in candidates:
        raw = constraints.get("smithy.api#range")
        if not isinstance(raw, dict) or "max" not in raw:
            continue
        maximum = raw["max"]
        if not isinstance(maximum, int) or isinstance(maximum, bool):
            msg = f"range maximum on {owner} must be an integer for page-size lowering"
            raise PythonLoweringError(msg)
        return maximum
    return None


def _model_aliases(context: PythonGenerationContext) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for shape in context.service_plan.shapes.values():
        if shape.kind != "structure" or shape.id in context.settings.external_models:
            continue
        for member in shape.members:
            extension = member.extensions.get(PYTHON_MODEL_FIELD_TRAIT)
            if extension is None:
                continue
            if not isinstance(extension, dict):
                msg = f"Python extension {PYTHON_MODEL_FIELD_TRAIT!r} on {member.id} must be an object"
                raise PythonLoweringError(msg)
            if "name" not in extension:
                continue
            if not isinstance(extension["name"], str):
                msg = f"Python model field name on {member.id} must be a string"
                raise PythonLoweringError(msg)
            target_name = context.symbols.to_member_name(
                member,
                scope=f"model-fields:{shape.id}",
                purpose="model",
            )
            aliases[f"{_shape_name(shape.id)}.{_json_name(member)}"] = target_name
    return aliases


def _json_name(member: MemberPlan) -> str:
    value = member.traits.get("smithy.api#jsonName")
    if value is None:
        return member.name
    if not isinstance(value, str):
        msg = f"smithy.api#jsonName on {member.id} must be a string"
        raise PythonLoweringError(msg)
    return value


def _documentation_url(documentation: dict[str, str]) -> str | None:
    if not documentation:
        return None
    if "operation" in documentation:
        return documentation["operation"]
    return documentation[min(documentation)]


def _python_uri(operation: OperationPlan) -> str:
    if operation.http is None:
        msg = "HTTP operation expected before URI lowering"
        raise AssertionError(msg)
    uri = operation.http.uri
    for binding in operation.http.request_bindings:
        if binding.location == "label" and binding.greedy:
            uri = uri.replace(f"{{{binding.name}+}}", f"{{{binding.name}}}")
    if "+}" in uri:
        msg = f"Python runtime cannot resolve unmatched greedy URI labels in {operation.http.uri!r} on {operation.id}"
        raise PythonLoweringError(msg)
    return uri


def _require_query_binding(binding: HttpBindingPlan, operation_id: str, role: str) -> None:
    if binding.location != "query":
        msg = f"{role} on {operation_id} must use an HTTP query binding, got {binding.location!r}"
        raise PythonLoweringError(msg)


def _policy(nodes: dict[str, JSONValue], name: str, owner: str) -> dict[str, JSONValue]:
    value = nodes.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"normalized policy {name!r} on {owner} must be an object"
        raise PythonLoweringError(msg)
    return value


def _policy_flag(nodes: dict[str, JSONValue], name: str, owner: str) -> bool:
    value = nodes.get(name, False)
    if not isinstance(value, bool):
        msg = f"normalized policy flag {name!r} on {owner} must be a boolean"
        raise PythonLoweringError(msg)
    return value


def _extension(nodes: dict[str, JSONValue], trait_id: str, owner: str) -> dict[str, JSONValue]:
    value = nodes.get(trait_id)
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"Python extension {trait_id!r} on {owner} must be an object"
        raise PythonLoweringError(msg)
    return value


def _required_string(values: dict[str, JSONValue], name: str, owner: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        msg = f"{owner} requires string field {name!r}"
        raise PythonLoweringError(msg)
    return value


def _optional_string(values: dict[str, JSONValue], name: str) -> str | None:
    value = values.get(name)
    if value is not None and not isinstance(value, str):
        msg = f"field {name!r} must be a string when present"
        raise PythonLoweringError(msg)
    return value


def _string_array(values: dict[str, JSONValue], name: str, *, owner: str) -> tuple[str, ...]:
    value = values.get(name)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        msg = f"{owner} requires a non-empty string array field {name!r}"
        raise PythonLoweringError(msg)
    return tuple(cast("list[str]", value))


def _optional_boolean(values: dict[str, JSONValue], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if not isinstance(value, bool):
        msg = f"field {name!r} must be a boolean"
        raise PythonLoweringError(msg)
    return value


def _optional_number(values: dict[str, JSONValue], name: str, *, default: float) -> float:
    value = values.get(name, default)
    if not isinstance(value, int | float) or isinstance(value, bool):
        msg = f"field {name!r} must be a number"
        raise PythonLoweringError(msg)
    return float(value)


def _optional_integer(
    values: dict[str, JSONValue],
    name: str,
    *,
    default: int | None,
) -> int | None:
    value = values.get(name, default)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        msg = f"field {name!r} must be an integer"
        raise PythonLoweringError(msg)
    return value


def _choice[T: str](
    values: dict[str, JSONValue],
    name: str,
    choices: tuple[T, ...],
    *,
    default: T | None,
) -> T:
    value = values.get(name, default)
    if value not in choices:
        msg = f"field {name!r} must be one of {choices}, got {value!r}"
        raise PythonLoweringError(msg)
    return cast("T", value)


def _shape_name(shape_id: str) -> str:
    return shape_id.rsplit("#", maxsplit=1)[-1].split("$", maxsplit=1)[0]


def _default_vendor(service_id: str) -> str:
    name = _shape_name(service_id).removesuffix("Service")
    return snake_case(name).replace("_", " ").title()
