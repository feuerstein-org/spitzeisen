"""The packaged Java frontend boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from codegen_fixtures import frontend_result

from spitzeisen.codegen.assembly import SMITHY_CLI_COORDINATE
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.java_frontend import (
    PLUGIN_NAME,
    SMITHY_CODEGEN_CORE_COORDINATE,
    SMITHY_JSONSCHEMA_COORDINATE,
    compile_smithy_frontend,
    smithy_build_command,
)
from spitzeisen.codegen.toolchain import ALLOY_CORE_COORDINATE


def _model() -> dict[str, Any]:
    return {
        "smithy": "2.0",
        "shapes": {"native.weather#WeatherService": {"type": "service"}},
    }


def test_frontend_launcher_includes_every_thin_plugin_runtime_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundled JAR stays reproducible and thin, so Coursier supplies its Smithy libraries."""
    plugin = tmp_path / "spitzeisen-python-codegen.jar"
    plugin.touch()

    def find_coursier(name: str) -> str | None:
        return "/tools/cs" if name == "cs" else None

    monkeypatch.setattr(
        "spitzeisen.codegen.java_frontend.shutil.which",
        find_coursier,
    )

    assert smithy_build_command(plugin) == [
        "/tools/cs",
        "launch",
        SMITHY_CLI_COORDINATE,
        SMITHY_JSONSCHEMA_COORDINATE,
        SMITHY_CODEGEN_CORE_COORDINATE,
        ALLOY_CORE_COORDINATE,
        "--extra-jars",
        str(plugin),
        "--",
    ]


def test_frontend_runs_smithy_build_and_loads_source_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process wrapper passes target settings and collects generated files and model metadata."""
    expected = frontend_result({"$defs": {"Weather": {"type": "object"}}})
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy-with-plugin", "--"])

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["smithy-with-plugin", "--"]
        output = Path(command[command.index("--output") + 1])
        config_path = Path(command[command.index("--config") + 1])
        config: dict[str, Any] = json.loads(config_path.read_text())
        settings = config["projections"]["spitzeisen"]["plugins"][PLUGIN_NAME]
        assert settings == {
            "service": "native.weather#WeatherService",
            "package": "test_sdk",
            "client_name": "Client",
            "python": {},
        }
        artifacts = output / "spitzeisen" / PLUGIN_NAME
        artifacts.mkdir(parents=True)
        (artifacts / "manifest.json").write_text(expected.manifest.model_dump_json())
        for module in expected.modules:
            path = artifacts / "sdk" / module.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(module.source)
        (artifacts / "model-schema.json").write_text(json.dumps(expected.model_schema))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.java_frontend.subprocess.run", run)

    actual = compile_smithy_frontend(
        _model(),
        package="test_sdk",
        client_name="Client",
        service="WeatherService",
        working_directory=tmp_path,
    )

    assert actual == expected
    assert not tuple(tmp_path.iterdir())


def test_frontend_reports_smithy_build_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Java validation failures keep their diagnostics at the CLI error boundary."""
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy-with-plugin", "--"])

    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, stdout="", stderr="invalid trait")

    monkeypatch.setattr(
        "spitzeisen.codegen.java_frontend.subprocess.run",
        fail,
    )

    with pytest.raises(CodegenError, match="invalid trait"):
        compile_smithy_frontend(
            _model(),
            package="test_sdk",
            client_name="Client",
        )


def test_frontend_rejects_an_unknown_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's local service-name convenience fails before launching Java when it drifts."""
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy-with-plugin", "--"])

    with pytest.raises(CodegenError, match=r"absent or ambiguous.*WeatherService"):
        compile_smithy_frontend(
            _model(),
            package="test_sdk",
            client_name="Client",
            service="RemovedWeatherService",
        )


@pytest.mark.parametrize("filename", ["../escaped.py", "/absolute.py", "bad\\path.py", "not_python.txt"])
def test_frontend_rejects_invalid_source_paths(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    """A corrupt build manifest cannot read outside the Java artifact directory."""
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy"])

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output") + 1]) / "spitzeisen" / PLUGIN_NAME
        output.mkdir(parents=True)
        manifest = frontend_result().manifest
        manifest.files = {filename: False}
        (output / "manifest.json").write_text(manifest.model_dump_json())
        (output / "model-schema.json").write_text("{}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.java_frontend.subprocess.run", run)
    with pytest.raises(CodegenError, match="invalid generated source path"):
        compile_smithy_frontend(_model(), package="test_sdk", client_name="Client")
