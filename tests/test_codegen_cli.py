"""The reproducible code-generation command and its user-facing failures."""

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from smithy_fixtures import native_weather_model, smithy_model
from typer.testing import CliRunner

from spitzeisen.codegen.cli import main
from spitzeisen.codegen.openapi import ImportedSmithy
from spitzeisen.codegen.traits import PYTHON_PARAMETER, SDK_OPERATION


@pytest.fixture(autouse=True)
def converted_smithy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI transaction tests offline while crossing the real Smithy parser boundary."""

    def convert(spec: dict[str, Any], *, working_directory: Path | None = None) -> ImportedSmithy:
        del working_directory
        return ImportedSmithy(model=smithy_model(spec), warnings=())

    monkeypatch.setattr("spitzeisen.codegen.inputs.import_openapi", convert)
    monkeypatch.setattr("spitzeisen.codegen.cli.import_openapi", convert)

    def assemble(
        imported: dict[str, Any],
        overlays: tuple[Path, ...],
        *,
        working_directory: Path | None = None,
    ) -> dict[str, Any]:
        del overlays, working_directory
        operation = next(shape for shape in imported["shapes"].values() if shape.get("type") == "operation")
        operation["traits"][SDK_OPERATION] = {
            "name": "things",
            "methodName": "get_things",
            "responseModel": "Thing",
            "generateModel": False,
            "shape": "collection",
        }
        input_shape = imported["shapes"][operation["input"]["target"]]
        input_shape["members"]["category"]["traits"][PYTHON_PARAMETER] = {}
        input_shape["members"]["category"]["traits"]["smithy.api#documentation"] = "Category to return."
        return imported

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)
    monkeypatch.setattr("spitzeisen.codegen.cli.assemble_smithy", assemble)


def project(root: Path) -> tuple[Path, Path, Path]:
    """Create explicit OpenAPI and Smithy overlay inputs without invoking the model backend."""
    input_dir = root / "spec"
    package_root = root / "example_sdk"
    input_dir.mkdir()
    (package_root / "models").mkdir(parents=True)
    (package_root / "models" / "things.py").write_text(
        "from spitzeisen import SpitzeisenModel\n\nclass Thing(SpitzeisenModel):\n    pass\n",
    )
    overlay = input_dir / "example.smithy"
    overlay.write_text('$version: "2"\nnamespace example.overlay\n')
    openapi = input_dir / "openapi.json"
    openapi.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Example", "version": "1.0.0"},
                "paths": {
                    "/things": {
                        "get": {
                            "parameters": [
                                {"name": "category", "in": "query", "schema": {"type": "string"}},
                            ],
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            },
        ),
    )
    return openapi, overlay, package_root


def test_cli_exposes_one_generation_transaction_and_one_drift_check(tmp_path: Path) -> None:
    """SDK authors do not have to coordinate independent model and operation phases."""
    openapi, overlay, package_root = project(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    generated = runner.invoke(
        main,
        [
            "generate",
            "--path",
            str(openapi),
            "--overlay",
            str(overlay),
            "--package",
            "example_sdk",
            "--client-name",
            "ExampleApi",
            "--output-path",
            str(package_root),
        ],
    )
    checked = runner.invoke(
        main,
        [
            "check",
            "--path",
            str(openapi),
            "--overlay",
            str(overlay),
            "--package",
            "example_sdk",
            "--client-name",
            "ExampleApi",
            "--output-path",
            str(package_root),
        ],
    )

    assert help_result.exit_code == 0
    assert "generate" in help_result.output
    assert "check" in help_result.output
    assert "\n  models " not in help_result.output
    assert "\n  operations " not in help_result.output
    assert generated.exit_code == 0, generated.output
    assert "generated 13 files" in generated.output
    assert checked.exit_code == 0, checked.output
    assert "13 generated or scaffolded modules are up to date" in checked.output


def test_import_command_writes_an_inspectable_smithy_json_ast(tmp_path: Path) -> None:
    """The convenience command exposes the exact model consumed by the frontend."""
    openapi, _, _ = project(tmp_path)
    output = tmp_path / "model" / "vendor.smithy.json"

    result = CliRunner().invoke(
        main,
        ["import-openapi", "--path", str(openapi), "--output-path", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["smithy"] == "2.0"


def test_cli_generates_pydantic_models_from_native_smithy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intended Smithy-first path generates models and operation code together."""
    source = tmp_path / "weather.smithy"
    source.write_text('$version: "2"\nnamespace native.weather\n')
    package_root = tmp_path / "native_weather_sdk"
    model = native_weather_model()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {
            "Weather": {
                "type": "object",
                "required": ["temperature"],
                "properties": {
                    "temperature": {
                        "type": "number",
                        "minimum": -100,
                        "maximum": 100,
                        "description": "Air temperature in degrees Celsius.",
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            },
        },
    }

    def assemble(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return model

    def convert_schema(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return schema

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)
    monkeypatch.setattr("spitzeisen.codegen.inputs.smithy_to_json_schema", convert_schema)
    arguments = [
        "--smithy",
        str(source),
        "--package",
        "native_weather_sdk",
        "--client-name",
        "NativeWeatherApi",
        "--output-path",
        str(package_root),
    ]
    runner = CliRunner()

    generated = runner.invoke(main, ["generate", *arguments])
    checked = runner.invoke(main, ["check", *arguments])

    assert generated.exit_code == 0, generated.output
    assert checked.exit_code == 0, checked.output
    model_source = (package_root / "models" / "_generated.py").read_text()
    operation_source = (package_root / "_async" / "_generated" / "weather.py").read_text()
    assert "from the assembled Smithy model" in model_source
    assert "temperature: Annotated[float, Field(ge=-100.0, le=100.0)]" in model_source
    assert "summary: Annotated[str | None, Field(max_length=200, min_length=1)] = None" in model_source
    assert "city: str" in operation_source
    assert "return Weather.model_validate(raw)" in operation_source

    sys.path.insert(0, str(tmp_path))
    try:
        generated_models = importlib.import_module("native_weather_sdk.models")
        weather = generated_models.Weather.model_validate({"temperature": 20, "futureField": True})
        assert weather.temperature == 20
        assert not hasattr(weather, "futureField")
        with pytest.raises(ValidationError):
            generated_models.Weather.model_validate({"temperature": 101})
    finally:
        sys.path.remove(str(tmp_path))
        for module_name in tuple(sys.modules):
            if module_name == "native_weather_sdk" or module_name.startswith("native_weather_sdk."):
                del sys.modules[module_name]


def test_check_catches_modified_and_orphaned_generated_modules(tmp_path: Path) -> None:
    """CI checks both byte drift and files left behind after an operation is removed."""
    openapi, overlay, package_root = project(tmp_path)
    runner = CliRunner()
    arguments = [
        "--path",
        str(openapi),
        "--overlay",
        str(overlay),
        "--package",
        "example_sdk",
        "--client-name",
        "ExampleApi",
        "--output-path",
        str(package_root),
    ]
    assert runner.invoke(main, ["generate", *arguments]).exit_code == 0

    generated = package_root / "_async" / "_generated" / "things.py"
    generated.write_text("# hand edited generated code\n")
    orphan = package_root / "_sync" / "_generated" / "removed_operation.py"
    orphan.write_text("# stale\n")

    result = runner.invoke(main, ["check", *arguments])

    assert result.exit_code == 1
    assert str(generated) in result.output
    assert str(orphan) in result.output


def test_cli_formats_invalid_input_without_a_traceback(tmp_path: Path) -> None:
    """Expected input failures are concise domain errors rather than return-value unions."""
    openapi, overlay, package_root = project(tmp_path)
    openapi.write_text("{")

    result = CliRunner().invoke(
        main,
        [
            "generate",
            "--path",
            str(openapi),
            "--overlay",
            str(overlay),
            "--package",
            "example_sdk",
            "--client-name",
            "ExampleApi",
            "--output-path",
            str(package_root),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid JSON from provided source" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_source_selection_as_a_usage_error(tmp_path: Path) -> None:
    """Mutually exclusive CLI options use Typer's standard usage-error path."""
    openapi, overlay, package_root = project(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "generate",
            "--path",
            str(openapi),
            "--url",
            "https://example.test/openapi.json",
            "--overlay",
            str(overlay),
            "--package",
            "example_sdk",
            "--client-name",
            "ExampleApi",
            "--output-path",
            str(package_root),
        ],
    )

    assert result.exit_code == 2
    assert "provide either --url or --path, not both" in result.output

    native_result = CliRunner().invoke(
        main,
        [
            "generate",
            "--path",
            str(openapi),
            "--smithy",
            str(overlay),
            "--package",
            "example_sdk",
            "--client-name",
            "ExampleApi",
            "--output-path",
            str(package_root),
        ],
    )

    assert native_result.exit_code == 2
    assert "provide native --smithy sources" in native_result.output
    assert "OpenAPI --url/--path, not both" in native_result.output
