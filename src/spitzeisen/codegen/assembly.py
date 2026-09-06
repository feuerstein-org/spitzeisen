"""Assemble converted and handwritten Smithy sources into one validated JSON model."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.toolchain import ALLOY_CORE_COORDINATE, SMITHY_VERSION, alloy_model_path

SMITHY_CLI_VERSION = SMITHY_VERSION
SMITHY_CLI_COORDINATE = f"software.amazon.smithy:smithy-cli:{SMITHY_CLI_VERSION}"
TRAIT_BUNDLE = Path(__file__).with_name("smithy") / "spitzeisen-python-codegen.jar"


def _assembler_command() -> list[str]:
    """Resolve the official, pinned Smithy CLI."""
    configured = os.environ.get("SPITZEISEN_SMITHY")
    if configured:
        command = shlex.split(configured)
        if command:
            return command
    coursier = shutil.which("coursier") or shutil.which("cs")
    if coursier:
        return [coursier, "launch", SMITHY_CLI_COORDINATE, ALLOY_CORE_COORDINATE, "--"]
    executable = shutil.which("smithy")
    if executable:
        return [executable]
    raise CodegenError(
        header="Smithy assembler is unavailable",
        detail=(
            "Install the Smithy CLI on PATH, or install Java and Coursier. "
            f"Spitzeisen launches the pinned Maven artifact {SMITHY_CLI_COORDINATE}."
        ),
    )


def assemble_smithy(
    imported: dict[str, Any],
    sources: tuple[Path, ...] = (),
    *,
    working_directory: Path | None = None,
) -> dict[str, Any]:
    """Merge an imported model and native Smithy sources with Smithy's own assembler."""
    missing = [path for path in sources if not path.exists()]
    if missing:
        raise CodegenError(
            header="Unable to read Smithy source",
            detail=f"Smithy source files do not exist: {[str(path) for path in missing]}",
        )
    if not TRAIT_BUNDLE.is_file():  # pragma: no cover - packaging integrity guard
        raise CodegenError(
            header="Spitzeisen Smithy traits are unavailable",
            detail=f"the installed package is missing {TRAIT_BUNDLE}",
        )
    if working_directory is not None:
        working_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spitzeisen-assembly-", dir=working_directory) as temporary:
        source = Path(temporary) / "imported.smithy.json"
        source.write_text(json.dumps(imported, indent=2))
        command = [
            *_assembler_command(),
            "ast",
            "--no-config",
            "--quiet",
            "--flatten",
            str(source),
            str(TRAIT_BUNDLE),
            str(alloy_model_path()),
            *(str(path.resolve()) for path in sources),
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip() or f"assembler exited with status {process.returncode}"
        raise CodegenError(header="Smithy model assembly failed", detail=detail)
    try:
        model = json.loads(process.stdout)
    except ValueError as err:
        raise CodegenError(header="Smithy model assembly produced invalid JSON", detail=str(err)) from err
    if not isinstance(model, dict):
        raise CodegenError(
            header="Smithy model assembly produced invalid JSON",
            detail="The assembled Smithy JSON AST must contain an object at its top level.",
        )
    return cast("dict[str, Any]", model)
