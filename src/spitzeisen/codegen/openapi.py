"""Best-effort OpenAPI import into the Smithy JSON AST consumed by codegen."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from spitzeisen.codegen.diagnostics import ModelImportWarning
from spitzeisen.codegen.exceptions import CodegenError

SMITHY_TRANSLATE_VERSION = "0.7.8"
SMITHY_TRANSLATE_COORDINATE = f"com.disneystreaming.smithy:smithytranslate-cli_2.13:{SMITHY_TRANSLATE_VERSION}"
_SMITHY_TRANSLATE_MAIN = "smithytranslate.cli.Main"
_IGNORED_DIAGNOSTICS = frozenset(
    {"Unable to automatically account for exclusiveMin/Max on decimal type Double"},
)


@dataclass(frozen=True, slots=True)
class ImportedSmithy:
    """A converted Smithy JSON AST plus non-fatal converter diagnostics."""

    model: dict[str, Any]
    warnings: tuple[ModelImportWarning, ...]


def _schemas(value: object, path: str = "", *, schema: bool = False) -> Iterator[tuple[str, dict[str, Any]]]:
    """Locate schema objects, keeping property names and example data out of keyword checks."""
    if isinstance(value, list):
        for index, item in enumerate(cast("list[object]", value)):
            yield from _schemas(item, f"{path}/{index}", schema=schema)
    elif isinstance(value, dict):
        mapping = cast("dict[str, Any]", value)
        if schema:
            yield path, mapping
            for key in ("properties", "patternProperties", "$defs", "definitions"):
                for name, item in mapping.get(key, {}).items():
                    yield from _schemas(item, f"{path}/{key}/{name}", schema=True)
            for key in ("items", "additionalProperties", "not", "allOf", "oneOf", "anyOf"):
                yield from _schemas(mapping.get(key), f"{path}/{key}", schema=True)
        else:
            for key, item in mapping.items():
                if key in {"example", "examples"} or key.startswith("x-"):
                    continue
                yield from _schemas(item, f"{path}/{key}", schema=key == "schema" or path == "/components/schemas")


def validate_openapi_model_presence(spec: dict[str, Any]) -> None:
    """Reject response presence information lost by routing the model backend through Smithy."""
    for path, schema in _schemas(spec):
        response = path.startswith("/components/schemas/") or "/responses/" in path
        if response and ("default" in schema or schema.get("nullable") is True):
            raise CodegenError(
                header="OpenAPI presence semantics cannot be preserved by smithy-translate",
                detail=(
                    f"{path} uses default or nullable, which smithy-translate {SMITHY_TRANSLATE_VERSION} drops. "
                    "Run `spitzeisen-gen import-openapi` to inspect the imported model, encode the intended "
                    "presence with Smithy @default/@clientOptional traits, and generate with --smithy."
                ),
            )


def _converter_command() -> list[str]:
    """Resolve a pinned smithy-translate launcher."""
    configured = os.environ.get("SPITZEISEN_SMITHYTRANSLATE")
    if configured:
        command = shlex.split(configured)
        if command:
            return command
    coursier = shutil.which("coursier") or shutil.which("cs")
    if coursier:
        return [
            coursier,
            "launch",
            SMITHY_TRANSLATE_COORDINATE,
            "--main-class",
            _SMITHY_TRANSLATE_MAIN,
            "--",
        ]
    executable = shutil.which("smithytranslate")
    if executable:
        return [executable]
    raise CodegenError(
        header="OpenAPI to Smithy converter is unavailable",
        detail=(
            "Install smithy-translate on PATH, or install Java and Coursier. "
            f"Spitzeisen launches the pinned Maven artifact {SMITHY_TRANSLATE_COORDINATE}."
        ),
    )


def validate_openapi_version(spec: dict[str, Any]) -> None:
    """Accept only OpenAPI 3.0.x until the pinned importer supports 3.1 reliably."""
    version = spec.get("openapi")
    if not isinstance(version, str) or re.fullmatch(r"3\.0\.[0-9]+", version) is None:
        raise CodegenError(
            header="Unsupported OpenAPI version",
            detail=(
                f"Expected OpenAPI 3.0.x; found {version!r}. "
                f"smithy-translate {SMITHY_TRANSLATE_VERSION} does not reliably import OpenAPI 3.1, "
                "so Spitzeisen does not accept or downgrade it. "
                "Export a valid OpenAPI 3.0.x document or use native Smithy with --smithy."
            ),
        )


def _diagnostics(output: str) -> tuple[ModelImportWarning, ...]:
    """Turn converter messages into concise codegen warnings."""
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.startswith("Writing ") and line.strip() not in _IGNORED_DIAGNOSTICS
    ]
    return tuple(ModelImportWarning(header="smithy-translate warning", detail=line) for line in lines)


def import_openapi(spec: dict[str, Any], *, working_directory: Path | None = None) -> ImportedSmithy:
    """Convert one OpenAPI document into Smithy's JSON AST using pinned community tooling."""
    validate_openapi_version(spec)
    if working_directory is not None:
        working_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spitzeisen-smithy-", dir=working_directory) as temporary:
        root = Path(temporary)
        source = root / "openapi.json"
        output = root / "output"
        output.mkdir()
        source.write_text(json.dumps(spec, indent=2))
        command = [
            *_converter_command(),
            "openapi-to-smithy",
            "--input",
            str(source),
            "--validate-input",
            "--json-output",
            str(output),
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
        combined_output = "\n".join(part for part in (process.stdout, process.stderr) if part)
        result = output / "result.json"
        if process.returncode != 0 or not result.exists():
            detail = combined_output.strip() or f"converter exited with status {process.returncode}"
            raise CodegenError(header="OpenAPI to Smithy conversion failed", detail=detail)
        try:
            model = json.loads(result.read_text())
        except (OSError, ValueError) as err:
            raise CodegenError(header="OpenAPI to Smithy conversion produced invalid JSON", detail=str(err)) from err
    if not isinstance(model, dict):
        raise CodegenError(
            header="OpenAPI to Smithy conversion produced invalid JSON",
            detail="The Smithy JSON AST must contain an object at its top level.",
        )
    return ImportedSmithy(model=cast("dict[str, Any]", model), warnings=_diagnostics(combined_output))
