"""Compile real Smithy models, run Pydantic, import the SDK, and exercise both HTTP surfaces."""

from __future__ import annotations

import importlib
import inspect
import json
import shutil
import sys
from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from spitzeisen import AsyncSpitzeisenConfig, NoLimit, ResponseShapeError, SyncSpitzeisenConfig
from spitzeisen.codegen.assembly import assemble_smithy
from spitzeisen.codegen.cli import main
from spitzeisen.codegen.java_frontend import compile_smithy_frontend
from spitzeisen.testing import FakeRouter

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def toolchain() -> None:
    """These are build integration tests; ordinary Python unit tests remain toolchain-independent."""
    if not shutil.which("java") or not (shutil.which("coursier") or shutil.which("cs")):
        pytest.skip("Smithy SDK integration tests require Java and Coursier; run through mise")


def build(root: Path, source: Path, package: str, settings: Path | None = None) -> Path:
    """Run the public generation command, including the actual model backend."""
    args = [
        "--smithy",
        str(source),
        "--package",
        package,
        "--client-name",
        "Client",
        "--output-path",
        str(root / package),
    ]
    if settings is not None:
        args += ["--python-settings", str(settings)]
    runner = CliRunner()
    result = runner.invoke(main, ["generate", *args])
    assert result.exit_code == 0, result.output
    check = runner.invoke(main, ["check", *args])
    assert check.exit_code == 0, check.output
    return root / package


def import_sdk(root: Path, name: str) -> Generator[Any]:
    """Import a generated package, without leaving test packages cached in other tests."""
    sys.path.insert(0, str(root))
    try:
        yield importlib.import_module(name)
    finally:
        sys.path.remove(str(root))
        for module in tuple(sys.modules):
            if module == name or module.startswith(name + "."):
                del sys.modules[module]


@pytest.fixture(scope="module")
def native_sdk(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any]:
    """Include a service rename to check the Java-to-Pydantic naming contract end to end."""
    root = tmp_path_factory.mktemp("native-sdk")
    source = root / "weather.smithy"
    source.write_text(
        (ROOT / "examples/spec/native-weather.smithy")
        .read_text()
        .replace(
            'version: "1.0"',
            'version: "1.0"\n    rename: {"native.weather#Weather": "RenamedWeather"}',
        )
    )
    build(root, source, "native_sdk")
    yield from import_sdk(root, "native_sdk")


@pytest.fixture(scope="module")
def policy_sdk(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any]:
    """Generate the portable-policy fixture with real configured adapters and timestamp bindings."""
    root = tmp_path_factory.mktemp("policy-sdk")
    source = root / "policy.smithy"
    original = ROOT / "codegen/spitzeisen-smithy-codegen/src/test/resources/policy-features.smithy"
    text = original.read_text().replace(
        '@httpQuery("state")',
        '@httpQuery("epoch") @timestampFormat("epoch-seconds") epoch: Timestamp\n'
        '        @httpQuery("times") times: TimeList\n'
        '        @httpHeader("If-Modified-Since") modified: Timestamp\n'
        '        @httpQuery("state")',
    )
    text = text.replace('sortMember: "sort")', 'sortMember: "sort", separator: "::")')
    for field in ("created", "updated"):
        text = text.replace(field + ".", field + "::")
    text = text.replace('    UPDATED_DESC = "updated::desc"\n', "")
    text = text.replace(
        '@required\n        @httpHeader("X-Workspace")',
        '@required\n        @clientDefault(value: "main")\n        @httpHeader("X-Workspace")',
    )
    source.write_text(text + "\nlist TimeList { member: Timestamp }\n")
    package = root / "policy_sdk"
    package.mkdir()
    (package / "adapters.py").write_text(
        'def join_values(value, *, param_name):\n    return ",".join(value) if value else None\n'
        "def Record(value, *, param_name):\n    return value.upper() if value else None\n",
    )
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "input_adapters": {
                    "date": {
                        "function": {"module": "spitzeisen", "name": "coerce_date"},
                        "public_type": {"kind": "date_input"},
                    },
                    "comma-list": {
                        "function": {"module": "policy_sdk.adapters", "name": "join_values"},
                        "public_type": {"kind": "list", "members": [{"kind": "str"}]},
                    },
                    "custom": {
                        "function": {"module": "policy_sdk.adapters", "name": "Record"},
                        "public_type": {"kind": "str"},
                    },
                }
            }
        )
    )
    build(root, source, "policy_sdk", settings)
    yield from import_sdk(root, "policy_sdk")


async def invoke(sdk: Any, router: FakeRouter, async_: bool, accessor: str, method: str, **kwargs: Any) -> Any:
    """Exercise native async or sync code against the same scripted HTTP transport."""
    options: dict[str, Any] = {"base_url": "https://test.example", "limiter": NoLimit(), "owns_http_client": True}
    if async_:
        config = AsyncSpitzeisenConfig(http_client=httpx2.AsyncClient(transport=router.mock_transport()), **options)
        async with sdk.AsyncClient(config) as api:
            return await getattr(getattr(api, accessor), method)(**kwargs)
    config = SyncSpitzeisenConfig(http_client=httpx2.Client(transport=router.mock_transport()), **options)
    with sdk.SyncClient(config) as api:
        return getattr(getattr(api, accessor), method)(**kwargs)


@pytest.mark.parametrize("async_", [True, False])
async def test_native_sdk_imports_renamed_models_and_validates_responses(native_sdk: Any, async_: bool) -> None:
    """Required path arguments, response constraints, unknown fields, and HTTP 404 survive generation."""
    router = FakeRouter().add("/weather/Dublin", json={"temperature": 20, "status": "clear", "future": True})
    result = await invoke(native_sdk, router, async_, "weather_api", "get_weather", city="Dublin")
    assert isinstance(result, native_sdk.RenamedWeather)
    assert result.temperature == 20
    assert not hasattr(result, "future")
    router = FakeRouter().add("/weather/Dublin", status=404)
    assert await invoke(native_sdk, router, async_, "weather_api", "get_weather", city="Dublin") is None
    with pytest.raises(ValidationError):
        native_sdk.RenamedWeather.model_validate({"temperature": 101, "status": "clear"})
    with pytest.raises(TypeError, match="city"):
        await invoke(native_sdk, FakeRouter(), async_, "weather_api", "get_weather")
    with pytest.raises(ValueError, match="Required param"):
        await invoke(native_sdk, FakeRouter(), async_, "weather_api", "get_weather", city=None)


@pytest.mark.parametrize("async_", [True, False])
async def test_policy_sdk_serializes_and_paginates(policy_sdk: Any, async_: bool) -> None:
    """Test wire behavior and validated model aliases, not merely generated strings."""
    path = "/accounts/acct/records"
    router = FakeRouter().add_pages(
        path, [[{"id": "one", "displayName": "First"}], [{"id": "two"}]], results_key="records"
    )
    stamp = datetime(2024, 1, 2, tzinfo=UTC)
    records = await invoke(
        policy_sdk,
        router,
        async_,
        "records",
        "list_records",
        account_id="acct",
        workspace="main",
        filters=["a", "b"],
        since=date(2024, 1, 2),
        state="open",
        custom="x",
        epoch=stamp,
        times=[stamp],
        modified=stamp,
        max_results=2,
    )
    assert [record.id for record in records] == ["one", "two"]
    assert records[0].display_name == "First"
    assert isinstance(records[0], policy_sdk.Record)
    assert len(router.requests) == 2
    request = router.requests[0]
    assert request.params["filter"] == "a,b"
    assert request.params["since"] == "2024-01-02"
    assert request.params["epoch"] == "1704153600"
    assert request.params["times"] == "2024-01-02T00:00:00.000Z"
    assert request.params["custom"] == "X"
    assert request.params["sort"] == "created::desc"
    assert request.params["language"] == "en"
    assert request.params["limit"] == "2"
    assert request.params["page"] == "0"
    assert router.requests[1].params["page"] == "2"
    assert request.headers["x-workspace"] == "main"
    assert request.headers["if-modified-since"] == "Tue, 02 Jan 2024 00:00:00 GMT"
    assert "internal" not in request.params


@pytest.mark.parametrize("async_", [True, False])
async def test_policy_sdk_errors_and_validation_mode(policy_sdk: Any, async_: bool) -> None:
    """404, enum validation, required headers, and row validation have the same behavior on both surfaces."""
    params = {"account_id": "acct", "workspace": "main"}
    router = FakeRouter().add("/accounts/acct/records", status=404)
    assert await invoke(policy_sdk, router, async_, "records", "list_records", **params) is None
    with pytest.raises(ValueError, match="state"):
        await invoke(policy_sdk, FakeRouter(), async_, "records", "list_records", state="bad", **params)
    with pytest.raises(ValueError, match="workspace"):
        await invoke(policy_sdk, FakeRouter(), async_, "records", "list_records", account_id="acct", workspace=None)
    with pytest.raises(ValueError, match="sort"):
        await invoke(
            policy_sdk, FakeRouter(), async_, "records", "list_records", sort="updated", order="desc", **params
        )
    router = FakeRouter().add_pages(
        "/accounts/acct/records", [[{"id": "valid"}, {"missing": True}]], results_key="records"
    )
    rows = await invoke(policy_sdk, router, async_, "records", "list_records", on_validation_error="skip", **params)
    assert [row.id for row in rows] == ["valid"]


@pytest.mark.parametrize("value", [[], "wrong"])
async def test_single_response_rejects_nonobjects(native_sdk: Any, value: Any) -> None:
    """Raw single-response methods reject arrays and primitives before model validation."""
    router = FakeRouter().add("/weather/Dublin", json=value)
    with pytest.raises(ResponseShapeError):
        await invoke(native_sdk, router, async_=True, accessor="weather_api", method="get_weather_raw", city="Dublin")


def test_generated_signature_and_docs_are_native(native_sdk: Any, policy_sdk: Any) -> None:
    """Public methods expose useful signatures, readable docs, and native async/sync functions."""
    async_op = importlib.import_module("native_sdk._async.weather").AsyncWeatherApi
    sync_op = importlib.import_module("native_sdk._sync.weather").SyncWeatherApi
    assert inspect.iscoroutinefunction(async_op.get_weather)
    assert not inspect.iscoroutinefunction(sync_op.get_weather)
    assert inspect.signature(async_op.get_weather).parameters["city"].default is inspect.Parameter.empty
    assert "city=..." in async_op.get_weather.__doc__
    assert "async with AsyncClient" in async_op.get_weather.__doc__
    assert "with SyncClient" in sync_op.get_weather.__doc__
    assert native_sdk.RenamedWeather.__module__ == "native_sdk.models.renamed_weather"
    assert policy_sdk.Record.__module__ == "policy_sdk.models.record"


def test_imported_openapi_generates_an_importable_sdk(tmp_path: Path) -> None:
    """Keep the original OpenAPI model backend while generating operations from Smithy."""
    package = tmp_path / "openapi_sdk"
    args = [
        "--path",
        str(ROOT / "examples/spec/vendor.json"),
        "--overlay",
        str(ROOT / "examples/spec/weather.smithy"),
        "--package",
        "openapi_sdk",
        "--client-name",
        "Client",
        "--output-path",
        str(package),
    ]
    runner = CliRunner()
    generated = runner.invoke(main, ["generate", *args])
    assert generated.exit_code == 0, generated.output
    checked = runner.invoke(main, ["check", *args])
    assert checked.exit_code == 0, checked.output
    for sdk in import_sdk(tmp_path, "openapi_sdk"):
        assert sdk.AsyncClient.__name__ == "AsyncClient"
        assert sdk.CurrentWeatherResponse.__module__ == "openapi_sdk.models.current_weather_response"


def test_external_model_skips_the_model_backend() -> None:
    """An externally-owned response generates imports without unused model classes or schemas."""
    source = ROOT / "examples/spec/native-weather.smithy"
    assembled = assemble_smithy({"smithy": "2.0", "shapes": {}}, (source,))
    result = compile_smithy_frontend(
        assembled,
        package="test_sdk",
        client_name="Client",
        python_settings={
            "external_models": {"native.weather#Weather": {"module": "external.models", "symbol": "Observation"}},
        },
    )
    assert result.manifest.models[0].name == "Observation"
    assert not result.manifest.models[0].generated
    assert result.model_schema.get("$defs", {}) == {}
    source_text = next(
        module.source for module in result.modules if module.path.as_posix() == "_async/_generated/weather.py"
    )
    assert "from external.models import Observation" in source_text
    assert "return Observation.model_validate(raw)" in source_text
