"""
Neutral intermediate representation for OpenAPI documents.

The classes in this module describe protocol facts only. They deliberately contain no Python
source fragments, imports, Jinja template names, runtime sentinels, or Spitzeisen policy. That
keeps parsing independent from the generated SDK design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spitzeisen.codegen.diagnostics import SourceLocation

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class OpenAPIDialect(StrEnum):
    """OpenAPI dialects accepted by the compiler."""

    V3_0 = "3.0"
    V3_1 = "3.1"
    V3_2 = "3.2"


class HTTPMethod(StrEnum):
    """HTTP methods that an OpenAPI Path Item can define."""

    GET = "get"
    PUT = "put"
    POST = "post"
    DELETE = "delete"
    OPTIONS = "options"
    HEAD = "head"
    PATCH = "patch"
    TRACE = "trace"
    QUERY = "query"


class ParameterLocation(StrEnum):
    """Where an operation parameter is serialized."""

    QUERY = "query"
    HEADER = "header"
    PATH = "path"
    COOKIE = "cookie"


class PrimitiveKind(StrEnum):
    """JSON Schema primitive types."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"


@dataclass(frozen=True, slots=True)
class SchemaId:
    """Canonical identity of a reusable or inline schema."""

    uri: str
    pointer: str = ""

    @property
    def canonical_uri(self) -> str:
        """Return an absolute URI including its fragment, when present."""
        return f"{self.uri}#{self.pointer}" if self.pointer else self.uri


@dataclass(frozen=True, slots=True)
class PrimitiveType:
    """A primitive JSON value and its optional semantic format."""

    kind: PrimitiveKind
    format: str | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class LiteralType:
    """A closed set of JSON values."""

    values: tuple[JSONScalar, ...]


@dataclass(frozen=True, slots=True)
class ArrayType:
    """A homogeneous JSON array."""

    items: TypeIR


@dataclass(frozen=True, slots=True)
class ObjectType:
    """A free-form or schema-constrained JSON object."""

    additional_properties: TypeIR | None = None


@dataclass(frozen=True, slots=True)
class ReferenceType:
    """A reference to a schema with stable identity."""

    schema_id: SchemaId
    suggested_name: str


@dataclass(frozen=True, slots=True)
class UnionType:
    """A value accepted by any of several schemas."""

    options: tuple[TypeIR, ...]


@dataclass(frozen=True, slots=True)
class IntersectionType:
    """A value constrained by every listed schema."""

    parts: tuple[TypeIR, ...]


@dataclass(frozen=True, slots=True)
class UnknownType:
    """A schema the compiler preserved but cannot express more precisely yet."""

    reason: str


type TypeIR = (
    PrimitiveType | LiteralType | ArrayType | ObjectType | ReferenceType | UnionType | IntersectionType | UnknownType
)


@dataclass(frozen=True, slots=True)
class MediaTypeIR:
    """One media representation of a request or response body."""

    media_type: str
    schema: TypeIR | None
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class ParameterIR:
    """One effective operation parameter after inheritance and dereferencing."""

    wire_name: str
    location: ParameterLocation
    required: bool
    schema: TypeIR | None
    content: tuple[MediaTypeIR, ...]
    style: str
    explode: bool
    allow_reserved: bool
    description: str
    default: JSONValue
    has_default: bool
    deprecated: bool
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class RequestBodyIR:
    """An operation request body in each declared representation."""

    required: bool
    description: str
    content: tuple[MediaTypeIR, ...]
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class StatusPattern:
    """A concrete, ranged, or default OpenAPI response status."""

    raw: str
    minimum: int | None
    maximum: int | None

    @property
    def is_default(self) -> bool:
        """Whether this pattern is OpenAPI's fallback response."""
        return self.minimum is None

    @property
    def is_success(self) -> bool:
        """Whether the pattern can describe only successful responses."""
        success_minimum = 200
        success_maximum = 300
        return (
            self.minimum is not None
            and self.minimum >= success_minimum
            and self.maximum is not None
            and self.maximum < success_maximum
        )


@dataclass(frozen=True, slots=True)
class ResponseIR:
    """One documented response status and its representations."""

    status: StatusPattern
    description: str
    content: tuple[MediaTypeIR, ...]
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class OperationIR:
    """One HTTP operation, independent from generated SDK policy."""

    source: SourceLocation
    method: HTTPMethod
    path: str
    operation_id: str | None
    tags: tuple[str, ...]
    summary: str
    description: str
    parameters: tuple[ParameterIR, ...]
    request_body: RequestBodyIR | None
    responses: tuple[ResponseIR, ...]
    deprecated: bool
    security: tuple[dict[str, tuple[str, ...]], ...]
    extensions: dict[str, JSONValue] = field(default_factory=dict[str, JSONValue])


@dataclass(frozen=True, slots=True)
class DocumentIR:
    """A fully validated OpenAPI document lowered into protocol facts."""

    source: SourceLocation
    dialect: OpenAPIDialect
    version: str
    title: str
    description: str
    operations: tuple[OperationIR, ...]

    def operation_at(self, path: str, method: HTTPMethod) -> OperationIR | None:
        """Find an operation by its wire path and HTTP method."""
        return next(
            (operation for operation in self.operations if operation.path == path and operation.method == method),
            None,
        )

    def operation_named(self, operation_id: str) -> OperationIR | None:
        """Find an operation by its OpenAPI operationId."""
        return next((operation for operation in self.operations if operation.operation_id == operation_id), None)
