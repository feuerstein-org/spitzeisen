"""Rendering tests for the Python-specific plan produced by neutral-plan lowering."""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest
from plan_fixtures import operation_plan, parameter_plan, python_plan, splits_plan

from spitzeisen import AsyncSpitzeisenConfig, NoLimit
from spitzeisen.codegen.cli import is_current, normalize_model_header, write
from spitzeisen.codegen.generate import (
    GeneratedModule,
    generate_plan,
    generated_model_names,
    model_exports_module,
    prune_spec,
)
from spitzeisen.codegen.python_plan import (
    PythonCoercionPlan,
    PythonDefaultPlan,
    PythonImport,
    PythonNotFound,
    PythonTypePlan,
)
from spitzeisen.testing import FakeRouter


def rendered(client: Any | None = None) -> dict[str, str]:
    """Render modules keyed by their package-relative path."""
    active = client or splits_plan()
    root = Path(active.package)
    return {module.path.relative_to(root).as_posix(): module.source for module in generate_plan(active, root)}


def test_generated_modules_parse_and_cover_both_surfaces() -> None:
    """One plan produces regenerated bases and create-once public extensions for both surfaces."""
    modules = rendered()

    assert set(modules) == {
        "__init__.py",
        "_async/_generated/__init__.py",
        "_async/_generated/client.py",
        "_async/_generated/splits.py",
        "_async/__init__.py",
        "_async/client.py",
        "_async/splits.py",
        "_sync/_generated/__init__.py",
        "_sync/_generated/client.py",
        "_sync/_generated/splits.py",
        "_sync/__init__.py",
        "_sync/client.py",
        "_sync/splits.py",
        "models/__init__.py",
        "models/split.py",
    }
    for source in modules.values():
        ast.parse(source)


def test_modeled_strings_cannot_break_generated_python_source() -> None:
    """Wire strings and documentation are encoded as data, never interpolated as Python syntax."""
    parameter = parameter_plan(
        name="filter_value",
        wire_name='filter"name',
        type=PythonTypePlan("str"),
        description='Accepts a """quoted""" value from C:\\records.\x00',
        coercion=PythonCoercionPlan("identity"),
    )
    operation = operation_plan(
        path='/things/"quoted"',
        summary='Return """quoted""" things from C:\\records.\x00',
        params=(parameter,),
    )
    source = rendered(python_plan(operation))["_async/_generated/things.py"]

    ast.parse(source)
    assert "name='filter\"name'" in source
    assert '\\"""quoted\\"""' in source
    assert "\\x00" in source


def test_collection_renderer_consumes_policy_without_smithy_state() -> None:
    """Pagination, coercion, sorting, and aliases come exclusively from the immutable plan."""
    source = rendered()["_async/_generated/splits.py"]

    assert "adjustment_types: list[AdjustmentType] | None = None" in source
    assert 'coerce_choices(adjustment_types, AdjustmentType, "adjustment_types")' in source
    assert 'name="adjustment_type.any_of"' in source
    assert "execution_date_gte: str | date | datetime | None = None" in source
    assert 'coerce_date(execution_date_gte, "execution_date_gte")' in source
    assert 'sort: SortField = "execution_date"' in source
    assert 'order: SortDirection = "desc"' in source
    assert "coerce_sort(" in source
    assert "min(max_results, 5000) if max_results is not None else 5000" in source
    assert "pagination=PageNumber(" in source


def test_timestamp_coercions_render_as_protocol_selected_runtime_calls() -> None:
    """The renderer consumes an explicit format and never guesses from Python datetime values."""
    recorded_at = parameter_plan(
        name="recorded_at",
        wire_name="recordedAt",
        type=PythonTypePlan("datetime"),
        description="Timestamp to match.",
        coercion=PythonCoercionPlan("timestamp", timestamp_format="date-time"),
    )
    moments = parameter_plan(
        name="moments",
        wire_name="X-Moments",
        type=PythonTypePlan("list", members=(PythonTypePlan("datetime"),)),
        description="Timestamp headers.",
        coercion=PythonCoercionPlan("timestamps", timestamp_format="http-date"),
        location="header",
    )

    source = rendered(python_plan(operation_plan(params=(recorded_at, moments))))["_async/_generated/things.py"]

    assert "coerce_timestamp," in source
    assert "coerce_timestamps," in source
    assert 'coerce_timestamp(recorded_at, "date-time", "recorded_at")' in source
    assert 'coerce_timestamps(moments, "http-date", "moments")' in source
    ast.parse(source)


def test_async_and_sync_surfaces_have_native_syntax_and_documentation() -> None:
    """Rendering twice preserves surface-specific syntax and prose."""
    modules = rendered()
    asynchronous = modules["_async/_generated/splits.py"]
    synchronous = modules["_sync/_generated/splits.py"]

    assert "async def get_splits(" in asynchronous
    assert "await self._get_all_pages(" in asynchronous
    assert "Fetches every page asynchronously" in asynchronous
    assert "async with AsyncExampleApi(" in asynchronous
    assert "def get_splits(" in synchronous
    assert "await" not in synchronous
    assert "Fetches every page, then validates" in synchronous
    assert "with SyncExampleApi(" in synchronous


def test_required_path_query_and_header_arguments_render_faithfully() -> None:
    """Wire requiredness and Python defaults remain independent at the renderer boundary."""
    account = parameter_plan(
        name="account",
        wire_name="account-id",
        type=PythonTypePlan("int"),
        description="Account identifier.",
        coercion=PythonCoercionPlan("identity"),
        location="path",
        required=True,
        default=PythonDefaultPlan("required"),
    )
    region = parameter_plan(
        name="region",
        wire_name="region",
        type=PythonTypePlan("str"),
        description="Required region.",
        coercion=PythonCoercionPlan("identity"),
        required=True,
        default=PythonDefaultPlan("value", "eu"),
        explode=False,
    )
    workspace = parameter_plan(
        name="workspace",
        wire_name="X-Workspace",
        type=PythonTypePlan("str"),
        description="Workspace header.",
        coercion=PythonCoercionPlan("identity"),
        location="header",
        required=True,
        default=PythonDefaultPlan("required"),
    )
    operation = operation_plan(
        key="accounts",
        path="/accounts/{account-id}",
        method_name="get_accounts",
        model="Account",
        params=(region, workspace),
        path_params=(account,),
    )
    source = rendered(python_plan(operation))["_async/_generated/accounts.py"]

    assert "account: int," in source
    assert 'region: str = "eu"' in source
    assert "workspace: str," in source
    assert '"X-Workspace": require_value(workspace, "workspace")' in source
    assert '**{"account-id": serialize_path_param(require_value(account, "account"), greedy=False)}' in source
    ast.parse(source)


@pytest.mark.parametrize(
    ("not_found", "return_annotation", "request_call"),
    [
        ("raise", "-> Station:", "self._request("),
        ("absent", "-> Station | None:", "self._request_optional("),
    ],
)
def test_single_resource_renderer_honors_not_found_policy(
    not_found: PythonNotFound,
    return_annotation: str,
    request_call: str,
) -> None:
    """Optional 404 handling is explicit rather than inferred from single cardinality."""
    station_id = parameter_plan(
        name="station_id",
        wire_name="station_id",
        type=PythonTypePlan("str"),
        description="Station identifier.",
        coercion=PythonCoercionPlan("identity"),
        location="path",
        required=True,
        default=PythonDefaultPlan("required"),
    )
    operation = operation_plan(
        key="station",
        path="/stations/{station_id}",
        method_name="get_station",
        model="Station",
        response_cardinality="single",
        not_found=not_found,
        path_params=(station_id,),
    )
    source = rendered(python_plan(operation))["_async/_generated/station.py"]

    assert return_annotation in source
    assert request_call in source
    assert ("NotFoundError: If the resource does not exist" in source) is (not_found == "raise")
    ast.parse(source)


def test_collection_renderer_honors_absent_not_found_on_both_surfaces() -> None:
    """Collection 404 semantics select the optional paging primitive and exact return type."""
    operation = operation_plan(not_found="absent")
    modules = rendered(python_plan(operation))

    for path in ("_async/_generated/things.py", "_sync/_generated/things.py"):
        source = modules[path]
        assert "-> list[dict[str, Any]] | None:" in source
        assert "self._get_all_pages_optional(" in source
        ast.parse(source)


def test_renderer_imports_external_types_and_aliased_input_adapters() -> None:
    """Structured target types and adapter callables produce exact, importable source references."""
    external_type = PythonTypePlan("symbol", name="Filter", module="vendor_types")
    parameter = parameter_plan(
        name="filter_value",
        wire_name="filter",
        type=external_type,
        description="Filter value.",
        coercion=PythonCoercionPlan(
            "custom",
            function=PythonImport("vendor_adapters", "parse_filter", alias="adapt_filter"),
        ),
    )
    source = rendered(python_plan(operation_plan(params=(parameter,))))["_async/_generated/things.py"]

    assert "from vendor_adapters import parse_filter as adapt_filter" in source
    assert "from vendor_types import Filter" in source
    assert 'adapt_filter(filter_value, param_name="filter_value")' in source
    ast.parse(source)


def test_single_envelope_preserves_empty_objects_and_rejects_non_objects() -> None:
    """Envelope extraction checks shape rather than collapsing every falsy payload to absence."""
    operation = operation_plan(
        response_cardinality="single",
        not_found="absent",
        results_key="thing",
    )
    source = rendered(python_plan(operation))["_async/_generated/things.py"]

    assert 'result = data.get("thing")' in source
    assert "if result is None:" in source
    assert "if not isinstance(result, dict):" in source
    assert 'data.get("thing") or None' not in source
    ast.parse(source)


def test_public_scaffolds_and_aggregate_client_wrap_generated_bases() -> None:
    """Client-owned modules remain separate from replaceable implementation."""
    modules = rendered()

    assert "class AsyncSplitsApi(AsyncSplitsApiBase):" in modules["_async/splits.py"]
    assert "class AsyncExampleApi(AsyncExampleApiBase):" in modules["_async/client.py"]
    assert "self.splits_api = AsyncSplitsApi(config)" in modules["_async/_generated/client.py"]
    assert "await operation_api.__aenter__()" in modules["_async/_generated/client.py"]
    assert "class SyncExampleApi(SyncExampleApiBase):" in modules["_sync/client.py"]
    assert "operation_api.__enter__()" in modules["_sync/_generated/client.py"]
    assert "class Split(GeneratedSplit):" in modules["models/split.py"]


def test_only_public_extension_modules_are_create_once() -> None:
    """Regenerated bases stay replaceable while public extension files belong to SDK authors."""
    root = Path("example_api")
    modules = generate_plan(splits_plan(), root)

    create_once = {module.path.relative_to(root).as_posix() for module in modules if module.create_once}
    assert create_once == {
        "__init__.py",
        "_async/__init__.py",
        "_async/client.py",
        "_async/splits.py",
        "_sync/__init__.py",
        "_sync/client.py",
        "_sync/splits.py",
        "models/__init__.py",
        "models/split.py",
    }


def test_shared_response_shape_has_one_public_model_extension() -> None:
    """Multiple operations returning one ShapeId share its shape-symbol module."""
    client = splits_plan()
    first = client.operations[0]
    second = replace(
        first,
        shape_id="example#GetHistoricalSplits",
        key="historical_splits",
        method_name="get_historical_splits",
        class_name="HistoricalSplitsApi",
        accessor="historical_splits_api",
        const_name="HISTORICAL_SPLITS_OPERATION",
    )
    client = replace(client, operations=(first, second))

    model_paths = [
        module.path for module in generate_plan(client, Path("example_api")) if "models" in module.path.parts
    ]

    assert model_paths.count(Path("example_api/models/split.py")) == 1


def test_writer_preserves_create_once_public_modules(tmp_path: Path) -> None:
    """Regeneration updates bases without overwriting SDK-owned public subclasses."""
    generated_path = tmp_path / "_generated" / "things.py"
    public_path = tmp_path / "things.py"
    write(
        [
            GeneratedModule(generated_path, "generated v1\n"),
            GeneratedModule(public_path, "public scaffold\n", create_once=True),
        ],
    )
    public_path.write_text("hand-written customization\n")
    updated = [
        GeneratedModule(generated_path, "generated v2\n"),
        GeneratedModule(public_path, "new scaffold\n", create_once=True),
    ]

    write(updated)

    assert generated_path.read_text() == "generated v2\n"
    assert public_path.read_text() == "hand-written customization\n"
    assert all(is_current(module) for module in updated)


def test_model_and_package_facades_promote_public_response_extensions() -> None:
    """Nested generated models and public response subclasses share stable import facades."""
    client = splits_plan()
    source = "class Wind:\n    pass\n\nclass Split:\n    pass\n"
    module = model_exports_module(client, Path("example_api"), source)
    package = rendered(client)["__init__.py"]

    assert "from example_api.models._generated import Wind" in module.source
    assert "from example_api.models.split import Split" in module.source
    assert '"Split",' in module.source
    assert "from example_api import" not in package
    assert "from example_api.models import Split" in package
    assert '"AsyncExampleApi"' in package
    assert '"SyncExampleApi"' in package


def test_model_header_names_and_openapi_pruning_are_plan_driven() -> None:
    """Model backend helpers consume the plan without importing semantic frontend code."""
    header = "# generated by datamodel-codegen:\n#   filename:  pruned.json\n\nclass Thing:\n    pass\n"
    spec: dict[str, Any] = {"paths": {"/stocks/v1/splits": {}, "/unused": {}}, "components": {}}

    assert "selected OpenAPI operations" in normalize_model_header(header)
    assert "assembled Smithy model" in normalize_model_header(header, "jsonschema")
    assert generated_model_names("class Wind:\n    pass\nclass _Private:\n    pass\n") == ["Wind"]
    assert set(prune_spec(spec, splits_plan())["paths"]) == {"/stocks/v1/splits"}


def test_generated_collection_client_runs_against_the_runtime(tmp_path: Path) -> None:
    """The rendered plan produces an importable client that serializes and validates responses."""
    client = splits_plan()
    operation = client.operations[0]
    ticker = replace(operation.params[0], required=True, default=PythonDefaultPlan("required"))
    params = (ticker, *operation.params[1:])
    operation = replace(
        operation,
        params=params,
        query_params=params,
        example_args=("ticker",),
    )
    client = replace(client, operations=(operation,))
    package = tmp_path / "example_api"
    write(generate_plan(client, package))
    (package / "models" / "_generated.py").write_text(
        "from datetime import date\n"
        "from spitzeisen import SpitzeisenModel\n"
        "class Split(SpitzeisenModel):\n"
        "    ticker: str\n"
        "    execution_date: date\n",
    )
    (package / "models" / "__init__.py").write_text(
        "from typing import Literal\n"
        'AdjustmentType = Literal["forward_split", "reverse_split"]\n'
        'SortField = Literal["execution_date"]\n'
        'SortDirection = Literal["asc", "desc"]\n'
        "from example_api.models.split import Split\n",
    )
    (package / "models" / "_exports.py").write_text(
        'from example_api.models.split import Split\n__all__ = ("Split",)\n',
    )

    sys.path.insert(0, str(tmp_path))
    try:
        generated: Any = importlib.import_module("example_api")
        router = FakeRouter().add_pages(
            "/stocks/v1/splits",
            [[{"ticker": "AAPL", "execution_date": "2020-08-31"}]],
        )
        api = generated.AsyncExampleApi(
            AsyncSpitzeisenConfig(
                base_url="https://api.example.test",
                http_client=httpx2.AsyncClient(transport=router.mock_transport()),
                limiter=NoLimit(),
            ),
        )

        async def exercise() -> list[Any]:
            async with api:
                with pytest.raises(ValueError, match=r"Required param 'ticker' was not provided"):
                    await api.splits_api.get_splits(ticker=None)
                return await api.splits_api.get_splits(
                    ticker="AAPL",
                    adjustment_types=["forward_split"],
                )

        records = asyncio.run(exercise())
    finally:
        sys.path.remove(str(tmp_path))
        for module_name in tuple(sys.modules):
            if module_name == "example_api" or module_name.startswith("example_api."):
                del sys.modules[module_name]

    assert records[0].ticker == "AAPL"
    assert records[0].execution_date == date(2020, 8, 31)
    assert router.requests[0].params["adjustment_type.any_of"] == "forward_split"
    assert router.requests[0].params["sort"] == "execution_date.desc"
