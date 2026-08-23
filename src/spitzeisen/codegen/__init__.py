"""Build-time code generation, installed with the ``codegen`` extra."""

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpcore
import httpx
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from spitzeisen.codegen.parser.errors import GeneratorError


def load_yaml_or_json(data: bytes, content_type: str | None) -> dict[str, Any] | GeneratorError:
    """Load source bytes using openapi-python-client's content-type rule."""
    if content_type == "application/json":
        try:
            return json.loads(data.decode())
        except ValueError as err:
            return GeneratorError(header=f"Invalid JSON from provided source: {err}")
    try:
        yaml = YAML(typ="safe")
        return yaml.load(data)  # pyright: ignore[reportUnknownMemberType]
    except YAMLError as err:
        return GeneratorError(header=f"Invalid YAML from provided source: {err}")


def get_document(*, source: str | Path, timeout: int) -> dict[str, Any] | GeneratorError:
    """Read an OpenAPI document from the explicit path or URL."""
    if isinstance(source, str):
        try:
            response = httpx.get(source, timeout=timeout)
            data = response.content
            if "content-type" in response.headers:
                content_type = response.headers["content-type"].split(";")[0]
            else:  # pragma: no cover
                content_type = mimetypes.guess_type(source, strict=True)[0]
        except (httpx.HTTPError, httpcore.NetworkError):
            return GeneratorError(header="Could not get OpenAPI document from provided URL")
    else:
        data = source.read_bytes()
        content_type = mimetypes.guess_type(source.absolute().as_uri(), strict=True)[0]
    return load_yaml_or_json(data, content_type)
