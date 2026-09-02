"""Python protocol handlers selected by Smithy protocol trait ShapeId."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from spitzeisen.codegen.python_plan import PythonCoercionPlan, PythonTimestampFormat

if TYPE_CHECKING:
    from spitzeisen.codegen.service_plan import HttpBindingPlan, MemberPlan, ServicePlan, ShapePlan

REST_JSON_1 = "aws.protocols#restJson1"
GENERIC_REST_JSON = "spitzeisen.protocols#genericRestJson"
_TIMESTAMP_FORMAT = "smithy.api#timestampFormat"
_TIMESTAMP_FORMATS: tuple[PythonTimestampFormat, ...] = ("date-time", "http-date", "epoch-seconds")


class ProtocolSelectionError(ValueError):
    """No installed Python protocol handler matches the service."""


class ProtocolSerializationError(ValueError):
    """A protocol-bound value cannot be represented exactly by the Python runtime."""


class PythonProtocol(Protocol):
    """Narrow target protocol surface used by lowering."""

    @property
    def trait_id(self) -> str:
        """Return the Smithy protocol trait ShapeId handled by this implementation."""
        ...

    def parameter_coercion(
        self,
        plan: ServicePlan,
        member: MemberPlan,
        target: ShapePlan | None,
        binding: HttpBindingPlan,
    ) -> PythonCoercionPlan:
        """Select the base runtime coercion for an HTTP-bound input member."""
        ...


@dataclass(frozen=True, slots=True)
class RestJsonProtocol:
    """Base parameter behavior shared by restJson1 and explicit generic REST JSON."""

    trait_id: str = REST_JSON_1

    def parameter_coercion(
        self,
        plan: ServicePlan,
        member: MemberPlan,
        target: ShapePlan | None,
        binding: HttpBindingPlan,
    ) -> PythonCoercionPlan:
        """Leave values structured for Spitzeisen's query/header serializers."""
        timestamp = _timestamp_subject(plan, member, target)
        if timestamp is not None:
            timestamp_member, timestamp_shape, collection = timestamp
            timestamp_format = _resolve_timestamp_format(timestamp_member, timestamp_shape, binding)
            return PythonCoercionPlan(
                "timestamps" if collection else "timestamp",
                timestamp_format=timestamp_format,
            )
        return PythonCoercionPlan("identity")


class PythonProtocolRegistry:
    """Register and resolve Python handlers by exact Smithy protocol ShapeId."""

    def __init__(self, protocols: tuple[PythonProtocol, ...] = ()) -> None:
        """Create a registry and install the provided handlers in order."""
        self._protocols: dict[str, PythonProtocol] = {}
        for protocol in protocols:
            self.register(protocol)

    def register(self, protocol: PythonProtocol) -> None:
        """Register one handler, rejecting ambiguous duplicate registrations."""
        if protocol.trait_id in self._protocols:
            msg = f"Python protocol {protocol.trait_id!r} is already registered"
            raise ProtocolSelectionError(msg)
        self._protocols[protocol.trait_id] = protocol

    def resolve(
        self,
        service_protocols: tuple[str, ...],
        *,
        preference: tuple[str, ...] = (),
    ) -> PythonProtocol:
        """Resolve a declared protocol, honoring only preferences the service declares."""
        declared = set(service_protocols)
        ordered = (*[item for item in preference if item in declared], *service_protocols)
        for trait_id in dict.fromkeys(ordered):
            protocol = self._protocols.get(trait_id)
            if protocol is not None:
                return protocol
        installed = sorted(self._protocols)
        msg = f"service protocols {list(service_protocols)!r} have no Python handler; installed handlers: {installed}"
        raise ProtocolSelectionError(msg)

    def copy(self) -> PythonProtocolRegistry:
        """Return an independent registry so integrations never mutate caller-owned state."""
        return PythonProtocolRegistry(tuple(self._protocols.values()))

    @classmethod
    def default(cls) -> PythonProtocolRegistry:
        """Return the built-in, side-effect-free protocol registry."""
        return cls((RestJsonProtocol(), RestJsonProtocol(GENERIC_REST_JSON)))


def _timestamp_subject(
    plan: ServicePlan,
    member: MemberPlan,
    target: ShapePlan | None,
) -> tuple[MemberPlan, ShapePlan | None, bool] | None:
    if _is_timestamp(member.target, target):
        return member, target, False
    if target is None or target.kind not in {"list", "set"}:
        return None
    if len(target.members) != 1:
        msg = f"timestamp collection {target.id} must contain exactly one member"
        raise ProtocolSerializationError(msg)
    value_member = target.members[0]
    value_target = plan.shapes.get(value_member.target)
    if _is_timestamp(value_member.target, value_target):
        return value_member, value_target, True
    return None


def _is_timestamp(shape_id: str, shape: ShapePlan | None) -> bool:
    return shape_id == "smithy.api#Timestamp" or (shape is not None and shape.kind == "timestamp")


def _resolve_timestamp_format(
    member: MemberPlan,
    target: ShapePlan | None,
    binding: HttpBindingPlan,
) -> PythonTimestampFormat:
    raw: object | None = None
    owner = member.id
    if _TIMESTAMP_FORMAT in member.traits:
        raw = member.traits[_TIMESTAMP_FORMAT]
    elif target is not None and _TIMESTAMP_FORMAT in target.traits:
        raw = target.traits[_TIMESTAMP_FORMAT]
        owner = target.id
    if raw is None:
        return "http-date" if binding.location == "header" else "date-time"
    if not isinstance(raw, str) or raw not in _TIMESTAMP_FORMATS:
        msg = f"{_TIMESTAMP_FORMAT} on {owner} must be one of {_TIMESTAMP_FORMATS}, got {raw!r}"
        raise ProtocolSerializationError(msg)
    return raw
