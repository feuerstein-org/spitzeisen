"""Renderer-ready facts produced by Python-specific lowering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]

type PythonWireLocation = Literal["header", "path", "query"]
type PythonQueryStyle = Literal["form", "spaceDelimited", "pipeDelimited"]
type PythonResponseCardinality = Literal["collection", "single"]
type PythonNotFound = Literal["absent", "raise"]
type PythonPaginationStyle = Literal["none", "page_number", "smithy"]
type PythonSortStyle = Literal["separate", "suffix"]
type PythonTypeKind = Literal[
    "bool",
    "bytes",
    "date_input",
    "datetime",
    "decimal",
    "dict",
    "document",
    "float",
    "int",
    "list",
    "literal",
    "set",
    "str",
    "symbol",
    "union",
]
type PythonCoercionKind = Literal[
    "choice",
    "choices",
    "custom",
    "date",
    "identity",
    "join",
    "timestamp",
    "timestamps",
]
type PythonTimestampFormat = Literal["date-time", "http-date", "epoch-seconds"]
type PythonDefaultKind = Literal["required", "value"]


def _empty_strings() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class PythonImport:
    """One exact Python import owned by a target symbol or adapter."""

    module: str
    name: str
    alias: str | None = None

    @property
    def identifier(self) -> str:
        """Return the identifier used at Python call sites."""
        return self.alias or self.name


@dataclass(frozen=True, slots=True)
class PythonTypePlan:
    """A structured Python annotation chosen by the target symbol provider."""

    kind: PythonTypeKind
    name: str | None = None
    module: str | None = None
    values: tuple[JSONScalar, ...] = ()
    members: tuple[PythonTypePlan, ...] = ()


@dataclass(frozen=True, slots=True)
class PythonCoercionPlan:
    """One Python runtime coercion selected during target lowering."""

    kind: PythonCoercionKind
    separator: str | None = None
    literal_type: PythonTypePlan | None = None
    function: PythonImport | None = None
    timestamp_format: PythonTimestampFormat | None = None


@dataclass(frozen=True, slots=True)
class PythonDefaultPlan:
    """A Python signature default, including the absence of any default."""

    kind: PythonDefaultKind
    value: JSONValue = None

    @property
    def is_required(self) -> bool:
        """Whether the generated argument has no default expression."""
        return self.kind == "required"

    @property
    def allows_none(self) -> bool:
        """Whether the generated annotation needs an explicit ``None`` branch."""
        return self.kind == "value" and self.value is None


@dataclass(frozen=True, slots=True)
class PythonParameterPlan:
    """One public Python argument and its selected wire behavior."""

    member_id: str
    name: str
    wire_name: str
    type: PythonTypePlan
    description: str
    coercion: PythonCoercionPlan
    location: PythonWireLocation
    greedy: bool = False
    style: PythonQueryStyle = "form"
    explode: bool = True
    allow_reserved: bool = False
    required: bool = False
    default: PythonDefaultPlan = PythonDefaultPlan("value")


@dataclass(frozen=True, slots=True)
class PythonPageSizePlan:
    """Python pagination's public page-size control."""

    wire_name: str
    maximum: int


@dataclass(frozen=True, slots=True)
class PythonSortArgumentPlan:
    """One synthesized Python sorting argument."""

    name: str
    wire_name: str | None
    type: PythonTypePlan
    default: PythonDefaultPlan
    coercion: PythonCoercionPlan
    style: PythonQueryStyle = "form"
    explode: bool = True
    allow_reserved: bool = False


@dataclass(frozen=True, slots=True)
class PythonSortingPlan:
    """Python-facing sorting arguments and their runtime representation."""

    style: PythonSortStyle
    separator: str | None
    sort: PythonSortArgumentPlan
    order: PythonSortArgumentPlan


@dataclass(frozen=True, slots=True)
class PythonSmithyPaginationPlan:
    """Resolved standard Smithy pagination member names for Python generation."""

    input_token: str
    output_tokens: tuple[str, ...]
    page_size: str | None
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PythonOperationPlan:
    """All Python renderer facts for one selected operation."""

    shape_id: str
    key: str
    path: str
    method_name: str
    class_name: str
    accessor: str
    const_name: str
    response_shape: str
    model: str
    model_module: str
    summary: str
    docs_url: str | None
    generate_model: bool
    response_cardinality: PythonResponseCardinality
    not_found: PythonNotFound
    cost: float
    pagination: PythonPaginationStyle
    results_key: str | None
    page_param: str | None
    page_start: int
    page_step: int
    params: tuple[PythonParameterPlan, ...]
    query_params: tuple[PythonParameterPlan, ...]
    header_params: tuple[PythonParameterPlan, ...]
    path_params: tuple[PythonParameterPlan, ...]
    sorting: PythonSortingPlan | None
    page_size: PythonPageSizePlan | None
    smithy_pagination: PythonSmithyPaginationPlan | None
    example_args: tuple[str, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PythonPlan:
    """The complete renderer-ready Python SDK plan."""

    service_id: str
    protocol_id: str
    vendor: str
    package: str
    client_name: str
    operations: tuple[PythonOperationPlan, ...]
    response_shapes: tuple[str, ...]
    model_aliases: dict[str, str] = field(default_factory=_empty_strings)
    dependencies: tuple[str, ...] = ()
