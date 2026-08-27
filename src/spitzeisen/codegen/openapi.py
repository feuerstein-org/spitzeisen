"""Best-effort OpenAPI import into the Smithy JSON AST consumed by codegen."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.parser.errors import ParseError

SMITHY_TRANSLATE_VERSION = "0.7.8"
SMITHY_TRANSLATE_COORDINATE = f"com.disneystreaming.smithy:smithytranslate-cli_2.13:{SMITHY_TRANSLATE_VERSION}"
_SMITHY_TRANSLATE_MAIN = "smithytranslate.cli.Main"
_IGNORED_DIAGNOSTICS = frozenset(
    {"Unable to automatically account for exclusiveMin/Max on decimal type Double"},
)

# smithy-translate 0.7.8 advertises OpenAPI 3.x support, but its 3.1 path emits placeholder
# structures even for ordinary scalar schemas. A 3.0 compatibility projection is safe only
# when these genuinely 3.1-only JSON Schema features are absent.
_UNSUPPORTED_31_KEYWORDS = frozenset(
    {
        "$anchor",
        "$defs",
        "$dynamicAnchor",
        "$dynamicRef",
        "$vocabulary",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "dependentRequired",
        "dependentSchemas",
        "maxContains",
        "minContains",
        "prefixItems",
        "propertyNames",
        "unevaluatedItems",
        "unevaluatedProperties",
    },
)


@dataclass(frozen=True, slots=True)
class ImportedSmithy:
    """A converted Smithy JSON AST plus non-fatal converter diagnostics."""

    model: dict[str, Any]
    warnings: tuple[ParseError, ...]


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


def _project_schema_31(value: object, path: str) -> object:  # noqa: C901
    """Project the small compatible subset of OpenAPI 3.1 schemas into 3.0."""
    if isinstance(value, list):
        sequence = cast("list[object]", value)
        return [_project_schema_31(item, f"{path}/{index}") for index, item in enumerate(sequence)]
    if not isinstance(value, dict):
        return value
    mapping = cast("dict[str, object]", value)
    unsupported = sorted(_UNSUPPORTED_31_KEYWORDS & mapping.keys())
    if unsupported:
        raise CodegenError(
            header="OpenAPI 3.1 document cannot be projected for smithy-translate",
            detail=f"{path or '/'} uses unsupported JSON Schema keywords {unsupported}",
        )
    if any(key in mapping for key in ("if", "then", "else")):
        raise CodegenError(
            header="OpenAPI 3.1 document cannot be projected for smithy-translate",
            detail=f"{path or '/'} uses conditional JSON Schema keywords",
        )
    if "$ref" in mapping and len(mapping) > 1:
        siblings = sorted(key for key in mapping if key != "$ref")
        raise CodegenError(
            header="OpenAPI 3.1 document cannot be projected for smithy-translate",
            detail=f"{path or '/'} uses $ref siblings whose 3.0 semantics would be lossy: {siblings}",
        )

    projected: dict[str, object] = {
        key: _project_schema_31(item, f"{path}/{key}") for key, item in mapping.items() if key != "const"
    }
    raw_type = mapping.get("type")
    if isinstance(raw_type, list):
        type_values = cast("list[object]", raw_type)
        non_null = [item for item in type_values if item != "null"]
        if len(non_null) != 1 or len(non_null) == len(type_values):
            raise CodegenError(
                header="OpenAPI 3.1 document cannot be projected for smithy-translate",
                detail=f"{path or '/'} has a type union that OpenAPI 3.0 cannot express: {raw_type!r}",
            )
        projected["type"] = non_null[0]
        projected["nullable"] = True
    if "const" in mapping:
        enum = mapping.get("enum")
        if isinstance(enum, list) and mapping["const"] not in cast("list[object]", enum):
            raise CodegenError(
                header="OpenAPI 3.1 document cannot be projected for smithy-translate",
                detail=f"{path or '/'} declares contradictory const and enum values",
            )
        projected["enum"] = [mapping["const"]]
    for keyword, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        exclusive = mapping.get(keyword)
        if isinstance(exclusive, (int, float)) and not isinstance(exclusive, bool):
            projected[bound] = exclusive
            projected[keyword] = True
    return projected


def project_openapi_for_converter(spec: dict[str, Any]) -> dict[str, Any]:
    """Return input accepted by the pinned converter without changing the model backend input."""
    version = spec.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise CodegenError(
            header="Unsupported OpenAPI document",
            detail="Spitzeisen's OpenAPI importer currently accepts OpenAPI 3.0 and 3.1 documents.",
        )
    if not version.startswith("3.1"):
        return deepcopy(spec)
    projected = cast("dict[str, Any]", _project_schema_31(spec, ""))
    projected["openapi"] = "3.0.3"
    return projected


def _diagnostics(output: str) -> tuple[ParseError, ...]:
    """Turn converter messages into concise codegen warnings."""
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.startswith("Writing ") and line.strip() not in _IGNORED_DIAGNOSTICS
    ]
    return tuple(ParseError(header="smithy-translate warning", detail=line) for line in lines)


def import_openapi(spec: dict[str, Any], *, working_directory: Path | None = None) -> ImportedSmithy:
    """Convert one OpenAPI document into Smithy's JSON AST using pinned community tooling."""
    projected = project_openapi_for_converter(spec)
    if working_directory is not None:
        working_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spitzeisen-smithy-", dir=working_directory) as temporary:
        root = Path(temporary)
        source = root / "openapi.json"
        output = root / "output"
        output.mkdir()
        source.write_text(json.dumps(projected, indent=2))
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
