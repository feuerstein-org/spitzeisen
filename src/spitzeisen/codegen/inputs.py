"""Load native Smithy or OpenAPI and compile the external codegen inputs."""

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
from spitzeisen.codegen.diagnostics import ModelImportWarning
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.java_frontend import FrontendResult, compile_smithy_frontend
from spitzeisen.codegen.openapi import import_openapi, validate_openapi_model_presence, validate_openapi_version


@dataclass(frozen=True, slots=True)
class BuildInputs:
    """Pydantic model input and Java-emitted SDK artifacts."""

    model_schema: dict[str, Any]
    frontend: FrontendResult
    warnings: tuple[ModelImportWarning, ...]


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


def load_compile_inputs(
    *,
    package: str,
    client_name: str,
    vendor: str | None = None,
    python_settings: dict[str, Any] | None = None,
    source: str | Path | None = None,
    smithy_sources: tuple[Path, ...] = (),
    overlays: tuple[Path, ...],
    service: str | None = None,
    timeout: int,
) -> BuildInputs:
    """Load exactly one model source, assemble Smithy, and generate Python source."""
    if (source is None) == (not smithy_sources):
        raise CodegenError(detail="provide either an OpenAPI source or at least one native Smithy source")
    if source is not None:
        raw_openapi = load_document(source=source, timeout=timeout)
        validate_openapi_version(raw_openapi)
        validate_openapi_model_presence(raw_openapi)
        imported = import_openapi(raw_openapi)
        assembled = assemble_smithy(imported.model, overlays)
        import_warnings = imported.warnings
    else:
        assembled = assemble_smithy({"smithy": "2.0", "shapes": {}}, (*smithy_sources, *overlays))
        import_warnings = ()
    working_directory = smithy_sources[0].parent if smithy_sources else overlays[0].parent if overlays else None
    frontend = compile_smithy_frontend(
        assembled,
        service=service,
        package=package,
        client_name=client_name,
        vendor=vendor,
        python_settings=python_settings,
        working_directory=working_directory,
    )
    warnings = (
        *import_warnings,
        *(
            ModelImportWarning(header="Smithy client compatibility opt-in", detail=warning)
            for warning in frontend.manifest.warnings
        ),
    )
    return BuildInputs(
        model_schema=frontend.model_schema,
        frontend=frontend,
        warnings=warnings,
    )
