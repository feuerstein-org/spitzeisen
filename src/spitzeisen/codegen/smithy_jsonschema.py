"""Convert assembled Smithy response shapes with Smithy's official JSON Schema library."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from spitzeisen.codegen.assembly import SMITHY_CLI_VERSION
from spitzeisen.codegen.exceptions import CodegenError

SMITHY_JSONSCHEMA_COORDINATE = f"software.amazon.smithy:smithy-jsonschema:{SMITHY_CLI_VERSION}"
JAVA_BRIDGE = Path(__file__).with_name("smithy") / "SmithyJsonSchema.java"


def _bridge_command() -> list[str]:
    """Resolve the tiny launcher and the pinned official converter classpath."""
    configured = os.environ.get("SPITZEISEN_SMITHY_JSONSCHEMA")
    if configured:
        command = shlex.split(configured)
        if command:
            return command
    java = shutil.which("java")
    coursier = shutil.which("coursier") or shutil.which("cs")
    if java is None or coursier is None:
        raise CodegenError(
            header="Smithy JSON Schema converter is unavailable",
            detail="Install Java and Coursier, or configure SPITZEISEN_SMITHY_JSONSCHEMA.",
        )
    fetched = subprocess.run(  # noqa: S603
        [coursier, "fetch", "--classpath", SMITHY_JSONSCHEMA_COORDINATE],
        capture_output=True,
        text=True,
        check=False,
    )
    classpath = fetched.stdout.strip()
    if fetched.returncode != 0 or not classpath:
        detail = (fetched.stderr or fetched.stdout).strip() or f"Coursier exited with status {fetched.returncode}"
        raise CodegenError(header="Unable to resolve Smithy's JSON Schema converter", detail=detail)
    return [java, "--class-path", classpath, str(JAVA_BRIDGE)]


def smithy_to_json_schema(
    model: dict[str, Any],
    *,
    service_id: str,
    response_shapes: tuple[str, ...],
    working_directory: Path | None = None,
) -> dict[str, Any]:
    """Convert response closures from an assembled model into JSON Schema 2020-12."""
    roots = tuple(dict.fromkeys(response_shapes))
    if not roots:
        raise CodegenError(
            header="Smithy model conversion failed",
            detail="No generated response shapes were found for the selected service.",
        )
    if not JAVA_BRIDGE.is_file():  # pragma: no cover - packaging integrity guard
        raise CodegenError(
            header="Smithy JSON Schema bridge is unavailable",
            detail=f"the installed package is missing {JAVA_BRIDGE}",
        )
    if working_directory is not None:
        working_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spitzeisen-jsonschema-", dir=working_directory) as temporary:
        source = Path(temporary) / "model.smithy.json"
        source.write_text(json.dumps(model, indent=2))
        process = subprocess.run(  # noqa: S603
            [*_bridge_command(), str(source), service_id, *roots],
            capture_output=True,
            text=True,
            check=False,
        )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip() or f"converter exited with status {process.returncode}"
        raise CodegenError(header="Smithy to JSON Schema conversion failed", detail=detail)
    try:
        schema = json.loads(process.stdout)
    except ValueError as err:
        raise CodegenError(header="Smithy to JSON Schema conversion produced invalid JSON", detail=str(err)) from err
    if not isinstance(schema, dict):
        raise CodegenError(
            header="Smithy to JSON Schema conversion produced invalid JSON",
            detail="The JSON Schema document must contain an object at its top level.",
        )
    return cast("dict[str, Any]", schema)
