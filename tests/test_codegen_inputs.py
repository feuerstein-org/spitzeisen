"""OpenAPI import, Smithy parsing, and external-input orchestration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from smithy_fixtures import native_weather_model, smithy_model

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.inputs import load_compile_inputs, load_mapping, parse_smithy
from spitzeisen.codegen.ir import HTTPMethod, LiteralType, ParamLocationIR, PrimitiveKind, PrimitiveType
from spitzeisen.codegen.openapi import project_openapi_for_converter
from spitzeisen.codegen.policy import TargetSettings, compile_model
from spitzeisen.codegen.traits import PAGE_NUMBER_PAGINATION, SDK_OPERATION


def openapi_document(*, version: str = "3.1.0", paths: dict[str, object] | None = None) -> dict[str, Any]:
    """Build a small vendor document."""
    return {
        "openapi": version,
        "info": {"title": "Fixture", "version": "1.0.0"},
        "paths": paths or {},
    }


def test_yaml_and_json_loading() -> None:
    """Exact JSON content types select JSON; every other source is parsed as YAML."""
    as_json = load_mapping(b'{"openapi": "3.1.0"}', "application/json")
    as_yaml = load_mapping(b"openapi: 3.1.0\n", "application/yaml")

    assert as_json == {"openapi": "3.1.0"}
    assert as_yaml == {"openapi": "3.1.0"}


def test_document_loading_rejects_invalid_or_non_mapping_input() -> None:
    """Malformed external documents receive concise input errors."""
    with pytest.raises(CodegenError, match="Expecting property name"):
        load_mapping(b"{", "application/json")
    with pytest.raises(CodegenError, match="mapping at its top level"):
        load_mapping(b"[]", "application/json")


def test_smithy_http_bindings_constraints_and_documentation_are_parsed() -> None:
    """The generator frontend consumes Smithy traits rather than OpenAPI objects."""
    spec = openapi_document(
        paths={
            "/places/{place_id}": {
                "get": {
                    "operationId": "getPlace",
                    "description": "Return one place.",
                    "parameters": [
                        {
                            "name": "place_id",
                            "in": "path",
                            "required": True,
                            "description": "Place identifier.",
                            "schema": {"type": "integer"},
                        },
                        {
                            "name": "unit",
                            "in": "query",
                            "description": "Measurement unit.",
                            "schema": {"type": "string", "enum": ["metric", "imperial"], "default": "metric"},
                        },
                        {
                            "name": "X-Workspace",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                },
            },
        },
    )

    parsed = parse_smithy(smithy_model(spec))
    operation = parsed.operation_named("getPlace")

    assert operation is not None
    assert operation.method is HTTPMethod.GET
    assert operation.path == "/places/{place_id}"
    assert operation.summary == "Return one place."
    assert [param.location for param in operation.params] == [
        ParamLocationIR.PATH,
        ParamLocationIR.QUERY,
        ParamLocationIR.HEADER,
    ]
    assert operation.params[0].schema == PrimitiveType(PrimitiveKind.INTEGER)
    assert operation.params[0].required
    assert operation.params[0].description == "Place identifier."
    assert operation.params[1].schema == LiteralType(("metric", "imperial"))
    assert operation.params[1].has_default
    assert operation.params[1].default == "metric"


def test_smithy_parser_rejects_converter_placeholders() -> None:
    """A partial conversion cannot silently generate stringly typed SDK methods."""
    model = {
        "smithy": "2.0",
        "shapes": {
            "vendor#Unsupported": {
                "type": "structure",
                "members": {},
                "traits": {"smithytranslate#errorMessage": "Schema not supported"},
            },
        },
    }

    with pytest.raises(CodegenError, match="unsupported Smithy placeholders"):
        parse_smithy(model)


def test_openapi_31_compatibility_projection_is_explicit_and_bounded() -> None:
    """Simple nullable and const schemas project; genuinely 3.1-only schemas fail."""
    spec = openapi_document()
    spec["components"] = {
        "schemas": {
            "MaybeName": {"type": ["string", "null"]},
            "OnlyOne": {"const": "one"},
        },
    }

    projected = project_openapi_for_converter(spec)

    assert projected["openapi"] == "3.0.3"
    assert projected["components"]["schemas"]["MaybeName"] == {"type": "string", "nullable": True}
    assert projected["components"]["schemas"]["OnlyOne"] == {"enum": ["one"]}

    components = cast("dict[str, Any]", spec["components"])
    schemas = cast("dict[str, Any]", components["schemas"])
    schemas["Modern"] = {"$defs": {"nested": {"type": "string"}}}
    with pytest.raises(CodegenError, match=r"\$defs"):
        project_openapi_for_converter(spec)


def test_whole_input_pipeline_compiles_the_imported_smithy_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAPI is converted, parsed as Smithy, and combined with the reviewed SDK overlay."""
    spec = openapi_document(
        paths={
            "/things": {
                "get": {
                    "operationId": "listThings",
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 100}},
                    ],
                },
            },
        },
    )
    source = tmp_path / "openapi.json"
    source.write_text(json.dumps(spec))
    overlay = tmp_path / "example.smithy"
    overlay.write_text('$version: "2"\nnamespace example.overlay\n')

    def fake_converter(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[-1])
        (output / "result.json").write_text(json.dumps(smithy_model(spec)))
        return subprocess.CompletedProcess(command, 0, stdout=f"Writing {output / 'result.json'}\n", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.openapi._converter_command", lambda: ["smithytranslate"])
    monkeypatch.setattr("spitzeisen.codegen.openapi.subprocess.run", fake_converter)

    def fake_assembler(imported: dict[str, Any], overlays: tuple[Path, ...]) -> dict[str, Any]:
        assert overlays == (overlay,)
        operation = imported["shapes"]["example#ListThings"]
        operation["traits"][SDK_OPERATION] = {
            "name": "things",
            "methodName": "list_things",
            "responseModel": "Thing",
            "shape": "collection",
        }
        operation["traits"][PAGE_NUMBER_PAGINATION] = {"pageSize": "limit"}
        return imported

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", fake_assembler)

    inputs = load_compile_inputs(
        source=source,
        overlays=(overlay,),
        target=TargetSettings(package="example_sdk", client_name="ExampleApi"),
        timeout=5,
    )

    assert inputs.client.operations[0].method_name == "list_things"
    assert inputs.client.operations[0].page_size is not None
    assert inputs.client.operations[0].page_size.maximum == 100
    assert inputs.model_input_type == "openapi"
    assert inputs.model_schema == spec
    assert inputs.smithy["smithy"] == "2.0"
    assert not inputs.warnings


def test_native_smithy_pipeline_builds_a_json_schema_model_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native Smithy bypasses OpenAPI while retaining the Pydantic backend."""
    source = tmp_path / "weather.smithy"
    source.write_text('$version: "2"\nnamespace native.weather\n')
    overlay = tmp_path / "sdk.smithy"
    overlay.write_text('$version: "2"\nnamespace native.overlay\n')
    model = native_weather_model()

    def assemble(imported: dict[str, Any], sources: tuple[Path, ...]) -> dict[str, Any]:
        assert imported == {"smithy": "2.0", "shapes": {}}
        assert sources == (source, overlay)
        return model

    expected_schema = {"$defs": {"Weather": {"type": "object"}}}

    def convert_schema(
        assembled: dict[str, Any],
        *,
        service_id: str,
        response_shapes: tuple[str, ...],
        working_directory: Path | None,
    ) -> dict[str, Any]:
        assert assembled is model
        assert service_id == "native.weather#WeatherService"
        assert response_shapes == ("native.weather#Weather",)
        assert working_directory == tmp_path
        return expected_schema

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)
    monkeypatch.setattr("spitzeisen.codegen.inputs.smithy_to_json_schema", convert_schema)

    inputs = load_compile_inputs(
        smithy_sources=(source,),
        overlays=(overlay,),
        target=TargetSettings(package="native_weather_sdk", client_name="NativeWeatherApi"),
        timeout=5,
    )

    assert inputs.model_input_type == "jsonschema"
    assert inputs.model_schema == expected_schema
    assert inputs.client.operations[0].model == "Weather"
    assert inputs.client.response_shapes == ("native.weather#Weather",)


def test_native_smithy_service_rename_matches_the_generated_model_name() -> None:
    """Service renames used by Smithy's converter also reach client annotations."""
    model = native_weather_model()
    model["shapes"]["native.weather#WeatherService"]["rename"] = {
        "native.weather#Weather": "Observation",
    }

    client = compile_model(
        TargetSettings(package="native_weather_sdk", client_name="NativeWeatherApi"),
        parse_smithy(model),
    )

    assert client.operations[0].model == "Observation"
    assert client.response_shapes == ("native.weather#Weather",)
