"""The official Smithy assembler boundary."""

import json
import subprocess
from pathlib import Path

import pytest

from spitzeisen.codegen.assembly import SMITHY_CLI_COORDINATE, assemble_smithy
from spitzeisen.codegen.exceptions import CodegenError


@pytest.fixture(autouse=True)
def alloy_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process-boundary tests do not download dependencies."""
    monkeypatch.setattr("spitzeisen.codegen.assembly.alloy_model_path", lambda: Path("/tools/alloy.jar"))


def test_assembly_invokes_pinned_cli_with_all_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The converted model, trait definitions, and every overlay form one assembly."""
    first = tmp_path / "first.smithy"
    second = tmp_path / "second.smithy"
    first.write_text('$version: "2"\nnamespace first\n')
    second.write_text('$version: "2"\nnamespace second\n')
    captured: list[str] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        source = Path(command[command.index("--flatten") + 1])
        assert json.loads(source.read_text()) == {"smithy": "2.0", "shapes": {}}
        return subprocess.CompletedProcess(command, 0, stdout='{"smithy":"2.0","shapes":{}}', stderr="")

    monkeypatch.setattr(
        "spitzeisen.codegen.assembly._assembler_command",
        lambda: ["coursier", "launch", SMITHY_CLI_COORDINATE, "--"],
    )
    monkeypatch.setattr("spitzeisen.codegen.assembly.subprocess.run", run)

    model = assemble_smithy({"smithy": "2.0", "shapes": {}}, (first, second), working_directory=tmp_path)

    assert model == {"smithy": "2.0", "shapes": {}}
    assert captured[:5] == ["coursier", "launch", SMITHY_CLI_COORDINATE, "--", "ast"]
    assert captured[-2:] == [str(first.resolve()), str(second.resolve())]
    assert "/tools/alloy.jar" in captured


def test_assembly_rejects_missing_source(tmp_path: Path) -> None:
    """A misspelled Smithy path fails before launching Java."""
    with pytest.raises(CodegenError, match="Smithy source files do not exist"):
        assemble_smithy({"smithy": "2.0", "shapes": {}}, (tmp_path / "missing.smithy",))


@pytest.mark.parametrize(
    ("completed", "match"),
    [
        (subprocess.CompletedProcess(["smithy"], 1, stdout="", stderr="broken trait"), "broken trait"),
        (subprocess.CompletedProcess(["smithy"], 0, stdout="not json", stderr=""), "Expecting value"),
        (subprocess.CompletedProcess(["smithy"], 0, stdout="[]", stderr=""), "top level"),
    ],
)
def test_assembly_reports_tool_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    match: str,
) -> None:
    """Assembler failures stay concise at the codegen boundary."""
    monkeypatch.setattr("spitzeisen.codegen.assembly._assembler_command", lambda: ["smithy"])

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr("spitzeisen.codegen.assembly.subprocess.run", run)

    with pytest.raises(CodegenError, match=match):
        assemble_smithy({"smithy": "2.0", "shapes": {}}, working_directory=tmp_path)
