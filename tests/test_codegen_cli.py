"""The upstream-shaped, reproducible code-generation command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from spitzeisen.codegen.cli import main


def project(root: Path) -> tuple[Path, Path, Path]:
    """Create explicit OpenAPI and manifest inputs without invoking the model backend."""
    input_dir = root / "spec"
    package_root = root / "example_sdk"
    input_dir.mkdir()
    (package_root / "models").mkdir(parents=True)
    (package_root / "models" / "things.py").write_text(
        "from spitzeisen import SpitzeisenModel\n\nclass Thing(SpitzeisenModel):\n    pass\n",
    )
    manifest = input_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "vendor": "example",
                "base_url": "https://api.example.test",
                "package": "example_sdk",
                "client_name": "ExampleApi",
                "endpoints": {
                    "things": {
                        "path": "/things",
                        "method_name": "get_things",
                        "model": "Thing",
                        "generate_model": False,
                        "params": {"category": {"description": "Category to return."}},
                    },
                },
            },
        ),
    )
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
    return openapi, manifest, package_root


def test_cli_exposes_one_generation_transaction_and_one_drift_check(tmp_path: Path) -> None:
    """SDK authors do not have to coordinate independent model and endpoint phases."""
    openapi, manifest, package_root = project(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    generated = runner.invoke(
        main,
        [
            "generate",
            "--path",
            str(openapi),
            "--config",
            str(manifest),
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
            "--config",
            str(manifest),
            "--output-path",
            str(package_root),
        ],
    )

    assert help_result.exit_code == 0
    assert "generate" in help_result.output
    assert "check" in help_result.output
    assert "\n  models " not in help_result.output
    assert "\n  endpoints " not in help_result.output
    assert generated.exit_code == 0, generated.output
    assert "generated 13 files" in generated.output
    assert checked.exit_code == 0, checked.output
    assert "13 generated or scaffolded modules are up to date" in checked.output


def test_check_catches_modified_and_orphaned_generated_modules(tmp_path: Path) -> None:
    """CI checks both byte drift and files left behind after an endpoint is removed."""
    openapi, manifest, package_root = project(tmp_path)
    runner = CliRunner()
    arguments = [
        "--path",
        str(openapi),
        "--config",
        str(manifest),
        "--output-path",
        str(package_root),
    ]
    assert runner.invoke(main, ["generate", *arguments]).exit_code == 0

    generated = package_root / "_async" / "_generated" / "things.py"
    generated.write_text("# hand edited generated code\n")
    orphan = package_root / "_sync" / "_generated" / "removed_endpoint.py"
    orphan.write_text("# stale\n")

    result = runner.invoke(main, ["check", *arguments])

    assert result.exit_code == 1
    assert str(generated) in result.output
    assert str(orphan) in result.output
