"""Safe loading, validation, and reference-aware access for OpenAPI documents."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import yaml
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from jsonschema_path import SchemaAccessor, SchemaPath
from openapi_spec_validator import OpenAPIV30SpecValidator, OpenAPIV31SpecValidator, OpenAPIV32SpecValidator

from spitzeisen.codegen.diagnostics import CodegenError, Diagnostic, SourceLocation, error
from spitzeisen.codegen.ir import JSONValue, OpenAPIDialect, SchemaId

type JSONObject = dict[str, JSONValue]
type ReferenceHandler = Callable[[str], Mapping[str, Any]]


def _pointer(parts: Sequence[str | int]) -> str:
    """Encode path components as an RFC 6901 JSON Pointer."""
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in parts)


def _load_mapping(path: Path) -> JSONObject:
    """Load a JSON or YAML object without accepting a scalar document root."""
    try:
        text = path.read_text()
    except OSError as exc:
        location = SourceLocation(path.resolve().as_uri())
        code = "openapi.read"
        raise error(code, str(exc), location) from exc

    try:
        value = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        location = SourceLocation(path.resolve().as_uri())
        code = "openapi.syntax"
        raise error(code, str(exc), location) from exc
    if not isinstance(value, dict):
        location = SourceLocation(path.resolve().as_uri())
        code = "openapi.root"
        message = "the document root must be an object"
        raise error(code, message, location)
    raw_mapping = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in raw_mapping):
        location = SourceLocation(path.resolve().as_uri())
        code = "openapi.root"
        message = "every document object key must be a string"
        raise error(code, message, location)
    return cast("JSONObject", value)


class _RestrictedFileReferences:
    """Load referenced files only from the configured OpenAPI source tree."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def __call__(self, uri: str) -> Mapping[str, Any]:
        """Resolve one file URI after preventing traversal outside the source tree."""
        parsed = urlsplit(uri)
        if parsed.scheme not in {"", "file"}:
            msg = f"unsupported reference scheme {parsed.scheme!r}"
            raise ValueError(msg)
        path = Path(unquote(parsed.path)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            msg = f"reference {uri!r} escapes the allowed OpenAPI directory {self.root}"
            raise ValueError(msg) from exc
        return _load_mapping(path)


def _deny_remote_reference(uri: str) -> Mapping[str, Any]:
    """Reject implicit network access while compiling an untrusted specification."""
    msg = f"remote OpenAPI reference {uri!r} is disabled; vendor referenced documents locally instead"
    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class OpenAPINode:
    """A source node that can be read either literally or through `$ref` resolution."""

    document: OpenAPIDocument
    parts: tuple[str | int, ...]

    @property
    def location(self) -> SourceLocation:
        """Return the node's original source location."""
        return SourceLocation(self.document.base_uri, _pointer(self.parts))

    @property
    def raw(self) -> JSONValue:
        """Read the literal value without following a reference."""
        value: JSONValue = self.document.data
        for part in self.parts:
            if isinstance(value, dict) and isinstance(part, str):
                value = value[part]
            elif isinstance(value, list) and isinstance(part, int):
                value = cast("list[JSONValue]", value)[part]
            else:
                msg = f"{self.location} does not identify a value"
                raise KeyError(msg)
        return value

    @property
    def resolved(self) -> JSONValue:
        """Read the value after lazily following references."""
        path = SchemaPath(self.document.accessor, separator="#")
        for part in self.parts:
            path = path / part
        try:
            return cast("JSONValue", path.read_value())
        except Exception as exc:
            code = "openapi.reference"
            raise error(code, str(exc), self.location) from exc

    @property
    def reference(self) -> str | None:
        """Return a literal `$ref` carried by this node, if any."""
        raw = self.raw
        if not isinstance(raw, dict):
            return None
        reference = raw.get("$ref")
        return reference if isinstance(reference, str) else None

    @property
    def schema_id(self) -> SchemaId:
        """Return the canonical identity of this inline or referenced schema."""
        reference = self.reference
        if reference is None:
            return SchemaId(uri=self.document.base_uri, pointer=_pointer(self.parts))
        absolute = (
            f"{self.document.base_uri}{reference}"
            if reference.startswith("#")
            else urljoin(self.document.base_uri, reference)
        )
        uri, fragment = urldefrag(absolute)
        pointer = unquote(fragment)
        return SchemaId(uri=uri, pointer=pointer)

    def child(self, *parts: str | int) -> OpenAPINode:
        """Return a child without reading it yet."""
        return OpenAPINode(self.document, (*self.parts, *parts))


@dataclass(frozen=True, slots=True)
class OpenAPIDocument:
    """A validated OpenAPI document and its safely configured reference accessor."""

    data: JSONObject
    base_uri: str
    dialect: OpenAPIDialect
    version: str
    accessor: SchemaAccessor

    @classmethod
    def from_path(cls, path: Path, *, reference_root: Path | None = None) -> OpenAPIDocument:
        """Load and validate an OpenAPI document from JSON or YAML."""
        resolved = path.resolve()
        return cls.from_mapping(
            _load_mapping(resolved),
            base_uri=resolved.as_uri(),
            reference_root=reference_root or resolved.parent,
        )

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        base_uri: str = "urn:spitzeisen:openapi",
        reference_root: Path | None = None,
    ) -> OpenAPIDocument:
        """Validate an already-loaded document and configure safe reference handling."""
        copied = cast("JSONObject", deepcopy(dict(data)))
        version = copied.get("openapi")
        if not isinstance(version, str):
            code = "openapi.version"
            message = "a supported OpenAPI 3.x document must declare a string `openapi` version"
            raise error(
                code,
                message,
                SourceLocation(base_uri, "/openapi"),
            )
        dialect = _dialect(version, base_uri)

        file_handler: ReferenceHandler = (
            _RestrictedFileReferences(reference_root) if reference_root is not None else _deny_remote_reference
        )
        handlers: dict[str, ReferenceHandler] = {
            "": file_handler,
            "file": file_handler,
            "http": _deny_remote_reference,
            "https": _deny_remote_reference,
        }
        accessor = SchemaAccessor.from_schema(
            copied,
            base_uri=base_uri,
            handlers=handlers,
            resolved_cache_maxsize=256,
        )
        document = cls(data=copied, base_uri=base_uri, dialect=dialect, version=version, accessor=accessor)
        document.validate()
        return document

    @property
    def root(self) -> OpenAPINode:
        """Return the document root."""
        return OpenAPINode(self, ())

    def node(self, *parts: str | int) -> OpenAPINode:
        """Return a lazily-read source node."""
        return OpenAPINode(self, tuple(parts))

    def validate(self) -> None:
        """Validate structure and OpenAPI-specific semantic constraints."""
        validator_class = {
            OpenAPIDialect.V3_0: OpenAPIV30SpecValidator,
            OpenAPIDialect.V3_1: OpenAPIV31SpecValidator,
            OpenAPIDialect.V3_2: OpenAPIV32SpecValidator,
        }[self.dialect]
        root = SchemaPath(self.accessor, separator="#")
        try:
            validation_errors = list(validator_class(root).iter_errors())
        except Exception as exc:
            code = "openapi.validation"
            message = _exception_chain_message(exc)
            reference = _exception_reference(exc)
            pointer = _find_reference_pointer(self.data, reference) if reference is not None else ""
            raise error(code, message, SourceLocation(self.base_uri, pointer)) from exc
        if not validation_errors:
            return
        diagnostics = tuple(_validation_diagnostic(self.base_uri, item) for item in validation_errors[:20])
        raise CodegenError(*diagnostics)


def _dialect(version: str, base_uri: str) -> OpenAPIDialect:
    """Select the normalizer for a supported OpenAPI version."""
    for prefix, dialect in (
        ("3.0.", OpenAPIDialect.V3_0),
        ("3.1.", OpenAPIDialect.V3_1),
        ("3.2.", OpenAPIDialect.V3_2),
    ):
        if version.startswith(prefix):
            return dialect
    code = "openapi.version"
    message = f"OpenAPI {version!r} is unsupported; expected a 3.0.x, 3.1.x, or 3.2.x document"
    raise error(
        code,
        message,
        SourceLocation(base_uri, "/openapi"),
    )


def _validation_diagnostic(base_uri: str, validation_error: JSONSchemaValidationError) -> Diagnostic:
    """Turn jsonschema's error path into a stable compiler diagnostic."""
    path = tuple(validation_error.absolute_path)
    return Diagnostic(
        code="openapi.invalid",
        message=validation_error.message,
        location=SourceLocation(base_uri, _pointer(path)),
    )


def _exception_chain_message(exc: Exception) -> str:
    """Preserve the actionable root cause hidden by reference resolver wrappers."""
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        message = str(current)
        if message and message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return ": ".join(messages)


def _exception_reference(exc: Exception) -> str | None:
    """Find the unresolved reference carried by a resolver exception chain."""
    current: BaseException | None = exc
    while current is not None:
        reference = getattr(current, "ref", None)
        if isinstance(reference, str):
            return reference
        current = current.__cause__ or current.__context__
    return None


def _find_reference_pointer(value: JSONValue, reference: str, parts: tuple[str | int, ...] = ()) -> str:
    """Locate an unresolved reference in the original document for a precise diagnostic."""
    if isinstance(value, dict):
        if value.get("$ref") == reference:
            return _pointer((*parts, "$ref"))
        for key, child in value.items():
            found = _find_reference_pointer(child, reference, (*parts, key))
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_reference_pointer(child, reference, (*parts, index))
            if found:
                return found
    return ""
