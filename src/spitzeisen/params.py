"""
Helpers for turning user-supplied arguments into wire-ready query params.

Nothing here is API-specific: each helper takes the closed value set it should validate
against, so a client library keeps ownership of its own `Literal` types.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import format_datetime
from typing import Literal, cast, get_args
from urllib.parse import quote

from typing_extensions import TypeForm

QueryStyle = Literal["form", "spaceDelimited", "pipeDelimited"]
TimestampFormat = Literal["date-time", "http-date", "epoch-seconds"]
_SUPPORTED_QUERY_STYLES = frozenset(get_args(QueryStyle))
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECONDS_PER_SECOND = 1000
_MICROSECONDS_PER_MILLISECOND = 1000


@dataclass(frozen=True, slots=True)
class SerializedQueryParam:
    """One query-string key/value pair."""

    name: str
    value: str


type QueryParams = list[SerializedQueryParam]


def coerce_choice[T](value: T | None, literal_type: TypeForm[T], param_name: str) -> T | None:
    """
    Validate `value` against the members of a Literal type (str or int members).

    Returns the value unchanged, or None if it is None. Raises ValueError (listing the
    allowed values) when `value` is not one of the Literal's members.
    """
    if value is None:
        return None
    allowed = cast("tuple[T, ...]", get_args(literal_type))
    if value not in allowed:
        joined = ", ".join(str(member) for member in allowed)
        msg = f"Invalid {param_name} {value!r}. Allowed values: {joined}."
        raise ValueError(msg)
    return value


def require_value[T](value: T | None, param_name: str) -> T:
    """Return a required wire value, rejecting an absent one before a request is sent."""
    if value is None:
        msg = f"Required param {param_name!r} was not provided."
        raise ValueError(msg)
    return value


def coerce_choices[T](
    values: Collection[T] | None,
    literal_type: TypeForm[T],
    param_name: str,
) -> Collection[T] | None:
    """
    Validate a collection of Literal members without choosing its wire encoding.

    Returns the input collection unchanged so ``serialize_query_param`` can independently
    apply the modeled style and explode setting. None and empty collections become None.
    """
    if not values:
        return None
    allowed = cast("tuple[T, ...]", get_args(literal_type))
    invalid = [value for value in values if value not in allowed]
    if invalid:
        joined = ", ".join(str(member) for member in allowed)
        msg = f"Invalid {param_name} {invalid!r}. Allowed values: {joined}."
        raise ValueError(msg)
    return values


# TODO: Should this live here?
def coerce_sort(sort: object, order: object, *, separator: str = ".") -> str:
    """
    Combine an already-coerced field and direction into the `field.direction` form.

    Allowed values belong to the vendor's OpenAPI params or client-owned Literal types;
    callers validate them with `coerce_choice` before combining them here.
    """
    if sort is None or sort == "" or order is None or order == "":
        msg = "order or sort were not provided."
        raise ValueError(msg)
    return f"{sort}{separator}{order}"


def coerce_date(value: str | date | datetime | None, param_name: str) -> str | None:
    """
    Normalize a date value to a "YYYY-MM-DD" string.

    Accepts a date/datetime object or an ISO date string. Returns None if `value` is None.
    Raises ValueError when a string is not a valid ISO date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        msg = f"Invalid {param_name} {value!r}. Expected an ISO date (YYYY-MM-DD)."
        raise ValueError(msg) from None


def coerce_timestamp(
    value: datetime | None,
    timestamp_format: TimestampFormat,
    param_name: str,
) -> str | None:
    """Serialize one timezone-aware datetime using an exact Smithy timestamp format."""
    if value is None:
        return None
    value = _require_datetime(value, param_name)
    if value.utcoffset() is None:
        msg = f"Invalid {param_name} {value!r}. Smithy timestamps must include a timezone."
        raise ValueError(msg)
    utc_value = value.astimezone(UTC)
    if timestamp_format == "date-time":
        return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if timestamp_format == "http-date":
        return format_datetime(utc_value.replace(microsecond=0), usegmt=True)
    if timestamp_format == "epoch-seconds":
        delta = utc_value - _EPOCH
        milliseconds = (
            delta.days * 24 * 60 * 60 + delta.seconds
        ) * _MILLISECONDS_PER_SECOND + delta.microseconds // _MICROSECONDS_PER_MILLISECOND
        return str(Decimal(milliseconds) / Decimal(_MILLISECONDS_PER_SECOND))
    msg = f"Unsupported Smithy timestamp format {timestamp_format!r}."
    raise ValueError(msg)


def _require_datetime(value: object, param_name: str) -> datetime:
    if not isinstance(value, datetime):
        msg = f"Invalid {param_name} {value!r}. Expected a datetime."
        raise TypeError(msg)
    return value


def coerce_timestamps(
    values: Collection[datetime] | None,
    timestamp_format: TimestampFormat,
    param_name: str,
) -> Collection[str] | None:
    """Serialize a collection of timestamps while leaving its wire style to the binding."""
    if not values:
        return None
    return [
        cast("str", coerce_timestamp(value, timestamp_format, f"{param_name}[{index}]"))
        for index, value in enumerate(values)
    ]


def _stringify(value: object) -> str:
    """Turn one scalar into its query/header representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        msg = "datetime values require an explicit Smithy timestamp format"
        raise TypeError(msg)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _values(value: object) -> list[str]:
    """Convert collection values into strings. E.g. True -> 'true' etc."""
    if isinstance(value, Collection) and not isinstance(value, str | bytes | bytearray):
        return [_stringify(item) for item in cast("Collection[object]", value)]
    return [_stringify(value)]


def serialize_path_param(value: object, *, greedy: bool = False) -> str:
    """Serialize one Smithy HTTP label, preserving slashes only for greedy labels."""
    return quote(_stringify(value), safe="/" if greedy else "")


def serialize_query_param(
    value: object,
    *,
    name: str,
    style: QueryStyle = "form",
    explode: bool = True,
    required: bool = False,
    param_name: str | None = None,
) -> QueryParams:
    """
    Serialize one Smithy-bound query member into the exact key/value pairs required by its protocol traits.

    Both, arrays and mappings are supported, if a required param is None, ValueError is raised.

    For example, an array with ``explode=True`` becomes repeated keys:

        serialize_query_param(["AAPL", "MSFT"], name="symbol", explode=True)
        # [SerializedQueryParam("symbol", "AAPL"), SerializedQueryParam("symbol", "MSFT")]

    With ``explode=False``, the array is one comma-separated value:

        serialize_query_param(["AAPL", "MSFT"], name="symbol", explode=False)
        # [SerializedQueryParam("symbol", "AAPL,MSFT")]

    You acn also pass a Mapping:

        serialize_query_param({"role": "admin", "active": True}, name="filter", explode=True)
        # [SerializedQueryParam("role", "admin"), SerializedQueryParam("active", "true")]
    """
    if style not in _SUPPORTED_QUERY_STYLES:
        msg = f"query param style {style!r} is unsupported"
        raise ValueError(msg)
    if value is None:
        if required:
            require_value(value, param_name or name)
        return []

    if isinstance(value, Mapping):
        items = _serialize_object(
            cast("Mapping[object, object]", value),
            name=name,
            style=style,
            explode=explode,
        )
    else:
        items = _serialize_array_or_scalar(
            value,
            name=name,
            style=style,
            explode=explode,
        )
    if required and not items:
        require_value(None, param_name or name)
    return items


def _serialize_array_or_scalar(
    value: object,
    *,
    name: str,
    style: QueryStyle,
    explode: bool,
) -> QueryParams:
    """Serialize an OpenAPI scalar or array param."""
    values = _values(value)
    if not values:
        return []
    if len(values) == 1:
        return [SerializedQueryParam(name, values[0])]
    if style == "form" and explode:
        return [SerializedQueryParam(name, item) for item in values]
    separator = {"form": ",", "spaceDelimited": " ", "pipeDelimited": "|"}[style]
    return [SerializedQueryParam(name, separator.join(values))]


def _serialize_object(
    value: Mapping[object, object],
    *,
    name: str,
    style: QueryStyle,
    explode: bool,
) -> QueryParams:
    """Serialize a one-level OpenAPI object param."""
    members = [(str(key), _stringify(member)) for key, member in value.items()]
    if not members:
        return []
    if style == "form" and explode:
        return [SerializedQueryParam(key, member) for key, member in members]
    if style != "form":
        msg = f"style={style!r} does not support object values"
        raise ValueError(msg)
    flattened = ",".join(part for member in members for part in member)
    return [SerializedQueryParam(name, flattened)]


def build_header_params(raw: Mapping[str, object]) -> dict[str, str]:
    """Build header values, where every param has exactly one name/value pair."""
    return {key: ",".join(_values(value)) for key, value in raw.items() if value is not None}
