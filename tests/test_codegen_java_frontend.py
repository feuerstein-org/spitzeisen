"""The packaged Java frontend boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from plan_fixtures import native_weather_client

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.java_frontend import PLUGIN_NAME, FrontendResult, compile_smithy_frontend
from spitzeisen.codegen.plan import TargetSettings
from spitzeisen.codegen.plan_io import client_plan_document


def _expected() -> FrontendResult:
    return FrontendResult(
        client=native_weather_client(),
        model_schema={"$defs": {"Weather": {"type": "object"}}},
    )


def _model() -> dict[str, Any]:
    return {
        "smithy": "2.0",
        "shapes": {"native.weather#WeatherService": {"type": "service"}},
    }


def test_frontend_runs_smithy_build_and_loads_versioned_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process wrapper passes only target settings and consumes both plugin outputs."""
    expected = _expected()
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy-with-plugin", "--"])

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["smithy-with-plugin", "--"]
        output = Path(command[command.index("--output") + 1])
        config_path = Path(command[command.index("--config") + 1])
        config: dict[str, Any] = json.loads(config_path.read_text())
        settings = config["projections"]["spitzeisen"]["plugins"][PLUGIN_NAME]
        assert settings == {
            "package": "native_weather_sdk",
            "clientName": "NativeWeatherApi",
            "service": "native.weather#WeatherService",
            "vendor": "weather test",
        }
        artifacts = output / "spitzeisen" / PLUGIN_NAME
        artifacts.mkdir(parents=True)
        (artifacts / "client-plan.json").write_text(json.dumps(client_plan_document(expected.client)))
        (artifacts / "model-schema.json").write_text(json.dumps(expected.model_schema))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.java_frontend.subprocess.run", run)

    actual = compile_smithy_frontend(
        _model(),
        target=TargetSettings(
            package="native_weather_sdk",
            client_name="NativeWeatherApi",
            service="WeatherService",
            vendor="weather test",
        ),
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
            target=TargetSettings(package="native_weather_sdk", client_name="NativeWeatherApi"),
        )


def test_frontend_rejects_an_unknown_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's local service-name convenience fails before launching Java when it drifts."""
    monkeypatch.setattr("spitzeisen.codegen.java_frontend._frontend_command", lambda: ["smithy-with-plugin", "--"])

    with pytest.raises(CodegenError, match=r"absent or ambiguous.*WeatherService"):
        compile_smithy_frontend(
            _model(),
            target=TargetSettings(
                package="native_weather_sdk",
                client_name="NativeWeatherApi",
                service="RemovedWeatherService",
            ),
        )
