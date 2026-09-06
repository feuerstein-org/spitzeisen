"""Resolution of the pinned, external Alloy model dependency."""

import os
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

from spitzeisen.codegen import toolchain
from spitzeisen.codegen.exceptions import CodegenError


@pytest.fixture(autouse=True)
def clear_resolution_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Keep mocked resolution local to each test."""

    def locate(_name: str) -> str:
        return "/tools/coursier"

    monkeypatch.setattr("spitzeisen.codegen.toolchain.shutil.which", locate)
    toolchain.alloy_model_path.cache_clear()
    yield
    toolchain.alloy_model_path.cache_clear()


def test_alloy_resolution_selects_the_pinned_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The classpath can include unrelated dependencies before Alloy's model JAR."""
    jar = tmp_path / f"alloy-core-{toolchain.ALLOY_VERSION}.jar"
    jar.touch()

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command == ["/tools/coursier", "fetch", "--classpath", toolchain.ALLOY_CORE_COORDINATE]
        return subprocess.CompletedProcess(command, 0, stdout=f"/other.jar{os.pathsep}{jar}\n", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.toolchain.subprocess.run", run)
    assert toolchain.alloy_model_path() == jar


@pytest.mark.parametrize("status", [0, 1])
def test_alloy_resolution_reports_missing_or_failed_artifacts(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed fetch or missing JAR cannot be passed to model assembly."""

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], status, stdout="/other.jar", stderr="")

    monkeypatch.setattr("spitzeisen.codegen.toolchain.subprocess.run", run)
    with pytest.raises(CodegenError, match="Coursier did not return"):
        toolchain.alloy_model_path()


def test_alloy_resolution_requires_coursier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the missing build tool before launching a process."""

    def locate(_name: str) -> None:
        return None

    monkeypatch.setattr("spitzeisen.codegen.toolchain.shutil.which", locate)
    with pytest.raises(CodegenError, match="Install Java and Coursier"):
        toolchain.alloy_model_path()
