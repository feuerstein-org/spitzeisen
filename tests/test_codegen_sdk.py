"""Compile real Smithy models, run Pydantic, import the SDK, and exercise both HTTP surfaces."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import shutil
import subprocess
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
from spitzeisen.codegen.toolchain import ALLOY_VERSION
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
        .replace("city: String", 'city: String\n @httpQuery("q") query: String\n @httpQueryParams extra: QueryValues')
        .replace("details: WeatherDetails", "details: WeatherDetails\n observed: Timestamp")
        + "\nmap QueryValues { key: String, value: QueryStrings }\nlist QueryStrings { member: String }\n"
    )
    settings = root / "settings.json"
    settings.write_text(json.dumps({"require_api_required_arguments": True}))
    build(root, source, "native_sdk", settings)
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


async def invoke(
    sdk: Any,
    router: FakeRouter,
    async_: bool,
    accessor: str,
    method: str,
    *,
    strict_inputs: bool = False,
    strict_response_validation: bool = False,
    **kwargs: Any,
) -> Any:
    """Exercise native async or sync code against the same scripted HTTP transport."""
    options: dict[str, Any] = {
        "base_url": "https://test.example",
        "limiter": NoLimit(),
        "owns_http_client": True,
        "strict_inputs": strict_inputs,
        "strict_response_validation": strict_response_validation,
    }
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
    evolved = native_sdk.RenamedWeather.model_validate({"temperature": 101, "status": "future"})
    assert evolved.status == "future"
    with pytest.raises(ValidationError):
        native_sdk.RenamedWeather.model_validate({"temperature": "invalid", "status": "clear"})
    with pytest.raises(TypeError, match="city"):
        await invoke(native_sdk, FakeRouter(), async_, "weather_api", "get_weather")
    with pytest.raises(ValueError, match="Required param"):
        await invoke(native_sdk, FakeRouter(), async_, "weather_api", "get_weather", city=None)


@pytest.mark.parametrize("async_", [True, False])
async def test_alloy_query_maps_and_response_timestamps(native_sdk: Any, async_: bool) -> None:
    """Query lists repeat keys, named bindings take precedence, and JSON timestamps default to date-time."""
    payload = {"temperature": 20, "status": "clear", "observed": "2026-09-06T12:34:56.789Z"}
    router = FakeRouter().add("/weather/Dublin", json=payload)
    result = await invoke(
        native_sdk,
        router,
        async_,
        "weather_api",
        "get_weather",
        city="Dublin",
        query="a b+c",
        extra={"q": ["ignored"], "tag": ["a/b", "c d"], "empty": []},
    )
    sent = router.requests[0]
    assert sent.params == {"q": "a b+c", "tag": ["a/b", "c d"]}
    assert "q=a%20b%2Bc" in sent.url
    assert "tag=a%2Fb&tag=c%20d" in sent.url
    assert result.observed == datetime(2026, 9, 6, 12, 34, 56, 789000, tzinfo=UTC)


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
    """404, explicit input validation, escape hatches, and row validation work on both surfaces."""
    params: dict[str, Any] = {"account_id": "acct", "workspace": "main"}
    router = FakeRouter().add("/accounts/acct/records", status=404)
    assert await invoke(policy_sdk, router, async_, "records", "list_records", **params) is None
    router = FakeRouter().add("/accounts/acct/records", json={"records": []})
    await invoke(
        policy_sdk,
        router,
        async_,
        "records",
        "list_records",
        account_id="acct",
        workspace=None,
        state="future",
        sort="updated",
        order="desc",
    )
    assert "x-workspace" not in router.requests[0].headers
    assert router.requests[0].params["state"] == "future"
    assert router.requests[0].params["sort"] == "updated::desc"
    with pytest.raises(ValidationError):
        await invoke(
            policy_sdk, FakeRouter(), async_, "records", "list_records", state="future", strict_inputs=True, **params
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


async def test_imported_openapi_generates_an_importable_sdk(tmp_path: Path) -> None:
    """Imported OpenAPI generates Pydantic models through the same Smithy client projection."""
    package = tmp_path / "openapi_sdk"
    overlay = tmp_path / "response.smithy"
    overlay.write_text(
        (ROOT / "examples/spec/weather.smithy").read_text()
        + "\napply openapi#CurrentWeatherResponse$cod @clientOptional\n"
        + 'apply openapi#CurrentWeatherResponse$name @default("unknown")\n'
        + "apply openapi#GetCurrentWeatherInput$lon @clientOptional\n"
    )
    args = [
        "--path",
        str(ROOT / "examples/spec/vendor.json"),
        "--overlay",
        str(overlay),
        "--require-api-required-arguments",
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
        data: dict[str, Any] = {
            "coord": {"lon": 10, "lat": 20},
            "weather": [],
            "main": {"temp": 20, "feels_like": 20, "pressure": 1000, "humidity": 50},
            "dt": 1,
            "timezone": 0,
            "id": 1,
            "rain": {"1h": 0.5},
            "extra": True,
        }
        result = sdk.CurrentWeatherResponse.model_validate(data)
        assert result.cod is None
        assert result.name == "unknown"
        assert result.rain.n1h == 0.5
        assert not hasattr(result, "extra")
        assert result.main.temp_min is None
        invalid = {**data, "main": {**data["main"], "humidity": 200}}
        with pytest.raises(ValidationError):
            sdk.CurrentWeatherResponse.model_validate(invalid, context={"strict_response_validation": True})
        assert sdk.CurrentWeatherResponse.model_validate(invalid).main.humidity == 200
        for async_ in (False, True):
            router = FakeRouter().add("/data/2.5/weather", json=data)
            await invoke(
                sdk, router, async_, "current_weather_api", "get_current_weather", latitude=None, longitude=None
            )
            assert "lat" not in router.requests[0].params
            assert "lon" not in router.requests[0].params
            surface = "_async" if async_ else "_sync"
            cls = "AsyncCurrentWeatherApi" if async_ else "SyncCurrentWeatherApi"
            op = getattr(importlib.import_module(f"openapi_sdk.{surface}.current_weather"), cls)
            parameters = inspect.signature(op.get_current_weather).parameters
            assert parameters["latitude"].default is inspect.Parameter.empty
            assert parameters["longitude"].default is None


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
    assert "return self._validate_response(raw, Observation)" in source_text


@pytest.fixture(scope="module")
def alloy_sdk(tmp_path_factory: pytest.TempPathFactory) -> Generator[tuple[Any, dict[str, Any]]]:
    """Use the published Alloy test models and expectations without copying their definitions."""
    root = tmp_path_factory.mktemp("alloy-protocol")
    coursier = shutil.which("coursier") or shutil.which("cs")
    assert coursier is not None
    output = subprocess.run(  # noqa: S603 - pinned public Maven artifacts
        [coursier, "fetch", "--classpath", f"com.disneystreaming.alloy:alloy-protocol-tests:{ALLOY_VERSION}"],
        capture_output=True,
        text=True,
        check=True,
    )
    dependencies = tuple(Path(path) for path in output.stdout.strip().split(os.pathsep))
    model = assemble_smithy({"smithy": "2.0", "shapes": {}}, dependencies)
    operations = ["Health", "GetEnum", "GetIntEnum"]
    model["shapes"] = {key: value for key, value in model["shapes"].items() if value["type"] != "service"}
    model["shapes"]["compliance#Service"] = {
        "type": "service",
        "version": "1",
        "traits": {"alloy#simpleRestJson": {}},
        "operations": [{"target": f"alloy.test#{operation}"} for operation in operations],
    }
    source = root / "upstream.smithy.json"
    source.write_text(json.dumps(model))
    build(root, source, "alloy_sdk")
    for sdk in import_sdk(root, "alloy_sdk"):
        yield sdk, model["shapes"]


@pytest.mark.parametrize("async_", [True, False])
@pytest.mark.parametrize(
    ("operation", "method"), [("Health", "health"), ("GetEnum", "get_enum"), ("GetIntEnum", "get_int_enum")]
)
async def test_published_alloy_client_cases(
    alloy_sdk: tuple[Any, dict[str, Any]],
    operation: str,
    method: str,
    async_: bool,
) -> None:
    """Check generated HTTP requests and Pydantic responses against upstream client cases."""
    sdk, shapes = alloy_sdk
    traits = shapes[f"alloy.test#{operation}"]["traits"]
    request = traits["smithy.test#httpRequestTests"][0]
    responses = traits.get("smithy.test#httpResponseTests", [])
    response: dict[str, Any] = responses[0] if responses else {"code": 200, "body": '{"status":"ok"}'}
    router = FakeRouter().add(
        request["uri"],
        status=response["code"],
        content=response["body"],
        headers=response.get("headers", {"Content-Type": "application/json"}),
    )
    result = await invoke(sdk, router, async_, f"{method}_api", method, **request.get("params", {}))
    recorded = router.requests[0]
    assert recorded.method == request["method"]
    assert recorded.path == request["uri"]
    assert recorded.body == request.get("body", "").encode()
    assert dict(httpx2.QueryParams("&".join(request.get("queryParams", [])))) == recorded.params
    for key, value in request.get("headers", {}).items():
        assert recorded.headers[key] == value
    if responses:
        assert result.model_dump(by_alias=True, exclude_unset=True) == response["params"]


@pytest.fixture(scope="module", params=[False, True])
def presence_sdk(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Generator[Any]:
    """Build once per signature policy; response checking changes without regeneration."""
    root = tmp_path_factory.mktemp("presence-sdk")
    source = ROOT / "codegen/spitzeisen-smithy-codegen/src/test/resources/client-presence.smithy"
    name = f"presence_{request.param}"
    settings = root / f"{name}.json"
    settings.write_text(json.dumps({"require_api_required_arguments": request.param}))
    build(root, source, name, settings)
    for sdk in import_sdk(root, name):
        yield sdk, request.param


@pytest.mark.parametrize("document_payload", [False, True])
async def test_handwritten_models_types_and_helpers_survive_regeneration(
    tmp_path: Path, document_payload: bool
) -> None:
    """A real SDK keeps Python-owned models, custom input types, and public helper methods."""
    package = tmp_path / "custom_sdk"
    package.mkdir()
    custom = package / "custom.py"
    custom.write_text(
        "from pydantic import BaseModel, model_validator\n"
        "class City(BaseModel):\n    name: str\n"
        "def encode_city(value, *, param_name):\n    return value.name\n"
        "class Observation(BaseModel):\n"
        "    temperature: float\n    status: str\n"
        "    @model_validator(mode='after')\n"
        "    def normalize(self):\n        self.status = self.status.strip()\n        return self\n"
        "    @property\n    def warm(self):\n        return self.temperature >= 20\n"
    )
    source = tmp_path / "model.smithy"
    source.write_text(
        (ROOT / "examples/spec/native-weather.smithy")
        .read_text()
        .replace(
            "@httpLabel",
            '@spitzeisen.api#inputAdapter(id: "city")\n        @httpLabel',
        )
    )
    response_shape = "native.weather#Weather"
    if document_payload:
        source.write_text(
            source.read_text().replace("weather: Weather", "weather: ObservationPayload")
            + "\ndocument ObservationPayload\n"
            + '\napply GetWeather @spitzeisen.api#result(path: ["weather"], cardinality: "single")\n'
        )
        response_shape = "native.weather#ObservationPayload"
    settings = tmp_path / "python.json"
    settings.write_text(
        json.dumps(
            {
                "external_models": {response_shape: {"module": "custom_sdk.custom", "symbol": "Observation"}},
                "input_adapters": {
                    "city": {
                        "function": {"module": "custom_sdk.custom", "name": "encode_city"},
                        "public_type": {"kind": "symbol", "module": "custom_sdk.custom", "name": "City"},
                    }
                },
            }
        )
    )
    build(tmp_path, source, "custom_sdk", settings)
    for folder, prefix, aw in [("_async", "Async", "await "), ("_sync", "Sync", "")]:
        (package / folder / "weather.py").write_text(
            f"from custom_sdk.{folder}._generated.weather import {prefix}WeatherApiBase\n"
            f"class {prefix}WeatherApi({prefix}WeatherApiBase):\n"
            f"    {'async ' if aw else ''}def is_warm(self, *, city):\n"
            f"        value = {aw}self.get_weather(city=city)\n"
            "        return value is not None and value.warm and value.status == 'clear'\n"
        )
    preserved = {
        path: path.read_bytes() for path in [custom, package / "_async/weather.py", package / "_sync/weather.py"]
    }
    build(tmp_path, source, "custom_sdk", settings)
    assert all(path.read_bytes() == content for path, content in preserved.items())
    for sdk in import_sdk(tmp_path, "custom_sdk"):
        city = importlib.import_module("custom_sdk.custom").City(name="Dublin")
        for async_ in (False, True):
            router = FakeRouter().add("/weather/Dublin", json={"temperature": 21, "status": " clear "})
            result = await invoke(sdk, router, async_, "weather_api", "is_warm", strict_inputs=True, city=city)
            assert result is True


def valid_result() -> dict[str, Any]:
    """A response containing only the members required by the client model."""
    return {"id": "one", "score": 5, "name": "ok", "state": "open", "level": 1, "child": {"value": "v"}}


@pytest.mark.parametrize("strict", [False, True])
def test_response_presence_defaults_and_nested_models(presence_sdk: Any, strict: bool) -> None:
    """Client optionality and declared defaults work with both response settings, recursively."""
    sdk, _ = presence_sdk
    context = {"strict_response_validation": strict}
    result = sdk.Result.model_validate(valid_result(), context=context)
    assert result.legacy is None
    assert result.ignored is None
    assert result.implicit is None
    assert result.old_child is None
    assert result.optional_child is None
    assert result.defaulted == "fallback"
    assert result.count == 7
    assert result.child.next is None
    assert result.child.value == "v"
    explicit_nulls = {
        **valid_result(),
        "legacy": None,
        "ignored": None,
        "oldChild": None,
        "optionalChild": None,
        "sparseList": [None, {"value": "v"}],
        "sparseMap": {"missing": None, "present": {"value": "v"}},
    }
    parsed = sdk.Result.model_validate(explicit_nulls, context=context)
    assert parsed.sparse_list[0] is None
    assert parsed.sparse_list[1].value == "v"
    assert parsed.sparse_map["missing"] is None
    invalid_patches: tuple[dict[str, Any], ...] = (
        {"id": None},
        {"child": None},
        {"child": {}},
        {"denseList": [None]},
        {"denseMap": {"bad": None}},
        {"score": "wrong"},
        {"state": []},
    )
    for patch in invalid_patches:
        with pytest.raises(ValidationError):
            sdk.Result.model_validate({**valid_result(), **patch}, context=context)
    missing = valid_result()
    del missing["id"]
    with pytest.raises(ValidationError):
        sdk.Result.model_validate(missing, context=context)


@pytest.mark.parametrize(
    "patch",
    [
        {"state": "future"},
        {"level": 999},
        {"score": 100},
        {"name": "x"},
        {"name": "too long"},
        {"name": "AB"},
        {"names": []},
        {"child": {"value": "v", "next": {"value": ""}}},
        {"scores": [5, 100]},
        {"states": ["open", "future"]},
        {"levels": [None, -1, 999]},
        {"codes": {"valid": "ok", "invalid": "AB"}},
    ],
)
def test_response_constraints_are_opt_in(presence_sdk: Any, patch: dict[str, Any]) -> None:
    """Known data types survive compatible constraint relaxation and new enum values by default."""
    sdk, _ = presence_sdk
    value = {**valid_result(), **patch, "futureField": True}
    result = sdk.Result.model_validate(value)
    assert not hasattr(result, "futureField")
    with pytest.raises(ValidationError):
        sdk.Result.model_validate(value, context={"strict_response_validation": True})
    assert sdk.Result.model_validate(value) == result


def test_response_checks_preserve_supported_collection_values(presence_sdk: Any) -> None:
    """Constraint checks respect sparse nulls, negative integer enums, and nested valid values."""
    sdk, _ = presence_sdk
    value = {**valid_result(), "scores": [0, 10], "states": ["open"], "levels": [None, -1], "codes": {"a": "ok"}}
    assert sdk.Result.model_validate(value, context={"strict_response_validation": True}) == sdk.Result.model_validate(
        value
    )


@pytest.mark.parametrize("async_", [False, True])
async def test_runtime_response_checks_preserve_models_and_raw_methods(presence_sdk: Any, async_: bool) -> None:
    """One generated SDK switches policy at runtime for single results and lists."""
    sdk, _ = presence_sdk
    evolved = {**valid_result(), "score": 100}
    params: dict[str, Any] = {"key": "key", "query": "q", "workspace": "w", "count": 0, "enabled": False}
    for strict in (False, True, False):
        router = FakeRouter().add("/search/key", json=evolved)
        if strict:
            with pytest.raises(ValidationError, match="score"):
                await invoke(sdk, router, async_, "search_api", "search", strict_response_validation=True, **params)
        else:
            result = await invoke(sdk, router, async_, "search_api", "search", **params)
            assert isinstance(result, sdk.Result)
            assert result.score == 100
        raw = await invoke(
            sdk,
            FakeRouter().add("/search/key", json=evolved),
            async_,
            "search_api",
            "search_raw",
            strict_response_validation=strict,
            **params,
        )
        assert raw == evolved

        rows = [valid_result(), evolved]
        for mode in ("raise", "skip"):
            router = FakeRouter().add("/results", json=rows)
            if strict and mode == "raise":
                with pytest.raises(ValidationError) as error:
                    await invoke(
                        sdk,
                        router,
                        async_,
                        "list_results_api",
                        "list_results",
                        strict_response_validation=True,
                        on_validation_error=mode,
                    )
                assert error.value.errors()[0]["loc"] == (1, "score")
            else:
                result = await invoke(
                    sdk,
                    router,
                    async_,
                    "list_results_api",
                    "list_results",
                    strict_response_validation=strict,
                    on_validation_error=mode,
                )
                assert len(result) == (1 if strict else 2)
                assert all(isinstance(row, sdk.Result) for row in result)


async def test_clients_use_independent_response_policies(presence_sdk: Any) -> None:
    """Clients sharing model classes can validate with different policies concurrently."""
    sdk, _ = presence_sdk
    payload = {**valid_result(), "state": "future"}
    params: dict[str, Any] = {"key": "key", "query": "q", "workspace": "w", "count": 0, "enabled": False}
    results = await asyncio.gather(
        *(
            invoke(
                sdk,
                FakeRouter().add("/search/key", json=payload),
                async_=True,
                accessor="search_api",
                method="search",
                strict_response_validation=strict,
                **params,
            )
            for strict in (False, True, False)
        ),
        return_exceptions=True,
    )
    assert isinstance(results[0], sdk.Result)
    assert isinstance(results[1], ValidationError)
    assert isinstance(results[2], sdk.Result)


@pytest.mark.parametrize("async_", [True, False])
@pytest.mark.parametrize("strict_inputs", [False, True])
async def test_required_typing_and_runtime_escape_hatch(presence_sdk: Any, async_: bool, strict_inputs: bool) -> None:
    """A type-checker suppression can omit query/header data without weakening public signatures."""
    sdk, required = presence_sdk
    surface = "_async" if async_ else "_sync"
    cls = "AsyncSearchApi" if async_ else "SyncSearchApi"
    operation = getattr(importlib.import_module(f"{sdk.__name__}.{surface}.search"), cls)
    params = inspect.signature(operation.search).parameters
    assert params["query"].annotation == ("str" if required else "str | None")
    assert params["query"].default is (inspect.Parameter.empty if required else None)
    assert params["legacy"].default is None
    assert params["ignored"].default is None
    assert params["limit"].default == 10
    assert params["limit"].annotation == ("int" if required else "int | None")
    router = FakeRouter().add("/search/key", json=valid_result())
    await invoke(
        sdk,
        router,
        async_,
        "search_api",
        "search",
        strict_inputs=strict_inputs,
        key="key",
        query=None,
        workspace=None,
        count=0,
        enabled=False,
    )
    sent = router.requests[0]
    assert "query" not in sent.params
    assert "legacy" not in sent.params
    assert "ignored" not in sent.params
    assert "x-workspace" not in sent.headers
    assert sent.params["count"] == "0"
    assert sent.params["enabled"] == "false"
    assert sent.params["limit"] == "10"
    empty = FakeRouter()
    with pytest.raises((ValueError, ValidationError), match="key"):
        await invoke(
            sdk,
            empty,
            async_,
            "search_api",
            "search",
            strict_inputs=strict_inputs,
            key=None,
            query=None,
            workspace=None,
            count=0,
            enabled=False,
        )
    assert not empty.requests
    if required:
        with pytest.raises(TypeError, match="query"):
            await invoke(sdk, empty, async_, "search_api", "search", key="key", workspace="w", count=0, enabled=False)
        assert not empty.requests
    else:
        router = FakeRouter().add("/search/key", json=valid_result())
        await invoke(sdk, router, async_, "search_api", "search", key="key")
        assert "query" not in router.requests[0].params
