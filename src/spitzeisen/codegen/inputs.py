"""Load OpenAPI, assemble Smithy overlays, and compile the external codegen inputs."""

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpcore
import httpx
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from spitzeisen.codegen.assembly import assemble_smithy
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.openapi import import_openapi
from spitzeisen.codegen.parser import ParsedSmithy
from spitzeisen.codegen.parser.errors import ParseError
from spitzeisen.codegen.policy import ClientPlan, TargetSettings, compile_model
from spitzeisen.codegen.traits import model_customizations


@dataclass(frozen=True, slots=True)
class BuildInputs:
    """Original schemas, imported Smithy model, and the compiled SDK plan."""

    spec: dict[str, Any]
    smithy: dict[str, Any]
    client: ClientPlan
    warnings: tuple[ParseError, ...]


def load_mapping(data: bytes, content_type: str | None) -> dict[str, Any]:
    """Load a JSON or YAML mapping, raising a structured error when it is invalid."""
    document: object
    if content_type == "application/json":
        try:
            document = json.loads(data.decode())
        except ValueError as err:
            raise CodegenError(header="Invalid JSON from provided source", detail=str(err)) from err
    else:
        try:
            yaml = YAML(typ="safe")
            document = yaml.load(data)  # pyright: ignore[reportUnknownMemberType]
        except YAMLError as err:
            raise CodegenError(header="Invalid YAML from provided source", detail=str(err)) from err
    if not isinstance(document, dict):
        raise CodegenError(
            header="Invalid document from provided source",
            detail="The document must contain a mapping at its top level.",
        )
    return cast("dict[str, Any]", document)


def load_document(*, source: str | Path, timeout: int) -> dict[str, Any]:
    """Read an OpenAPI document from a path or URL."""
    if isinstance(source, str):
        try:
            response = httpx.get(source, timeout=timeout)
            response.raise_for_status()
            data = response.content
            if "content-type" in response.headers:
                content_type = response.headers["content-type"].split(";", maxsplit=1)[0]
            else:  # pragma: no cover
                content_type = mimetypes.guess_type(source, strict=True)[0]
        except (httpx.HTTPError, httpcore.NetworkError) as err:
            raise CodegenError(
                header="Could not get OpenAPI document from provided URL",
                detail=str(err),
            ) from err
    else:
        try:
            data = source.read_bytes()
        except OSError as err:
            raise CodegenError(
                header="Could not read OpenAPI document from provided path",
                detail=str(err),
            ) from err
        content_type = mimetypes.guess_type(source.absolute().as_uri(), strict=True)[0]
    return load_mapping(data, content_type)


def parse_smithy(model: dict[str, Any]) -> ParsedSmithy:
    """Parse a Smithy JSON AST and add input-facing context to failures."""
    try:
        return ParsedSmithy.from_dict(model)
    except (TypeError, ValueError) as err:
        raise CodegenError(header="Failed to parse imported Smithy model", detail=str(err)) from err


def load_compile_inputs(
    *,
    source: str | Path,
    overlays: tuple[Path, ...],
    target: TargetSettings,
    timeout: int,
) -> BuildInputs:
    """Load OpenAPI, assemble Smithy and its overlays, then compile one SDK plan."""
    raw_openapi = load_document(source=source, timeout=timeout)
    imported = import_openapi(raw_openapi)
    assembled = assemble_smithy(imported.model, overlays)
    smithy = parse_smithy(assembled)
    try:
        client = compile_model(target, smithy, model_customizations(assembled))
    except (TypeError, ValueError) as err:
        raise CodegenError(detail=str(err)) from err
    warnings = (*imported.warnings, *smithy.errors)
    return BuildInputs(
        spec=raw_openapi,
        smithy=assembled,
        client=client,
        warnings=warnings,
    )
