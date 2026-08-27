"""The official Smithy-to-JSON-Schema model-backend boundary."""

import json
import subprocess
from pathlib import Path

import pytest

from spitzeisen.codegen import smithy_jsonschema as converter
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.smithy_jsonschema import smithy_to_json_schema


def test_bridge_command_honors_an_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A packaged installation can replace Java and Coursier with one command."""
    monkeypatch.setenv("SPITZEISEN_SMITHY_JSONSCHEMA", "custom-converter --flag 'two words'")

    assert converter._bridge_command() == ["custom-converter", "--flag", "two words"]


def test_bridge_command_resolves_the_pinned_official_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default bridge uses the pinned Smithy version through Coursier."""
    monkeypatch.delenv("SPITZEISEN_SMITHY_JSONSCHEMA", raising=False)

    def which(command: str) -> str | None:
        return {"java": "/bin/java", "coursier": "/bin/coursier"}.get(command)

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command == [
            "/bin/coursier",
            "fetch",
            "--classpath",
            converter.SMITHY_JSONSCHEMA_COORDINATE,
        ]
        return subprocess.CompletedProcess(command, 0, stdout="/cache/smithy-jsonschema.jar\n", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema.shutil.which", which)
    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema.subprocess.run", run)

    assert converter._bridge_command() == [
        "/bin/java",
        "--class-path",
        "/cache/smithy-jsonschema.jar",
        str(converter.JAVA_BRIDGE),
    ]


def test_bridge_command_reports_missing_runtime_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing Java/Coursier dependencies produce an actionable codegen error."""
    monkeypatch.delenv("SPITZEISEN_SMITHY_JSONSCHEMA", raising=False)

    def missing(_command: str) -> None:
        return None

    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema.shutil.which", missing)

    with pytest.raises(CodegenError, match="Install Java and Coursier"):
        converter._bridge_command()


def test_converter_receives_the_service_and_unique_response_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only response closures are requested from the official converter."""
    captured: list[str] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        model_path = Path(command[1])
        assert json.loads(model_path.read_text()) == {"smithy": "2.0", "shapes": {}}
        return subprocess.CompletedProcess(command, 0, stdout='{"$defs":{"Weather":{"type":"object"}}}', stderr="")

    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema._bridge_command", lambda: ["smithy-jsonschema"])
    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema.subprocess.run", run)

    schema = smithy_to_json_schema(
        {"smithy": "2.0", "shapes": {}},
        service_id="weather#WeatherService",
        response_shapes=("weather#Weather", "weather#Weather"),
        working_directory=tmp_path,
    )

    assert schema == {"$defs": {"Weather": {"type": "object"}}}
    assert captured[0] == "smithy-jsonschema"
    assert captured[2:] == ["weather#WeatherService", "weather#Weather"]


def test_converter_requires_a_generated_response_shape() -> None:
    """A scalar response cannot silently produce an absent Pydantic model."""
    with pytest.raises(CodegenError, match="No generated response shapes"):
        smithy_to_json_schema(
            {"smithy": "2.0", "shapes": {}},
            service_id="weather#WeatherService",
            response_shapes=(),
        )


@pytest.mark.parametrize(
    ("completed", "match"),
    [
        (subprocess.CompletedProcess(["bridge"], 1, stdout="", stderr="unknown shape"), "unknown shape"),
        (subprocess.CompletedProcess(["bridge"], 0, stdout="not json", stderr=""), "Expecting value"),
        (subprocess.CompletedProcess(["bridge"], 0, stdout="[]", stderr=""), "top level"),
    ],
)
def test_converter_reports_bridge_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    match: str,
) -> None:
    """Java and conversion failures remain concise codegen errors."""
    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema._bridge_command", lambda: ["bridge"])

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr("spitzeisen.codegen.smithy_jsonschema.subprocess.run", run)

    with pytest.raises(CodegenError, match=match):
        smithy_to_json_schema(
            {"smithy": "2.0", "shapes": {}},
            service_id="weather#WeatherService",
            response_shapes=("weather#Weather",),
            working_directory=tmp_path,
        )
