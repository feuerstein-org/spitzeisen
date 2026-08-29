"""Versioned contract between the Java Smithy frontend and Python rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from spitzeisen.params import QueryStyle

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type WireLocation = Literal["query", "header"]
type Shape = Literal["collection", "single"]
type NotFound = Literal["raise", "empty"]
type PaginationStyle = Literal["none", "page_number"]
type ActiveSortStyle = Literal["suffix", "param"]


@dataclass(frozen=True, slots=True)
class TargetSettings:
    """Python artifact settings that are intentionally not part of a service model."""

    package: str
    client_name: str
    service: str | None = None
    vendor: str | None = None


@dataclass(frozen=True, slots=True)
class ParamPlan:
    """One public Python argument and its wire representation."""

    name: str
    wire_name: str
    annotation: str
    description: str
    coercion: str
    location: WireLocation = "query"
    style: QueryStyle = "form"
    explode: bool = True
    required: bool = False
    client_default: str | None = "None"

    @property
    def runtime_annotation(self) -> str:
        """Annotation accepted by the opt-in strict input validator."""
        return f"{self.annotation} | None" if self.client_default == "None" else self.annotation


@dataclass(frozen=True, slots=True)
class PageSizePlan:
    """The query parameter controlling page size and its accepted maximum."""

    wire_name: str
    maximum: int


@dataclass(frozen=True, slots=True)
class SortArgumentPlan:
    """One synthesized sorting argument."""

    wire_name: str | None
    annotation: str
    default: JSONValue
    coercion: str
    style: QueryStyle = "form"
    explode: bool = True


@dataclass(frozen=True, slots=True)
class SortingPlan:
    """The two public sorting arguments and their vendor representation."""

    style: ActiveSortStyle
    sort: SortArgumentPlan
    order: SortArgumentPlan


@dataclass(frozen=True, slots=True)
class OperationPlan:
    """All facts needed to render one operation, with no raw Smithy state."""

    key: str
    path: str
    method_name: str
    model: str
    summary: str
    docs_url: str | None
    generate_model: bool
    shape: Shape
    not_found: NotFound
    cost: float
    pagination: PaginationStyle
    results_key: str | None
    page_param: str | None
    page_start: int
    page_step: int
    params: tuple[ParamPlan, ...]
    query_params: tuple[ParamPlan, ...]
    header_params: tuple[ParamPlan, ...]
    path_params: tuple[ParamPlan, ...]
    sorting: SortingPlan | None
    page_size: PageSizePlan | None
    example_args: tuple[str, ...]
    model_imports: tuple[str, ...]
    coerce_function_imports: tuple[str, ...]
    helpers: tuple[str, ...]

    @property
    def class_name(self) -> str:
        """Return the generated operation API class name."""
        return "".join(part.title() for part in self.key.split("_")) + "Api"

    @property
    def accessor(self) -> str:
        """Return the aggregate client's property name."""
        return f"{self.key}_api"

    @property
    def const_name(self) -> str:
        """Return the generated operation-spec constant name."""
        return f"{self.key.upper()}_OPERATION"


@dataclass(frozen=True, slots=True)
class ClientPlan:
    """The complete, renderer-ready SDK plan."""

    service_id: str
    vendor: str
    package: str
    client_name: str
    operations: tuple[OperationPlan, ...]
    response_shapes: tuple[str, ...]
    model_aliases: dict[str, str]
    model_type_overrides: dict[str, str]
