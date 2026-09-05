"""CLI transaction tests; actual SDK behavior is tested through both real generators."""

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest
from codegen_fixtures import frontend_result
from typer.testing import CliRunner

from spitzeisen.codegen import cli as codegen_cli
from spitzeisen.codegen.artifacts import ModelArtifact
from spitzeisen.codegen.cli import main
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.generate import GeneratedModule
from spitzeisen.codegen.inputs import BuildInputs


@pytest.fixture
def compiled(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept the expensive build boundary for file-management unit tests."""
    received: dict[str, Any] = {}

    def load(**settings: Any) -> BuildInputs:
        received.update(settings)
        return BuildInputs({}, "jsonschema", frontend_result(), ())

    monkeypatch.setattr(codegen_cli, "load_compile_inputs", load)
    return received


def arguments(root: Path) -> list[str]:
    """Select a native model and package destination."""
    return [
        "--smithy",
        str(root / "source.smithy"),
        "--package",
        "test_sdk",
        "--client-name",
        "Client",
        "--output-path",
        str(root / "test_sdk"),
    ]


def test_generation_preserves_extensions_and_check_detects_drift(tmp_path: Path, compiled: dict[str, Any]) -> None:
    """Generation replaces implementations, preserves extensions, and removes stale generated files."""
    runner = CliRunner()
    args = arguments(tmp_path)
    result = runner.invoke(main, ["generate", *args])
    assert result.exit_code == 0, result.output
    assert compiled["package"] == "test_sdk"
    root = tmp_path / "test_sdk"
    public = root / "__init__.py"
    public.write_text("# user customization\n")
    generated = root / "_async/_generated/client.py"
    generated.write_text("VALUE = 99\n")
    stale = generated.with_name("removed.py")
    stale.write_text("# stale generated code\n")
    checked = runner.invoke(main, ["check", *args])
    assert checked.exit_code == 1
    assert str(generated) in checked.output
    assert str(stale) in checked.output
    assert public.read_text() == "# user customization\n"
    regenerated = runner.invoke(main, ["generate", *args])
    assert regenerated.exit_code == 0, regenerated.output
    assert "preserved" in regenerated.output
    assert not stale.exists()
    assert generated.read_text() == "VALUE = 1\n"
    assert public.read_text() == "# user customization\n"
    checked = runner.invoke(main, ["check", *args])
    assert checked.exit_code == 0, checked.output


def test_target_settings_are_forwarded_to_java(tmp_path: Path, compiled: dict[str, Any]) -> None:
    """Python loads JSON; target semantics have one owner in Java."""
    path = tmp_path / "python.json"
    settings = {"input_adapters": {"vendor-date": {"function": {"module": "sdk.adapters", "name": "date"}}}}
    path.write_text(json.dumps(settings))
    result = CliRunner().invoke(main, ["generate", *arguments(tmp_path), "--python-settings", str(path)])
    assert result.exit_code == 0, result.output
    assert compiled["python_settings"] == settings


@pytest.mark.parametrize("source", ["{", "[]"])
def test_invalid_settings_fail_without_writing(tmp_path: Path, source: str) -> None:
    """Invalid JSON never reaches Java or writes SDK files."""
    path = tmp_path / "python.json"
    path.write_text(source)
    result = CliRunner().invoke(main, ["generate", *arguments(tmp_path), "--python-settings", str(path)])
    assert result.exit_code == 1
    assert "Invalid Python target settings" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "test_sdk").exists()


def test_invalid_generated_source_does_not_replace_existing_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All emitted files are formatted and syntax-checked before replacement."""
    result = frontend_result()
    result.modules.append(GeneratedModule(Path("bad.py"), "class ???"))

    def load(**_: Any) -> BuildInputs:
        return BuildInputs({}, "jsonschema", result, ())

    monkeypatch.setattr(codegen_cli, "load_compile_inputs", load)
    root = tmp_path / "test_sdk"
    root.mkdir()
    existing = root / "__init__.py"
    existing.write_text("# keep me\n")
    generated = CliRunner().invoke(main, ["generate", *arguments(tmp_path)])
    assert generated.exit_code == 1
    assert existing.read_text() == "# keep me\n"
    assert not (root / "_async").exists()


def test_missing_external_model_and_dependency_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local package exports are accepted, missing local models fail, and dependencies are reported."""
    frontend = frontend_result()
    frontend.manifest.models = [ModelArtifact(name="Record", module="test_sdk.custom", generated=False)]
    frontend.manifest.dependencies = ["records-runtime>=1"]

    def load(**_: Any) -> BuildInputs:
        return BuildInputs({}, "jsonschema", frontend, ())

    monkeypatch.setattr(codegen_cli, "load_compile_inputs", load)
    args = arguments(tmp_path)
    missing = CliRunner().invoke(main, ["generate", *args])
    assert missing.exit_code == 1
    assert "existing local modules" in missing.output
    module = tmp_path / "test_sdk/custom"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("# handwritten model\n")
    generated = CliRunner().invoke(main, ["generate", *args])
    assert generated.exit_code == 0, generated.output
    assert "records-runtime>=1" in generated.output


def test_frontend_failures_keep_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expected Java errors remain concise at the public command boundary."""

    def fail(**_: Any) -> BuildInputs:
        raise CodegenError(header="Smithy Python generation failed", detail="unsupported HTTP method POST")

    monkeypatch.setattr(codegen_cli, "load_compile_inputs", fail)
    result = CliRunner().invoke(main, ["generate", *arguments(tmp_path)])
    assert result.exit_code == 1
    assert "unsupported HTTP method POST" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("extra", [[], ["--url", "https://example.test/spec", "--path", "spec.json"]])
def test_source_selection_is_a_usage_error(tmp_path: Path, extra: list[str]) -> None:
    """Ambiguous or absent input selections fail before generation."""
    args = ["--package", "test_sdk", "--client-name", "Client", "--output-path", str(tmp_path / "test_sdk")]
    result = CliRunner().invoke(main, ["generate", *args, *extra])
    assert result.exit_code == 2
    assert "Traceback" not in result.output


def test_doctor_checks_the_packaged_frontend_without_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed local prerequisite is visible before a full generation transaction starts."""
    bundle = tmp_path / "spitzeisen-python-codegen.jar"
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
