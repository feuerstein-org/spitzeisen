"""Load, validate, and compile the external inputs used for code generation."""

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpcore
import httpx
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.manifest import Manifest
from spitzeisen.codegen.parser import ParsedOpenAPI
from spitzeisen.codegen.parser.errors import ParseError
from spitzeisen.codegen.policy import ClientPlan, compile_manifest


@dataclass(frozen=True, slots=True)
class BuildInputs:
    """Hydrated source inputs and their compiled SDK plan."""

    manifest: Manifest
    spec: dict[str, Any]
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


def load_manifest(path: Path) -> Manifest:
    """Load and validate a Spitzeisen manifest."""
    try:
        data = path.read_bytes()
    except OSError as err:
        raise CodegenError(header="Unable to read config", detail=str(err)) from err
    try:
        loaded = load_mapping(data, "application/json" if path.suffix == ".json" else None)
    except CodegenError as err:
        raise CodegenError(header="Unable to parse config", detail=err.detail or str(err)) from err
    try:
        return Manifest.model_validate(loaded)
    except ValidationError as err:
        raise CodegenError(header="Unable to parse config", detail=str(err)) from err


def parse_openapi(spec: dict[str, Any]) -> ParsedOpenAPI:
    """Parse OpenAPI data and add input-facing context to validation failures."""
    try:
        return ParsedOpenAPI.from_dict(spec)
    except ValidationError as err:
        detail = str(err)
        if "swagger" in spec:
            detail = "You may be trying to use a Swagger document; this is not supported by this project.\n\n" + detail
        raise CodegenError(header="Failed to parse OpenAPI document", detail=detail) from err


def load_compile_inputs(*, source: str | Path, manifest_path: Path, timeout: int) -> BuildInputs:
    """Load both external inputs and compile them into one SDK plan."""
    manifest = load_manifest(manifest_path)
    raw_openapi = load_document(source=source, timeout=timeout)
    openapi = parse_openapi(raw_openapi)
    try:
        client = compile_manifest(manifest, openapi)
    except ValueError as err:
        raise CodegenError(detail=str(err)) from err
    warnings: list[ParseError] = list(openapi.errors)
    for collection in openapi.operation_collections_by_tag.values():
        warnings.extend(collection.parse_errors)
    return BuildInputs(manifest=manifest, spec=raw_openapi, client=client, warnings=tuple(warnings))
