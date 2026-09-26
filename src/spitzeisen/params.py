"""
Helpers for turning user-supplied arguments into HTTP wire values.

Query collections use repeated keys. SDKs own input validation and any vendor-specific
formats, such as comma-separated filter values.
"""

from collections.abc import Collection, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from urllib.parse import quote

type ParamScalar = str | int | float | bool | Decimal | date
type ParamValue = ParamScalar | Sequence[ParamScalar] | AbstractSet[ParamScalar] | None


@dataclass(frozen=True, slots=True)
class SerializedQueryParam:
    """One query-string key/value pair."""

    name: str
    value: str


type QueryParams = list[SerializedQueryParam]


def _stringify(value: object) -> str:
    """Validate one scalar at the wire boundary and turn it into text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        msg = "datetime values must be formatted by the SDK before serialization"
        raise TypeError(msg)
    if isinstance(value, str | int | float | Decimal | date):
        return str(value)
    msg = f"Unsupported HTTP parameter type: {type(value).__name__}"
    raise TypeError(msg)


def _values(value: ParamValue) -> list[str]:
    """Normalize optional scalars or flat collections into strings."""
    if value is None:
        return []
    if isinstance(value, Sequence | AbstractSet) and not isinstance(value, str | bytes | bytearray):
        return [_stringify(item) for item in value]
    return [_stringify(value)]


def serialize_path_param(value: ParamScalar, *, greedy: bool = False) -> str:
    """Serialize one HTTP path parameter, preserving slashes only for greedy labels."""
    if cast("object", value) is None or value == "":
        msg = "HTTP path labels must have a non-empty value."
        raise ValueError(msg)
    return quote(_stringify(value), safe="/" if greedy else "")


def serialize_query_param(value: ParamValue, *, name: str) -> QueryParams:
    """Serialize a scalar or repeated query binding, omitting None and empty collections."""
    if isinstance(value, Mapping):
        msg = "Query mappings should be passed to serialize_query_map."
        raise TypeError(msg)
    return [SerializedQueryParam(name, item) for item in _values(value)]


def serialize_query_map(
    value: Mapping[str, ParamValue] | None,
    *,
    reserved: Collection[str] = (),
) -> QueryParams:
    """Serialize a query mapping, omitting None and preserving lists and reserved names."""
    if value is None:
        return []
    return [
        item
        for name, member in value.items()
        if name not in reserved
        for item in serialize_query_param(member, name=name)
    ]


def build_header_params(raw: Mapping[str, ParamValue]) -> dict[str, str]:
    """Build header values, where every param has exactly one name/value pair."""
    return {key: ",".join(_values(value)) for key, value in raw.items() if value is not None}


def resolve_page_size(max_results: int | None, maximum: int) -> int:
    """
    Choose a request page size within the SDK's vendor limit and the total record cap.

    The SDK owns the vendor maximum. ``None`` requests that full page size. Reject
    nonpositive values before the first request, matching collection helper semantics.
    """
    if maximum < 1:
        msg = f"maximum must be >= 1, got {maximum}"
        raise ValueError(msg)
    if max_results is not None and max_results < 1:
        msg = f"max_results must be >= 1, got {max_results}"
        raise ValueError(msg)
    return maximum if max_results is None else min(max_results, maximum)
