"""Parameter coercion: the helpers that turn user arguments into wire values."""

from datetime import date, datetime
from typing import Literal, assert_type, cast

import pytest

from spitzeisen import (
    build_header_params,
    coerce_choice,
    coerce_choices,
    coerce_date,
    coerce_sort,
    require_value,
    serialize_query_param,
)
from spitzeisen.params import QueryStyle, SerializedQueryParam

Colour = Literal["red", "green", "blue"]


def test_coerce_choice_accepts_a_member() -> None:
    """A valid member passes through unchanged."""
    value = coerce_choice("red", Colour, "colour")

    assert_type(value, Colour | None)
    assert value == "red"


def test_coerce_choice_passes_none_through() -> None:
    """None means "not supplied" and is never an error."""
    assert coerce_choice(None, Colour, "colour") is None


def test_coerce_choice_lists_allowed_values_on_error() -> None:
    """The error names the parameter and every value the API accepts."""
    with pytest.raises(ValueError, match=r"Invalid colour 'purple'. Allowed values: red, green, blue."):
        coerce_choice("purple", Colour, "colour")


@pytest.mark.parametrize("value", [False, 0, ""])
def test_require_value_preserves_present_falsy_values(value: object) -> None:
    """Only None means absent; falsey values can be valid API inputs."""
    assert require_value(value, "exchange") is value


def test_require_value_rejects_none() -> None:
    """A required value must not reach query-param construction as None."""
    with pytest.raises(ValueError, match=r"Required parameter 'exchange' was not provided"):
        require_value(None, "exchange")


def test_coerce_choices_joins_into_a_csv_filter() -> None:
    """Multi-value filters go on the wire comma-separated."""
    assert coerce_choices(["red", "blue"], Colour, "colours") == "red,blue"


@pytest.mark.parametrize("empty", [None, []])
def test_coerce_choices_treats_empty_as_absent(empty: list[str] | None) -> None:
    """An empty selection is not a filter at all."""
    assert coerce_choices(empty, Colour, "colours") is None


def test_coerce_choices_reports_every_invalid_member() -> None:
    """All offending values are named at once rather than one per round trip."""
    with pytest.raises(ValueError, match=r"\['purple', 'teal'\]"):
        coerce_choices(["red", "purple", "teal"], Colour, "colours")


def test_coerce_sort_builds_the_suffix_form_without_imposing_vendor_values() -> None:
    """The combiner accepts the direction vocabulary already validated for this vendor."""
    assert coerce_sort("name", "upward") == "name.upward"


def test_coerce_sort_rejects_an_empty_component() -> None:
    """A suffix cannot be constructed when either already-coerced component is empty."""
    with pytest.raises(ValueError, match="order or sort were not provided"):
        coerce_sort("name", "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-01-31", "2025-01-31"),
        (date(2025, 1, 31), "2025-01-31"),
        (datetime(2025, 1, 31, 14, 30), "2025-01-31"),
        (None, None),
    ],
)
def test_coerce_date_normalizes_every_accepted_form(value: object, expected: str | None) -> None:
    """Strings, dates and datetimes all reduce to an ISO date."""
    assert coerce_date(value, "since") == expected  # type: ignore[arg-type]


def test_coerce_date_rejects_a_non_iso_string() -> None:
    """A malformed date fails locally rather than as a confusing 400."""
    with pytest.raises(ValueError, match="Expected an ISO date"):
        coerce_date("31/01/2025", "since")


def test_serialize_query_param_drops_none_and_normalizes_scalars() -> None:
    """None disappears and scalar values are normalized into one ordered parameter list."""
    params = [
        *serialize_query_param(None, name="absent"),
        *serialize_query_param(value=True, name="active"),
        *serialize_query_param(value=False, name="inactive"),
        *serialize_query_param(date(2025, 1, 31), name="since"),
        *serialize_query_param(datetime(2025, 1, 31, 14, 30), name="at"),
        *serialize_query_param(100, name="limit"),
    ]

    assert params == [
        SerializedQueryParam("active", "true"),
        SerializedQueryParam("inactive", "false"),
        SerializedQueryParam("since", "2025-01-31"),
        SerializedQueryParam("at", "2025-01-31"),
        SerializedQueryParam("limit", "100"),
    ]


@pytest.mark.parametrize(
    ("value", "style", "explode", "expected"),
    [
        (["AAPL", "MSFT"], "form", True, [("symbol", "AAPL"), ("symbol", "MSFT")]),
        (["AAPL", "MSFT"], "form", False, [("symbol", "AAPL,MSFT")]),
        (["AAPL", "MSFT"], "spaceDelimited", False, [("symbol", "AAPL MSFT")]),
        (["AAPL", "MSFT"], "pipeDelimited", False, [("symbol", "AAPL|MSFT")]),
    ],
)
def test_serialize_query_param_handles_openapi_arrays(
    value: list[str], style: QueryStyle, explode: bool, expected: list[tuple[str, str]]
) -> None:
    """The standard query array styles produce their corresponding wire pairs."""
    serialized = serialize_query_param(value, name="symbol", style=style, explode=explode)

    assert [(item.name, item.value) for item in serialized] == expected


@pytest.mark.parametrize(
    ("style", "explode", "expected"),
    [
        ("form", True, [("role", "admin"), ("active", "true")]),
        ("form", False, [("filter", "role,admin,active,true")]),
    ],
)
def test_serialize_query_param_handles_openapi_objects(
    style: QueryStyle, explode: bool, expected: list[tuple[str, str]]
) -> None:
    """Supported form object representations retain their OpenAPI semantics."""
    serialized = serialize_query_param({"role": "admin", "active": True}, name="filter", style=style, explode=explode)

    assert [(item.name, item.value) for item in serialized] == expected


def test_serialize_query_param_rejects_deep_object() -> None:
    """Unsupported OpenAPI query styles fail rather than emitting an incorrect request."""
    with pytest.raises(ValueError, match="style 'deepObject' is unsupported"):
        serialize_query_param({"role": "admin"}, name="filter", style=cast("QueryStyle", "deepObject"))


def test_serialize_query_param_rejects_a_required_empty_array() -> None:
    """An empty collection cannot silently satisfy a required query parameter."""
    with pytest.raises(ValueError, match=r"Required parameter 'symbol' was not provided"):
        serialize_query_param([], name="symbol", required=True)


def test_build_header_params_keeps_header_values_scalar() -> None:
    """Headers continue to use a simple name/value mapping, unlike query strings."""
    assert build_header_params({"X-Active": True, "absent": None}) == {"X-Active": "true"}
