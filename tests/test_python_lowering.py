"""Tests for Python-native symbols, protocols, extensions, and capability checks."""

from dataclasses import replace
from typing import Any, cast

import pytest

from spitzeisen.codegen.python_context import (
    PythonExternalModel,
    PythonGenerationContext,
    PythonInputAdapter,
    PythonSettings,
)
from spitzeisen.codegen.python_integrations import PythonIntegration
from spitzeisen.codegen.python_lowering import PythonLoweringError, lower_service_plan
from spitzeisen.codegen.python_plan import PythonImport, PythonTypePlan
from spitzeisen.codegen.python_protocols import ProtocolSelectionError
from spitzeisen.codegen.service_plan import (
    DefaultValue,
    EventStreamsPlan,
    HttpBindingPlan,
    HttpPlan,
    MemberPlan,
    OperationPlan,
    Service,
    ServicePlan,
    ShapePlan,
)

_NO_DEFAULT = DefaultValue(present=False)


def _member(
    shape: str,
    name: str,
    target: str,
    *,
    required: bool = False,
    default: DefaultValue = _NO_DEFAULT,
    **values: Any,
) -> MemberPlan:
    return MemberPlan(
        id=f"{shape}${name}",
        name=name,
        target=target,
        required=required,
        client_nullable=not required,
        default=default,
        **values,
    )


def _plan() -> ServicePlan:
    text = ShapePlan("example#Text", "string", recursive=False)
    long = ShapePlan("example#Counter", "long", recursive=False)
    page_size = ShapePlan(
        "example#PageSize",
        "integer",
        recursive=False,
        constraints={"smithy.api#range": {"min": 1, "max": 250}},
    )
    sort_id = "example#SortEncoding"
    sort = ShapePlan(
        sort_id,
        "enum",
        recursive=False,
        members=(
            _member(
                sort_id,
                "NAME_ASC",
                "smithy.api#Unit",
                traits={"smithy.api#enumValue": "name::asc"},
            ),
            _member(
                sort_id,
                "NAME_DESC",
                "smithy.api#Unit",
                traits={"smithy.api#enumValue": "name::desc"},
            ),
        ),
        enum_values=(),
    )
    # Enum values are assigned separately to keep the fixture's member helper focused.
    from spitzeisen.codegen.service_plan import EnumValuePlan  # noqa: PLC0415

    sort = replace(
        sort,
        enum_values=(EnumValuePlan("NAME_ASC", "name::asc"), EnumValuePlan("NAME_DESC", "name::desc")),
    )
    input_shape = ShapePlan(
        "example#ListThingsInput",
        "structure",
        recursive=False,
        members=(
            _member("example#ListThingsInput", "class", text.id, required=True),
            _member("example#ListThingsInput", "class_", long.id),
            _member(
                "example#ListThingsInput",
                "language",
                text.id,
                extensions={"spitzeisen.python#parameter": {"name": "lang"}},
                policies={
                    "client_default": {"value": None},
                    "query_encoding": {"style": "pipeDelimited", "explode": False, "allow_reserved": False},
                },
            ),
            _member("example#ListThingsInput", "page", long.id, required=True),
            _member("example#ListThingsInput", "pageSize", page_size.id),
            _member(
                "example#ListThingsInput",
                "sortBy",
                sort.id,
                policies={"client_default": {"value": "name::asc"}},
            ),
        ),
    )
    thing = ShapePlan(
        "example#Thing",
        "structure",
        recursive=False,
        members=(
            _member(
                "example#Thing",
                "displayName",
                text.id,
                traits={"smithy.api#jsonName": "display_name"},
                extensions={"spitzeisen.python#modelField": {"name": "label"}},
            ),
        ),
    )
    things = ShapePlan(
        "example#ThingList",
        "list",
        recursive=False,
        members=(_member("example#ThingList", "member", thing.id),),
    )
    output = ShapePlan(
        "example#ListThingsOutput",
        "structure",
        recursive=False,
        members=(_member("example#ListThingsOutput", "things", things.id, required=True),),
    )
    operation = OperationPlan(
        id="example#ListThings",
        input=input_shape.id,
        output=output.id,
        errors=(),
        auth_schemes=(),
        documentation="List things.",
        external_documentation={"Guide": "https://example.test/things"},
        http=HttpPlan(
            "GET",
            "/things",
            200,
            request_bindings=tuple(HttpBindingPlan(member.id, "query", member.name) for member in input_shape.members),
            response_bindings=(HttpBindingPlan("example#ListThingsOutput$things", "payload", "things"),),
        ),
        extensions={"spitzeisen.python#operation": {"module": "records", "accessor": "catalog", "method": "close"}},
        policies={
            "result": {"path": ["things"]},
            "not_found": {"behavior": "absent"},
            "rate_limit_cost": {"units": 2},
            "page_number_pagination": {
                "page_member": "example#ListThingsInput$page",
                "page_size_member": "example#ListThingsInput$pageSize",
                "start": 1,
                "step": 1,
            },
            "sorting": {
                "encoding": "suffix",
                "sort_member": "example#ListThingsInput$sortBy",
                "separator": "::",
            },
        },
    )
    return ServicePlan(
        service=Service(
            id="example#ExampleService",
            version="1",
            protocols=("aws.protocols#restJson1",),
            auth_schemes=(),
            operations=(operation.id,),
        ),
        operations={operation.id: operation},
        shapes={shape.id: shape for shape in (text, long, page_size, sort, input_shape, thing, things, output)},
    )


def _settings(**values: Any) -> PythonSettings:
    return PythonSettings(package="example_sdk", client_name="ExampleApi", **values)


def test_python_lowering_owns_names_types_defaults_policies_and_protocol_selection() -> None:
    """Neutral identities and exact shapes become safe renderer facts only in Python."""
    result = lower_service_plan(_plan(), _settings())

    operation = result.operations[0]
    assert result.protocol_id == "aws.protocols#restJson1"
    assert result.model_aliases == {"Thing.display_name": "label"}
    assert operation.response_shape == "example#Thing"
    assert operation.model == "Thing"
    assert operation.model_module == "example_sdk.models.thing"
    assert operation.generate_model is True
    assert operation.response_cardinality == "collection"
    assert operation.not_found == "absent"
    assert operation.cost == 2
    assert operation.method_name == "close_2"
    assert operation.class_name == "RecordsApi"
    assert operation.accessor == "catalog"
    assert operation.docs_url == "https://example.test/things"
    assert [parameter.name for parameter in operation.params] == ["class_", "class__2", "lang"]
    assert operation.params[1].type.kind == "int"
    assert operation.params[0].default.is_required
    assert operation.params[2].default.allows_none
    assert operation.params[2].style == "pipeDelimited"
    assert operation.params[2].explode is False
    assert operation.pagination == "page_number"
    assert operation.page_param == "page"
    assert operation.page_size is not None
    assert operation.page_size.maximum == 250
    assert operation.sorting is not None
    assert operation.sorting.style == "suffix"
    assert operation.sorting.separator == "::"
    assert operation.sorting.sort.default.value == "name"
    assert operation.sorting.order.default.value == "asc"


def test_page_number_pagination_does_not_require_a_page_size_member() -> None:
    """The portable pagination trait permits a page cursor without a page-size control."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    pagination = dict(cast("dict[str, Any]", operation.policies["page_number_pagination"]))
    pagination.pop("page_size_member")
    page_only = replace(
        operation,
        policies={**operation.policies, "page_number_pagination": pagination},
    )

    result = lower_service_plan(replace(plan, operations={page_only.id: page_only}), _settings())

    lowered = result.operations[0]
    assert lowered.pagination == "page_number"
    assert lowered.page_param == "page"
    assert lowered.page_size is None


def test_page_size_range_can_be_attached_to_a_prelude_target_member() -> None:
    """Member constraints are sufficient when Smithy's implicit prelude shape is not in the closure."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    input_shape = plan.shape(operation.input)
    page_size = replace(
        input_shape.member("pageSize"),
        target="smithy.api#Integer",
        constraints={"smithy.api#range": {"max": 100}},
    )
    constrained_input = replace(
        input_shape,
        members=tuple(page_size if member.id == page_size.id else member for member in input_shape.members),
    )
    constrained = replace(plan, shapes={**plan.shapes, constrained_input.id: constrained_input})

    lowered = lower_service_plan(constrained, _settings()).operations[0]

    assert lowered.page_size is not None
    assert lowered.page_size.maximum == 100


def test_python_settings_own_external_models_and_stable_input_adapter_registry() -> None:
    """Model implementation and callable adapter imports are target settings, never Smithy source fragments."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    input_shape = plan.shape(operation.input)
    language = input_shape.member("language")
    adapted_language = replace(
        language,
        policies={**language.policies, "input_adapter": {"id": "example.adapters#isoDate"}},
    )
    adapted_input = replace(
        input_shape,
        members=tuple(adapted_language if member.id == language.id else member for member in input_shape.members),
    )
    adapted = replace(plan, shapes={**plan.shapes, adapted_input.id: adapted_input})
    function = PythonImport("example_sdk.params", "coerce_date")
    result = lower_service_plan(
        adapted,
        _settings(
            external_models={
                "example#Thing": PythonExternalModel("records.models", "Record", ("records-runtime>=1",)),
            },
            input_adapters={
                "example.adapters#isoDate": PythonInputAdapter(
                    function,
                    PythonTypePlan("date_input"),
                    ("date-runtime>=2",),
                ),
            },
        ),
    )

    lowered = result.operations[0]
    language_param = next(parameter for parameter in lowered.params if parameter.name == "lang")
    assert lowered.model == "Record"
    assert lowered.model_module == "records.models"
    assert lowered.generate_model is False
    assert result.response_shapes == ()
    assert result.dependencies == ("date-runtime>=2", "records-runtime>=1")
    assert language_param.type.kind == "date_input"
    assert language_param.coercion.function == function
    assert language_param.coercion.function is not None
    assert language_param.coercion.function.identifier == "coerce_date"

    with pytest.raises(PythonLoweringError, match="is not registered"):
        lower_service_plan(adapted, _settings())


def test_python_external_model_can_own_a_prelude_scalar_result() -> None:
    """The neutral graph may terminate at a prelude shape that a target integration owns."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    output = plan.shape(operation.output)
    scalar_member = replace(output.member("things"), target="smithy.api#String")
    scalar_output = replace(output, members=(scalar_member,))
    scalar_operation = replace(operation, policies={"result": {"path": ["things"]}})
    scalar_plan = replace(
        plan,
        operations={scalar_operation.id: scalar_operation},
        shapes={**plan.shapes, scalar_output.id: scalar_output},
    )

    lowered = lower_service_plan(
        scalar_plan,
        _settings(
            external_models={
                "smithy.api#String": PythonExternalModel("records.models", "Record"),
            },
        ),
    ).operations[0]

    assert lowered.response_shape == "smithy.api#String"
    assert lowered.model_module == "records.models"
    assert lowered.model == "Record"
    assert lowered.response_cardinality == "single"
    assert lowered.generate_model is False

    with pytest.raises(PythonLoweringError, match="Smithy prelude scalar"):
        lower_service_plan(scalar_plan, _settings())


def test_rest_json_timestamp_coercion_resolves_smithy_precedence_and_http_defaults() -> None:
    """Timestamp formats stay protocol-owned for scalars and collection elements."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    assert operation.http is not None
    input_shape = plan.shape(operation.input)
    recorded_at = ShapePlan("example#RecordedAt", "timestamp", recursive=False)
    expires_at = ShapePlan(
        "example#ExpiresAt",
        "timestamp",
        recursive=False,
        traits={"smithy.api#timestampFormat": "epoch-seconds"},
    )
    timestamps = ShapePlan(
        "example#Timestamps",
        "list",
        recursive=False,
        members=(
            _member(
                "example#Timestamps",
                "member",
                recorded_at.id,
                traits={"smithy.api#timestampFormat": "epoch-seconds"},
            ),
        ),
    )
    query_member = _member(input_shape.id, "recordedAt", recorded_at.id)
    header_member = _member(
        input_shape.id,
        "expiresAt",
        expires_at.id,
        traits={"smithy.api#timestampFormat": "http-date"},
    )
    collection_member = _member(input_shape.id, "moments", timestamps.id)
    timestamp_input = replace(
        input_shape,
        members=(*input_shape.members, query_member, header_member, collection_member),
    )
    timestamp_operation = replace(
        operation,
        http=replace(
            operation.http,
            request_bindings=(
                *operation.http.request_bindings,
                HttpBindingPlan(query_member.id, "query", "recordedAt"),
                HttpBindingPlan(header_member.id, "header", "Expires-At"),
                HttpBindingPlan(collection_member.id, "query", "moment"),
            ),
        ),
    )
    timestamp_plan = replace(
        plan,
        operations={timestamp_operation.id: timestamp_operation},
        shapes={
            **plan.shapes,
            timestamp_input.id: timestamp_input,
            recorded_at.id: recorded_at,
            expires_at.id: expires_at,
            timestamps.id: timestamps,
        },
    )

    lowered = lower_service_plan(timestamp_plan, _settings()).operations[0]

    query = next(parameter for parameter in lowered.query_params if parameter.name == "recorded_at")
    header = next(parameter for parameter in lowered.header_params if parameter.name == "expires_at")
    collection = next(parameter for parameter in lowered.query_params if parameter.name == "moments")
    assert (query.coercion.kind, query.coercion.timestamp_format) == ("timestamp", "date-time")
    assert (header.coercion.kind, header.coercion.timestamp_format) == ("timestamp", "http-date")
    assert (collection.coercion.kind, collection.coercion.timestamp_format) == (
        "timestamps",
        "epoch-seconds",
    )


def test_rest_json_rejects_malformed_timestamp_format_during_lowering() -> None:
    """A malformed trait cannot fall through to datetime stringification."""
    plan = _plan()
    operation = next(iter(plan.operations.values()))
    assert operation.http is not None
    input_shape = plan.shape(operation.input)
    timestamp = ShapePlan(
        "example#RecordedAt",
        "timestamp",
        recursive=False,
        traits={"smithy.api#timestampFormat": "calendar-date"},
    )
    member = _member(input_shape.id, "recordedAt", timestamp.id)
    timestamp_input = replace(input_shape, members=(*input_shape.members, member))
    timestamp_operation = replace(
        operation,
        http=replace(
            operation.http,
            request_bindings=(
                *operation.http.request_bindings,
                HttpBindingPlan(member.id, "query", "recordedAt"),
            ),
        ),
    )
    timestamp_plan = replace(
        plan,
        operations={timestamp_operation.id: timestamp_operation},
        shapes={**plan.shapes, timestamp_input.id: timestamp_input, timestamp.id: timestamp},
    )

    with pytest.raises(PythonLoweringError, match=r"timestampFormat.*calendar-date"):
        lower_service_plan(timestamp_plan, _settings())


def test_python_lowering_rejects_protocols_and_runtime_capabilities_not_the_neutral_plan() -> None:
    """Unsupported target behavior remains representable for a future Rust or Python handler."""
    plan = _plan()
    unsupported_protocol = replace(plan, service=replace(plan.service, protocols=("example.protocols#rpc",)))
    with pytest.raises(ProtocolSelectionError, match="no Python handler"):
        lower_service_plan(unsupported_protocol, _settings())

    operation = next(iter(plan.operations.values()))
    assert operation.http is not None
    post = replace(operation, http=replace(operation.http, method="POST"))
    with pytest.raises(PythonLoweringError, match="supports GET only"):
        lower_service_plan(replace(plan, operations={post.id: post}), _settings())

    cursor = replace(operation, policies={**operation.policies, "smithy_pagination": {"input_token": "token"}})
    with pytest.raises(PythonLoweringError, match="standard Smithy pagination"):
        lower_service_plan(replace(plan, operations={cursor.id: cursor}), _settings())

    streaming = replace(operation, event_streams=EventStreamsPlan(output="example#ListThingsOutput$things"))
    with pytest.raises(PythonLoweringError, match="event streams"):
        lower_service_plan(replace(plan, operations={streaming.id: streaming}), _settings())

    input_shape = plan.shape(operation.input)
    language = input_shape.member("language")
    deep_language = replace(
        language,
        policies={
            **language.policies,
            "query_encoding": {"style": "deepObject", "explode": True, "allow_reserved": False},
        },
    )
    deep_input = replace(
        input_shape,
        members=tuple(deep_language if member.id == language.id else member for member in input_shape.members),
    )
    deep_plan = replace(plan, shapes={**plan.shapes, deep_input.id: deep_input})
    with pytest.raises(PythonLoweringError, match="deepObject"):
        lower_service_plan(deep_plan, _settings())

    body_bindings = tuple(
        replace(binding, location="document") if binding.member_id == language.id else binding
        for binding in operation.http.request_bindings
    )
    body = replace(operation, http=replace(operation.http, request_bindings=body_bindings))
    with pytest.raises(PythonLoweringError, match="cannot serialize request bindings"):
        lower_service_plan(replace(plan, operations={body.id: body}), _settings())


class _DependencyIntegration(PythonIntegration):
    name = "extra-runtime"

    def configure(self, context: PythonGenerationContext) -> None:
        context.dependencies.add("example-extra>=1")


def test_python_integrations_are_explicitly_opt_in() -> None:
    """Available integrations do nothing unless the target settings select them by name."""
    integration = _DependencyIntegration()
    disabled = lower_service_plan(_plan(), _settings(), available_integrations=(integration,))
    enabled = lower_service_plan(
        _plan(),
        _settings(enabled_integrations=(integration.name,)),
        available_integrations=(integration,),
    )

    assert disabled.dependencies == ()
    assert enabled.dependencies == ("example-extra>=1",)
