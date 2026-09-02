"""Tests for the strict, target-neutral service-plan boundary."""

from copy import deepcopy
from typing import Any, cast

import pytest

from spitzeisen.codegen.service_plan import (
    DefaultValue,
    EnumValuePlan,
    HttpBindingPlan,
    HttpErrorPlan,
    HttpPlan,
    MemberPlan,
    OperationPlan,
    Service,
    ServicePlan,
    ShapePlan,
)
from spitzeisen.codegen.service_plan_io import ServicePlanError, service_plan_document, service_plan_from_document

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
    state_id = "example#State"
    state = ShapePlan(
        state_id,
        "enum",
        recursive=False,
        traits={"smithy.api#documentation": "State values."},
        members=(
            _member(
                state_id,
                "OPEN",
                "smithy.api#Unit",
                traits={"smithy.api#enumValue": "open"},
            ),
            _member(
                state_id,
                "CLOSED",
                "smithy.api#Unit",
                traits={"smithy.api#enumValue": "closed", "smithy.api#deprecated": {}},
            ),
        ),
        enum_values=(
            EnumValuePlan("OPEN", "open", documentation="An open record."),
            EnumValuePlan("CLOSED", "closed", traits={"smithy.api#deprecated": {}}),
        ),
    )
    input_shape = ShapePlan(
        "example#GetThingInput",
        "structure",
        recursive=False,
        members=(
            _member(
                "example#GetThingInput",
                "id",
                text.id,
                required=True,
                constraints={"smithy.api#length": {"min": 1, "max": 64}},
            ),
            _member(
                "example#GetThingInput",
                "state",
                state.id,
                default=DefaultValue(present=True, value=None),
                extensions={"spitzeisen.python#parameter": {"name": "record_state"}},
                policies={
                    "client_default": {"value": None},
                    "query_encoding": {"style": "pipeDelimited", "explode": False, "allow_reserved": False},
                },
            ),
        ),
    )
    thing = ShapePlan(
        "example#Thing",
        "structure",
        recursive=True,
        members=(
            _member(
                "example#Thing",
                "id",
                text.id,
                required=True,
                traits={"smithy.api#jsonName": "thing_id"},
                extensions={"spitzeisen.python#modelField": {"name": "identifier"}},
            ),
        ),
    )
    output = ShapePlan(
        "example#GetThingOutput",
        "structure",
        recursive=False,
        members=(_member("example#GetThingOutput", "thing", thing.id, required=True),),
    )
    error = ShapePlan(
        "example#NotFound",
        "structure",
        recursive=False,
        members=(_member("example#NotFound", "message", text.id),),
    )
    operation = OperationPlan(
        id="example#GetThing",
        input=input_shape.id,
        output=output.id,
        errors=(error.id,),
        auth_schemes=("smithy.api#httpBearerAuth",),
        documentation="Get one thing.",
        external_documentation={"Guide": "https://example.test/guide"},
        http=HttpPlan(
            "GET",
            "/things/{id+}",
            200,
            request_bindings=(
                HttpBindingPlan("example#GetThingInput$id", "label", "id", greedy=True),
                HttpBindingPlan("example#GetThingInput$state", "query", "state"),
            ),
            response_bindings=(HttpBindingPlan("example#GetThingOutput$thing", "payload", "thing"),),
            error_bindings={
                error.id: HttpErrorPlan(
                    404,
                    (HttpBindingPlan("example#NotFound$message", "unbound", "message"),),
                ),
            },
        ),
        traits={"smithy.api#readonly": {}},
        extensions={"spitzeisen.python#operation": {"module": "things", "accessor": "things", "method": "get"}},
        policies={
            "result": {"path": ["thing"]},
            "not_found": {"behavior": "absent"},
            "rate_limit_cost": {"units": 1.5},
        },
    )
    return ServicePlan(
        service=Service(
            id="example#ExampleService",
            version="2026-08-30",
            protocols=("aws.protocols#restJson1",),
            auth_schemes=("smithy.api#httpBearerAuth",),
            operations=(operation.id,),
            documentation="Example service.",
            external_documentation={"API": "https://example.test/api"},
        ),
        operations={operation.id: operation},
        shapes={shape.id: shape for shape in (text, state, input_shape, thing, output, error)},
        extensions={"producer": "smithy-service-plan"},
    )


def test_service_plan_contract_round_trips_losslessly_without_a_version_wrapper() -> None:
    """Exact shapes, policies, extensions, defaults, docs, and HTTP bindings survive JSON."""
    plan = _plan()
    document = service_plan_document(plan)

    assert "schema_version" not in document
    assert service_plan_from_document(document) == plan
    shapes = cast("dict[str, dict[str, Any]]", document["shapes"])
    enum_values = cast("list[dict[str, Any]]", shapes["example#State"]["enum_values"])
    assert [value["name"] for value in enum_values] == ["OPEN", "CLOSED"]
    members = cast("list[dict[str, Any]]", shapes["example#GetThingInput"]["members"])
    assert members[0]["default"] == {"present": False}
    assert members[1]["default"] == {"present": True, "value": None}
    operations = cast("dict[str, dict[str, Any]]", document["operations"])
    operation = operations["example#GetThing"]
    assert operation["policies"] == {
        "result": {"path": ["thing"]},
        "not_found": {"behavior": "absent"},
        "rate_limit_cost": {"units": 1.5},
    }
    http = cast("dict[str, Any]", operation["http"])
    bindings = cast("list[dict[str, Any]]", http["request_bindings"])
    assert bindings[0]["greedy"] is True


def test_service_plan_contract_rejects_unknown_fields_and_collapsed_kinds() -> None:
    """Lockstep schema changes and lossy target-flavored kinds fail at the boundary."""
    unknown = deepcopy(service_plan_document(_plan()))
    unknown["schema_version"] = 1
    with pytest.raises(ServicePlanError, match=r"unknown fields.*schema_version"):
        service_plan_from_document(unknown)

    collapsed = deepcopy(service_plan_document(_plan()))
    shapes = cast("dict[str, dict[str, Any]]", collapsed["shapes"])
    shapes["example#Text"]["kind"] = "integer-like"
    with pytest.raises(ServicePlanError, match="exact Smithy shape kind"):
        service_plan_from_document(collapsed)


def test_service_plan_contract_rejects_ambiguous_default_presence_and_invalid_greedy_binding() -> None:
    """Explicit null and greedy-label semantics remain unambiguous."""
    ambiguous = deepcopy(service_plan_document(_plan()))
    shapes = cast("dict[str, dict[str, Any]]", ambiguous["shapes"])
    members = cast("list[dict[str, Any]]", shapes["example#GetThingInput"]["members"])
    cast("dict[str, Any]", members[0]["default"])["value"] = None
    with pytest.raises(ServicePlanError, match=r"unknown fields.*value"):
        service_plan_from_document(ambiguous)

    greedy_query = deepcopy(service_plan_document(_plan()))
    operations = cast("dict[str, dict[str, Any]]", greedy_query["operations"])
    http = cast("dict[str, Any]", operations["example#GetThing"]["http"])
    bindings = cast("list[dict[str, Any]]", http["request_bindings"])
    bindings[1]["greedy"] = True
    with pytest.raises(ServicePlanError, match="only be true for an HTTP label"):
        service_plan_from_document(greedy_query)


def test_service_plan_contract_rejects_dangling_operation_shapes() -> None:
    """The strict reader validates the selected operation graph after parsing."""
    document = deepcopy(service_plan_document(_plan()))
    operations = cast("dict[str, dict[str, Any]]", document["operations"])
    operations["example#GetThing"]["output"] = "example#MissingOutput"

    with pytest.raises(ServicePlanError, match="missing output shape"):
        service_plan_from_document(document)


def test_service_plan_contract_requires_dedicated_io_and_unique_shape_id_lists() -> None:
    """Prepared operations always carry input/output structures and set-like identity lists."""
    missing_input = deepcopy(service_plan_document(_plan()))
    operations = cast("dict[str, dict[str, Any]]", missing_input["operations"])
    del operations["example#GetThing"]["input"]
    with pytest.raises(ServicePlanError, match=r"missing required fields.*input"):
        service_plan_from_document(missing_input)

    duplicate_operation = deepcopy(service_plan_document(_plan()))
    service = cast("dict[str, Any]", duplicate_operation["service"])
    service["operations"] = ["example#GetThing", "example#GetThing"]
    with pytest.raises(ServicePlanError, match="duplicate ShapeIds"):
        service_plan_from_document(duplicate_operation)


def test_service_plan_contract_validates_native_enum_members_against_metadata() -> None:
    """Native enum members retain their graph edges and agree with ordered value metadata."""
    document = deepcopy(service_plan_document(_plan()))
    assert service_plan_from_document(document) == _plan()

    shapes = cast("dict[str, dict[str, Any]]", document["shapes"])
    members = cast("list[dict[str, Any]]", shapes["example#State"]["members"])
    members[0]["target"] = "example#Text"
    with pytest.raises(ServicePlanError, match=r"must target 'smithy.api#Unit'"):
        service_plan_from_document(document)
