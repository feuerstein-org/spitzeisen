"""OpenAPI compatibility and external-input orchestration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from codegen_fixtures import frontend_result

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.inputs import load_compile_inputs, load_mapping
from spitzeisen.codegen.openapi import ImportedSmithy, import_openapi, validate_openapi_model_presence

if TYPE_CHECKING:
    from spitzeisen.codegen.java_frontend import FrontendResult


def openapi_document(*, version: str = "3.0.3", paths: dict[str, object] | None = None) -> dict[str, Any]:
    """Build a small vendor document."""
    return {
        "openapi": version,
        "info": {"title": "Fixture", "version": "1.0.0"},
        "paths": paths or {},
    }


def test_yaml_and_json_loading() -> None:
    """Exact JSON content types select JSON; every other source is parsed as YAML."""
    as_json = load_mapping(b'{"openapi": "3.0.3"}', "application/json")
    as_yaml = load_mapping(b"openapi: 3.0.3\n", "application/yaml")

    assert as_json == {"openapi": "3.0.3"}
    assert as_yaml == {"openapi": "3.0.3"}


@pytest.mark.parametrize("schema", [{"type": "string", "nullable": True}, {"type": "integer", "default": 0}])
def test_lossy_openapi_presence_is_rejected(schema: dict[str, Any]) -> None:
    """A shared Smithy backend must not silently discard source nullability or defaults."""
    spec = {"components": {"schemas": {"Item": {"type": "object", "properties": {"value": schema}}}}}
    with pytest.raises(CodegenError, match="/components/schemas/Item/properties/value"):
        validate_openapi_model_presence(spec)


def test_presence_guard_ignores_property_names_and_example_data() -> None:
    """The guard checks schema keywords, not arbitrary dictionaries in an OpenAPI document."""
    spec = {
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "properties": {"default": {"type": "string"}, "nullable": {"type": "boolean"}},
                    "example": {"default": "value", "nullable": True},
                }
            }
        }
    }
    validate_openapi_model_presence(spec)


def test_document_loading_rejects_invalid_or_non_mapping_input() -> None:
    """Malformed external documents receive concise input errors."""
    with pytest.raises(CodegenError, match="Expecting property name"):
        load_mapping(b"{", "application/json")
    with pytest.raises(CodegenError, match="mapping at its top level"):
        load_mapping(b"[]", "application/json")


@pytest.mark.parametrize("version", [None, 3.0, "2.0", "3.1.0", "3.1.1", "3.2.0", "3.0", "3.0.", "3.0.3-extra"])
def test_import_rejects_unsupported_versions_before_launch_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    """Even simple 3.1 documents fail before resolving Java or creating output directories."""
    spec = openapi_document()
    if version is None:
        del spec["openapi"]
    else:
        spec["openapi"] = version
    launcher = Mock()
    monkeypatch.setattr("spitzeisen.codegen.openapi._converter_command", launcher)
    working_directory = tmp_path / "conversion"

    with pytest.raises(CodegenError, match=r"Expected OpenAPI 3\.0\.x"):
        import_openapi(spec, working_directory=working_directory)

    launcher.assert_not_called()
    assert not working_directory.exists()


@pytest.mark.parametrize("version", ["3.0.0", "3.0.3", "3.0.4"])
def test_openapi_30_reaches_converter_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Supported documents preserve their version, property names, and example data."""
    spec = openapi_document(version=version)
    spec["components"] = {
        "schemas": {
            "Item": {
                "type": "object",
                "properties": {"const": {"type": "string"}},
                "example": {"const": "one", "type": ["string", "null"]},
            },
        },
    }
    original = json.dumps(spec)
    model: dict[str, Any] = {"smithy": "2.0", "shapes": {}}
    launcher = Mock(return_value=["smithytranslate"])
    monkeypatch.setattr("spitzeisen.codegen.openapi._converter_command", launcher)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        source = Path(command[command.index("--input") + 1])
        assert json.loads(source.read_text()) == json.loads(original)
        assert "--validate-input" in command
        (Path(command[-1]) / "result.json").write_text(json.dumps(model))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.openapi.subprocess.run", run)
    imported = import_openapi(spec, working_directory=tmp_path)

    launcher.assert_called_once_with()
    assert imported == ImportedSmithy(model, ())
    assert json.dumps(spec) == original


def test_openapi_pipeline_passes_the_assembled_model_to_java(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported OpenAPI uses Java client semantics for operations and Pydantic response models."""
    spec = openapi_document(paths={"/things": {"get": {"operationId": "listThings"}}})
    source = tmp_path / "openapi.json"
    source.write_text(json.dumps(spec))
    overlay = tmp_path / "example.smithy"
    overlay.write_text('$version: "2"\nnamespace example.overlay\n')
    imported: dict[str, Any] = {"smithy": "2.0", "shapes": {"example#Imported": {"type": "string"}}}
    assembled: dict[str, Any] = {"smithy": "2.0", "shapes": {"example#Assembled": {"type": "string"}}}

    def import_model(_spec: dict[str, Any]) -> ImportedSmithy:
        return ImportedSmithy(imported, ())

    monkeypatch.setattr("spitzeisen.codegen.inputs.import_openapi", import_model)

    def assemble(model: dict[str, Any], sources: tuple[Path, ...]) -> dict[str, Any]:
        assert model is imported
        assert sources == (overlay,)
        return assembled

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)

    def compile_frontend(
        model: dict[str, Any],
        *,
        service: str | None,
        working_directory: Path | None,
        **settings: object,
    ) -> FrontendResult:
        assert model is assembled
        assert settings["package"] == "test_sdk"
        assert service is None
        assert working_directory == tmp_path
        return frontend_result()

    monkeypatch.setattr("spitzeisen.codegen.inputs.compile_smithy_frontend", compile_frontend)

    inputs = load_compile_inputs(
        package="test_sdk",
        client_name="Client",
        source=source,
        overlays=(overlay,),
        timeout=5,
    )

    assert len(inputs.frontend.modules) == 2
    assert inputs.model_schema == inputs.frontend.model_schema
    assert not inputs.warnings


def test_native_pipeline_uses_the_java_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native Smithy bypasses OpenAPI and uses both artifacts from the Java frontend."""
    source = tmp_path / "weather.smithy"
    source.write_text('$version: "2"\nnamespace native.weather\n')
    overlay = tmp_path / "sdk.smithy"
    overlay.write_text('$version: "2"\nnamespace native.overlay\n')
    assembled: dict[str, Any] = {"smithy": "2.0", "shapes": {}}
    schema: dict[str, Any] = {"$defs": {"Weather": {"type": "object"}}}

    def assemble(imported: dict[str, Any], sources: tuple[Path, ...]) -> dict[str, Any]:
        assert imported == {"smithy": "2.0", "shapes": {}}
        assert sources == (source, overlay)
        return assembled

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)

    def compile_frontend(*_args: object, **_kwargs: object) -> FrontendResult:
        return frontend_result(schema)

    monkeypatch.setattr("spitzeisen.codegen.inputs.compile_smithy_frontend", compile_frontend)

    inputs = load_compile_inputs(
        package="test_sdk",
        client_name="Client",
        smithy_sources=(source,),
        overlays=(overlay,),
        timeout=5,
    )

    assert inputs.model_schema == schema
    assert inputs.frontend == frontend_result(schema)
