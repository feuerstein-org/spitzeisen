"""
Protocol types retained after parsing the upstream OpenAPI models.

The parser owns operations and their grouping, just as openapi-python-client does. This
module only contains the small, generator-neutral type tree used by Spitzeisen policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

SUCCESS_MINIMUM = 200
SUCCESS_MAXIMUM = 300


class HTTPMethod(StrEnum):
    """HTTP methods parsed by openapi-python-client's operation workflow."""

    GET = "get"
    PUT = "put"
    POST = "post"
    DELETE = "delete"
    OPTIONS = "options"
    HEAD = "head"
    PATCH = "patch"
    TRACE = "trace"
    # TODO: Add QUERY support?


class ParamLocationIR(StrEnum):
    """Where an operation param is serialized."""

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
    """The local JSON Pointer carried by an OpenAPI reference."""

    reference: str

    @property
    def canonical_uri(self) -> str:
        """Return the reference in its original local-document form."""
        return f"#{self.reference}"


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
    """A reference to a reusable schema."""

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
    """A schema the parser cannot express more precisely yet."""

    reason: str


type TypeIR = (
    PrimitiveType | LiteralType | ArrayType | ObjectType | ReferenceType | UnionType | IntersectionType | UnknownType
)


@dataclass(frozen=True, slots=True)
class MediaTypeIR:
    """One media representation of a request or response body."""

    media_type: str
    schema: TypeIR | None


@dataclass(frozen=True, slots=True)
class ParamIR:
    """One effective operation param."""

    wire_name: str
    location: ParamLocationIR
    required: bool
    schema: TypeIR | None
    content: tuple[MediaTypeIR, ...]
    style: str | None
    explode: bool
    allow_reserved: bool
    description: str
    default: JSONValue
    # Need this field because otherwise if the default is actually "None" we wont know
    # since our field is also "None"
    has_default: bool
    deprecated: bool


@dataclass(frozen=True, slots=True)
class RequestBodyIR:
    """An operation request body in each declared representation."""

    required: bool
    description: str
    content: tuple[MediaTypeIR, ...]


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
        """Whether this pattern describes only successful responses."""
        return (
            self.minimum is not None
            and self.minimum >= SUCCESS_MINIMUM
            and self.maximum is not None
            and self.maximum < SUCCESS_MAXIMUM
        )


@dataclass(frozen=True, slots=True)
class ResponseIR:
    """One documented response status and its representations."""

    status: StatusPattern
    description: str
    content: tuple[MediaTypeIR, ...]
