"""The reproducible code-generation command and its user-facing failures."""

import importlib
import json
import shutil
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from plan_fixtures import native_weather_service_plan, things_plan, things_service_plan
from pydantic import ValidationError
from typer.testing import CliRunner

from spitzeisen.codegen import cli as codegen_cli
from spitzeisen.codegen.cli import main
from spitzeisen.codegen.java_frontend import FrontendResult
from spitzeisen.codegen.openapi import ImportedSmithy
from spitzeisen.codegen.python_context import PythonSettings
from spitzeisen.codegen.python_lowering import lower_service_plan as production_lower_service_plan
from spitzeisen.codegen.python_plan import PythonPlan
from spitzeisen.codegen.service_plan import ServicePlan


@pytest.fixture(autouse=True)
def converted_smithy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI transaction tests offline while preserving the production process boundary."""

    def convert(spec: dict[str, Any], *, working_directory: Path | None = None) -> ImportedSmithy:
        del spec, working_directory
        return ImportedSmithy(model={"smithy": "2.0", "shapes": {}}, warnings=())

    monkeypatch.setattr("spitzeisen.codegen.inputs.import_openapi", convert)
    monkeypatch.setattr("spitzeisen.codegen.cli.import_openapi", convert)

    def assemble(
        imported: dict[str, Any],
        overlays: tuple[Path, ...],
        *,
        working_directory: Path | None = None,
    ) -> dict[str, Any]:
        del overlays, working_directory
        return imported

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)
    monkeypatch.setattr("spitzeisen.codegen.cli.assemble_smithy", assemble)

    def compile_frontend(
        assembled: dict[str, Any],
        *,
        service: str | None,
        working_directory: Path | None,
    ) -> FrontendResult:
        del assembled, service, working_directory
        return FrontendResult(
            service_plan=things_service_plan(),
            model_schema={},
        )

    monkeypatch.setattr("spitzeisen.codegen.inputs.compile_smithy_frontend", compile_frontend)

    def lower(plan: ServicePlan, settings: PythonSettings) -> PythonPlan:
        if plan.service.id == "example#ExampleService":
            return things_plan(package=settings.package, client_name=settings.client_name)
        return production_lower_service_plan(plan, settings)

    monkeypatch.setattr(codegen_cli, "lower_service_plan", lower)


def project(root: Path) -> tuple[Path, Path, Path]:
    """Create explicit OpenAPI and Smithy overlay inputs without invoking the model backend."""
    input_dir = root / "spec"
    package_root = root / "example_sdk"
    input_dir.mkdir()
    (package_root / "models").mkdir(parents=True)
    (package_root / "models" / "thing.py").write_text(
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
    assert "doctor" in help_result.output
    assert "\n  models " not in help_result.output
    assert "\n  operations " not in help_result.output
    assert generated.exit_code == 0, generated.output
    assert "generated 15 files" in generated.output
    assert checked.exit_code == 0, checked.output
    assert "15 generated or scaffolded modules are up to date" in checked.output
    package_source = (package_root / "__init__.py").read_text()
    assert "from example_sdk._async.client import AsyncExampleApi" in package_source
    assert "from example_sdk._sync.client import SyncExampleApi" in package_source
    assert "from example_sdk.models import Thing" not in package_source

    sys.path.insert(0, str(tmp_path))
    try:
        generated_package = importlib.import_module("example_sdk")
        generated_models = importlib.import_module("example_sdk.models")
        assert generated_package.AsyncExampleApi.__name__ == "AsyncExampleApi"
        assert not hasattr(generated_models, "Thing")
    finally:
        sys.path.remove(str(tmp_path))
        for module_name in tuple(sys.modules):
            if module_name == "example_sdk" or module_name.startswith("example_sdk."):
                del sys.modules[module_name]


def test_cli_loads_python_only_target_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """External symbols and adapter implementations enter only the Python lowering boundary."""
    openapi, overlay, package_root = project(tmp_path)
    settings_path = tmp_path / "python-target.json"
    settings_path.write_text(
        json.dumps(
            {
                "external_models": {
                    "example#Thing": {
                        "module": "records.models",
                        "symbol": "Record",
                        "dependencies": ["records-runtime>=1"],
                    },
                },
                "input_adapters": {
                    "example.adapters#date": {
                        "function": {"module": "example_sdk.params", "name": "coerce_date"},
                        "public_type": {"kind": "date_input"},
                    },
                },
                "protocol_preference": ["spitzeisen.protocols#genericRestJson"],
            },
        ),
    )
    captured: list[PythonSettings] = []

    def lower(plan: ServicePlan, settings: PythonSettings) -> PythonPlan:
        del plan
        captured.append(settings)
        return things_plan(package=settings.package, client_name=settings.client_name)

    monkeypatch.setattr(codegen_cli, "lower_service_plan", lower)

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
            "--python-settings",
            str(settings_path),
            "--output-path",
            str(package_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0].external_models["example#Thing"].module == "records.models"
    assert captured[0].input_adapters["example.adapters#date"].public_type.kind == "date_input"
    assert captured[0].protocol_preference == ("spitzeisen.protocols#genericRestJson",)


def test_cli_accepts_an_external_model_exported_by_a_local_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local external-model module may be either a Python file or a package ``__init__``."""
    openapi, overlay, package_root = project(tmp_path)
    (package_root / "models" / "__init__.py").write_text(
        "from spitzeisen import SpitzeisenModel\n\nclass Thing(SpitzeisenModel):\n    pass\n",
    )
    external = things_plan(package="example_sdk", client_name="ExampleApi")
    operation = replace(external.operations[0], model_module="example_sdk.models")
    external = replace(external, operations=(operation,))

    def lower_external(plan: ServicePlan, settings: PythonSettings) -> PythonPlan:
        del plan, settings
        return external

    monkeypatch.setattr(codegen_cli, "lower_service_plan", lower_external)

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

    assert result.exit_code == 0, result.output
    assert "from example_sdk.models import Thing" in (package_root / "_async" / "_generated" / "things.py").read_text()


def test_cli_reports_invalid_python_target_settings_without_a_traceback(tmp_path: Path) -> None:
    """Target-config structural errors are concise and occur before compilation."""
    openapi, overlay, package_root = project(tmp_path)
    settings_path = tmp_path / "python-target.json"
    settings_path.write_text('{"external_models": []}')

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
            "--python-settings",
            str(settings_path),
            "--output-path",
            str(package_root),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid Python target settings" in result.output
    assert "external_models must be an object" in result.output
    assert "Traceback" not in result.output


def test_doctor_checks_the_packaged_frontend_without_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed local prerequisite is visible before a full generation transaction starts."""
    bundle = tmp_path / "spitzeisen-service-plan.jar"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "META-INF/services/software.amazon.smithy.build.SmithyBuildPlugin",
            "org.example.Plugin\n",
        )
        for name in ("spitzeisen-api.smithy", "spitzeisen-protocols.smithy", "spitzeisen-python.smithy"):
            archive.writestr(f"META-INF/smithy/{name}", '$version: "2"\n')
    monkeypatch.setattr(codegen_cli, "PLUGIN_JAR", bundle)

    def which(executable: str) -> str:
        return f"/{executable}"

    monkeypatch.setattr(shutil, "which", which)

    def command_output(command: list[str]) -> tuple[bool, str]:
        return True, 'openjdk version "25.0.4"' if command[-1] == "-version" else "2.1.24"

    monkeypatch.setattr(codegen_cli, "_command_output", command_output)

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "OK   Java" in result.output
    assert "OK   Coursier" in result.output
    assert "OK   Bundled Smithy plugin" in result.output
    assert "pinned CLI and JSON Schema frontend 1.73.0" in result.output


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
    model: dict[str, Any] = {"smithy": "2.0", "shapes": {}}
    schema: dict[str, Any] = {
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

    def compile_frontend(
        assembled: dict[str, Any],
        *,
        service: str | None,
        working_directory: Path | None,
    ) -> FrontendResult:
        del assembled, service
        assert working_directory == tmp_path
        return FrontendResult(
            service_plan=native_weather_service_plan(),
            model_schema=schema,
        )

    monkeypatch.setattr("spitzeisen.codegen.inputs.assemble_smithy", assemble)
    monkeypatch.setattr("spitzeisen.codegen.inputs.compile_smithy_frontend", compile_frontend)
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
        generated_package = importlib.import_module("native_weather_sdk")
        generated_models = importlib.import_module("native_weather_sdk.models")
        assert generated_package.AsyncNativeWeatherApi.__name__ == "AsyncNativeWeatherApi"
        assert generated_package.SyncNativeWeatherApi.__name__ == "SyncNativeWeatherApi"
        assert generated_package.Weather is generated_models.Weather
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
