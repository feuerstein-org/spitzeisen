"""
Helpers for turning user-supplied arguments into wire-ready query parameters.

Nothing here is API-specific: each helper takes the closed value set it should validate
against, so a client library keeps ownership of its own `Literal` types.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, cast, get_args

from typing_extensions import TypeForm

QueryStyle = Literal["form", "spaceDelimited", "pipeDelimited"]
_SUPPORTED_QUERY_STYLES = frozenset(get_args(QueryStyle))


@dataclass(frozen=True, slots=True)
class SerializedQueryParam:
    """One query-string key/value pair."""

    name: str
    value: str


type QueryParams = list[SerializedQueryParam]


def coerce_choice[T](value: T | None, literal: TypeForm[T], param_name: str) -> T | None:
    """
    Validate `value` against the members of a Literal type (str or int members).

    Returns the value unchanged, or None if it is None. Raises ValueError (listing the
    allowed values) when `value` is not one of the Literal's members.
    """
    if value is None:
        return None
    allowed = cast("tuple[T, ...]", get_args(literal))
    if value not in allowed:
        joined = ", ".join(str(member) for member in allowed)
        msg = f"Invalid {param_name} {value!r}. Allowed values: {joined}."
        raise ValueError(msg)
    return value


def require_value[T](value: T | None, param_name: str) -> T:
    """Return a required wire value, rejecting an absent one before a request is sent."""
    if value is None:
        msg = f"Required parameter {param_name!r} was not provided."
        raise ValueError(msg)
    return value


def coerce_choices[T: str](values: Sequence[T] | None, literal: TypeForm[T], param_name: str) -> str | None:
    """
    Validate a list of Literal members and join them into a comma-separated string.

    Used for multi-value filters such as `<field>.any_of=<val1>,<val2>`. Returns None for a None or empty
    list. Raises ValueError (listing the allowed values) if any element is invalid.
    """
    if not values:
        return None
    allowed = cast("tuple[T, ...]", get_args(literal))
    invalid = [value for value in values if value not in allowed]
    if invalid:
        joined = ", ".join(allowed)
        msg = f"Invalid {param_name} {invalid!r}. Allowed values: {joined}."
        raise ValueError(msg)
    return ",".join(values)


# TODO: Should this live here?
def coerce_sort(sort: object, order: object) -> str:
    """
    Combine an already-coerced field and direction into the `field.direction` form.

    Allowed values belong to the vendor's OpenAPI parameters or client-owned Literal types;
    callers validate them with `coerce_choice` before combining them here.
    """
    if sort is None or sort == "" or order is None or order == "":
        msg = "order or sort were not provided."
        raise ValueError(msg)
    return f"{sort}.{order}"


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


def _stringify(value: object) -> str:
    """Turn one scalar into its query/header representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _values(value: object) -> list[str]:
    """Convert an arrays values into strings. E.g. True -> 'true' etc."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_stringify(item) for item in cast("Sequence[object]", value)]
    return [_stringify(value)]


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
    Serialize one OpenAPI query parameter into the exact key/value pairs it requires as defined in spec or manifest.

    Both, arrays and mappings are supported, if a required parameter is None, ValueError is raised.

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
        msg = f"query parameter style {style!r} is unsupported"
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
    """Serialize an OpenAPI scalar or array parameter."""
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
    """Serialize a one-level OpenAPI object parameter."""
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
    """Build header values, where every parameter has exactly one name/value pair."""
    return {key: _stringify(value) for key, value in raw.items() if value is not None}
