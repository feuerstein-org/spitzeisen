"""
Target-neutral Smithy semantics exchanged across the codegen boundary.

The service plan deliberately contains no Python identifiers, annotations, imports, or
source fragments.  Shape and member relationships use Smithy ``ShapeId`` strings so a
target can apply its own symbol and type rules without reconstructing the Smithy model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type TraitNodes = dict[str, JSONValue]
type ShapeKind = Literal[
    "bigDecimal",
    "bigInteger",
    "blob",
    "boolean",
    "byte",
    "document",
    "double",
    "enum",
    "float",
    "intEnum",
    "integer",
    "list",
    "long",
    "map",
    "set",
    "short",
    "string",
    "structure",
    "timestamp",
    "union",
]
type HttpBindingLocation = Literal[
    "document",
    "header",
    "label",
    "payload",
    "prefix_headers",
    "query",
    "query_params",
    "response_code",
    "unbound",
]


def _empty_traits() -> TraitNodes:
    return {}


def _empty_external_documentation() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class DefaultValue:
    """A Smithy default with presence kept distinct from a JSON null value."""

    present: bool
    value: JSONValue = None


@dataclass(frozen=True, slots=True)
class MemberPlan:
    """One ordered member of a Smithy aggregate shape."""

    id: str
    name: str
    target: str
    required: bool
    client_nullable: bool
    default: DefaultValue
    documentation: str | None = None
    traits: TraitNodes = field(default_factory=_empty_traits)
    extensions: TraitNodes = field(default_factory=_empty_traits)
    constraints: TraitNodes = field(default_factory=_empty_traits)
    policies: TraitNodes = field(default_factory=_empty_traits)


@dataclass(frozen=True, slots=True)
class EnumValuePlan:
    """An ordered Smithy enum or intEnum value, including member metadata."""

    name: str
    value: str | int
    documentation: str | None = None
    traits: TraitNodes = field(default_factory=_empty_traits)
    extensions: TraitNodes = field(default_factory=_empty_traits)


@dataclass(frozen=True, slots=True)
class ShapePlan:
    """A shape in the service closure with its exact Smithy kind and graph edges."""

    id: str
    kind: ShapeKind
    recursive: bool
    documentation: str | None = None
    traits: TraitNodes = field(default_factory=_empty_traits)
    extensions: TraitNodes = field(default_factory=_empty_traits)
    constraints: TraitNodes = field(default_factory=_empty_traits)
    policies: TraitNodes = field(default_factory=_empty_traits)
    members: tuple[MemberPlan, ...] = ()
    enum_values: tuple[EnumValuePlan, ...] = ()

    def member(self, name_or_id: str) -> MemberPlan:
        """Return a member by local name or full ShapeId."""
        for member in self.members:
            if name_or_id in (member.name, member.id):
                return member
        msg = f"shape {self.id!r} has no member {name_or_id!r}"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class HttpBindingPlan:
    """The effective HTTP binding of one Smithy member."""

    member_id: str
    location: HttpBindingLocation
    name: str
    greedy: bool = False


@dataclass(frozen=True, slots=True)
class HttpErrorPlan:
    """Effective response code and bindings for one modeled error shape."""

    code: int
    bindings: tuple[HttpBindingPlan, ...] = ()


def _empty_error_bindings() -> dict[str, HttpErrorPlan]:
    return {}


@dataclass(frozen=True, slots=True)
class HttpPlan:
    """Target-neutral HTTP traits and effective member bindings."""

    method: str
    uri: str
    code: int
    request_bindings: tuple[HttpBindingPlan, ...] = ()
    response_bindings: tuple[HttpBindingPlan, ...] = ()
    error_bindings: dict[str, HttpErrorPlan] = field(default_factory=_empty_error_bindings)


@dataclass(frozen=True, slots=True)
class EventStreamsPlan:
    """Input/output event-stream member ShapeIds discovered by Smithy."""

    input: str | None = None
    output: str | None = None


@dataclass(frozen=True, slots=True)
class OperationPlan:
    """One service operation and the Smithy relationships needed by all targets."""

    id: str
    input: str
    output: str
    errors: tuple[str, ...]
    auth_schemes: tuple[str, ...]
    documentation: str | None = None
    external_documentation: dict[str, str] = field(default_factory=_empty_external_documentation)
    http: HttpPlan | None = None
    event_streams: EventStreamsPlan | None = None
    traits: TraitNodes = field(default_factory=_empty_traits)
    extensions: TraitNodes = field(default_factory=_empty_traits)
    policies: TraitNodes = field(default_factory=_empty_traits)


@dataclass(frozen=True, slots=True)
class Service:
    """The selected Smithy service and its effective protocol/auth metadata."""

    id: str
    version: str
    protocols: tuple[str, ...]
    auth_schemes: tuple[str, ...]
    operations: tuple[str, ...]
    title: str | None = None
    documentation: str | None = None
    external_documentation: dict[str, str] = field(default_factory=_empty_external_documentation)
    traits: TraitNodes = field(default_factory=_empty_traits)
    extensions: TraitNodes = field(default_factory=_empty_traits)
    policies: TraitNodes = field(default_factory=_empty_traits)


@dataclass(frozen=True, slots=True)
class ServicePlan:
    """The complete target-neutral input to runtime-specific lowering."""

    service: Service
    operations: dict[str, OperationPlan]
    shapes: dict[str, ShapePlan]
    extensions: TraitNodes = field(default_factory=_empty_traits)

    def operation(self, shape_id: str) -> OperationPlan:
        """Return a selected operation by ShapeId."""
        try:
            return self.operations[shape_id]
        except KeyError:
            msg = f"service plan has no operation {shape_id!r}"
            raise KeyError(msg) from None

    def shape(self, shape_id: str) -> ShapePlan:
        """Return a shape in the compiled service closure by ShapeId."""
        try:
            return self.shapes[shape_id]
        except KeyError:
            msg = f"service plan has no shape {shape_id!r}"
            raise KeyError(msg) from None
