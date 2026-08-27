"""The reproducible code-generation command and its user-facing failures."""

import json
from pathlib import Path
from typing import Any

import pytest
from smithy_fixtures import smithy_model
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
