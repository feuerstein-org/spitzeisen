"""OpenAPI compatibility and external-input orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from codegen_fixtures import frontend_result

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.inputs import load_compile_inputs, load_mapping
from spitzeisen.codegen.openapi import ImportedSmithy, project_openapi_for_converter

if TYPE_CHECKING:
    from spitzeisen.codegen.java_frontend import FrontendResult


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


def test_openapi_pipeline_passes_the_assembled_model_to_java(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAPI remains the model backend input while Java owns the assembled operation model."""
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
    assert inputs.model_input_type == "openapi"
    assert inputs.model_schema == spec
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

    assert inputs.model_input_type == "jsonschema"
    assert inputs.model_schema == schema
    assert inputs.frontend == frontend_result(schema)
