"""HTTP parameter serialization and pagination bounds."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest

from spitzeisen import (
    ParamScalar,
    ParamValue,
    build_header_params,
    resolve_page_size,
    serialize_path_param,
    serialize_query_map,
    serialize_query_param,
)
from spitzeisen.params import SerializedQueryParam

TIMESTAMP = datetime(2025, 1, 31, 14, 30, tzinfo=UTC)
TIMESTAMP_ERROR = "datetime values must be formatted by the SDK before serialization"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (date(2025, 1, 31), "2025-01-31"),
        ("2025-01-31T14:30:00.000Z", "2025-01-31T14:30:00.000Z"),
        (100, "100"),
        (1.5, "1.5"),
        (Decimal("12.340"), "12.340"),
        (0, "0"),
        ("", ""),
    ],
)
def test_serializers_normalize_scalars(value: ParamScalar, expected: str) -> None:
    """Query and header scalars share wire formats, while absent headers are omitted."""
    assert serialize_query_param(value, name="value") == [SerializedQueryParam("value", expected)]
    assert build_header_params({"X-Value": value, "absent": None}) == {"X-Value": expected}


@pytest.mark.parametrize("value", [None, [], (), set[str]()])
def test_serialize_query_param_omits_absent_collections(value: ParamValue) -> None:
    """Missing values and empty collections contribute no query bindings."""
    assert serialize_query_param(value, name="symbol") == []


@pytest.mark.parametrize("value", [{}, {"role": "admin"}])
def test_serialize_query_param_directs_mappings_to_query_map(value: object) -> None:
    """A mapping requires explicit query-map serialization instead of emitting its keys."""
    with pytest.raises(TypeError, match="serialize_query_map"):
        serialize_query_param(cast("ParamValue", value), name="filter")


@pytest.mark.parametrize(
    ("binding", "value", "message"),
    [
        ("query", object(), "Unsupported HTTP parameter type: object"),
        ("query", b"abc", "Unsupported HTTP parameter type: bytes"),
        ("query", bytearray(b"abc"), "Unsupported HTTP parameter type: bytearray"),
        ("query", [["nested"]], "Unsupported HTTP parameter type: list"),
        ("query", [None], "Unsupported HTTP parameter type: NoneType"),
        ("header", {"role": "admin"}, "Unsupported HTTP parameter type: dict"),
        ("header", [{"role": "admin"}], "Unsupported HTTP parameter type: dict"),
        ("header", [None], "Unsupported HTTP parameter type: NoneType"),
        ("path", object(), "Unsupported HTTP parameter type: object"),
        ("path", ["segment"], "Unsupported HTTP parameter type: list"),
        ("query", TIMESTAMP, TIMESTAMP_ERROR),
        ("query", [TIMESTAMP], TIMESTAMP_ERROR),
        ("path", TIMESTAMP, TIMESTAMP_ERROR),
        ("header", TIMESTAMP, TIMESTAMP_ERROR),
    ],
)
def test_serializers_reject_unsupported_values(binding: str, value: object, message: str) -> None:
    """Invalid inputs fail instead of leaking Python representations into HTTP values."""
    serializers: dict[str, Callable[[], object]] = {
        "path": lambda: serialize_path_param(cast("ParamScalar", value)),
        "header": lambda: build_header_params({"X-Value": cast("ParamValue", value)}),
        "query": lambda: serialize_query_param(cast("ParamValue", value), name="filter"),
    }

    with pytest.raises(TypeError, match=message):
        serializers[binding]()


def test_serialize_query_map_preserves_explicit_bindings() -> None:
    """Explicit query names take precedence while other map values retain their wire forms."""
    assert serialize_query_map(
        {
            "symbol": ["ignored"],
            "limit": 999,
            "market": ["US", "GB"],
            "active": True,
            "absent": None,
            "empty": [],
        },
        reserved=("symbol", "limit"),
    ) == [
        SerializedQueryParam("market", "US"),
        SerializedQueryParam("market", "GB"),
        SerializedQueryParam("active", "true"),
    ]


@pytest.mark.parametrize("value", [None, {}])
def test_serialize_query_map_omits_absent_maps(value: dict[str, ParamValue] | None) -> None:
    """An absent or empty map contributes no query bindings."""
    assert serialize_query_map(value) == []


def test_serializers_accept_typed_scalar_collections() -> None:
    """Typed collections keep their order and duplicates without widening every member."""
    symbols: list[str] = ["A", "B", "A"]
    filters: dict[str, list[str]] = {"symbol": symbols}
    expected = [
        SerializedQueryParam("symbol", "A"),
        SerializedQueryParam("symbol", "B"),
        SerializedQueryParam("symbol", "A"),
    ]

    assert serialize_query_param(symbols, name="symbol") == expected
    assert serialize_query_param(tuple(symbols), name="symbol") == expected
    assert serialize_query_map(filters) == expected
    assert build_header_params(filters) == {"symbol": "A,B,A"}


def test_serialize_path_param_distinguishes_greedy_and_non_greedy_labels() -> None:
    """Path parameters encode reserved characters, with an option to preserve slashes."""
    value = "folders/2026 report#final"

    assert serialize_path_param(value) == "folders%2F2026%20report%23final"
    assert serialize_path_param(value, greedy=True) == "folders/2026%20report%23final"


@pytest.mark.parametrize("value", [None, ""])
def test_path_labels_require_nonempty_values(value: ParamScalar | None) -> None:
    """Missing labels cannot form a request URI, even when input validation is disabled."""
    with pytest.raises(ValueError, match="path labels"):
        serialize_path_param(cast("ParamScalar", value))


@pytest.mark.parametrize(("cap", "expected"), [(None, 1000), (1, 1), (500, 500), (2000, 1000)])
def test_resolve_page_size_respects_vendor_limit(cap: int | None, expected: int) -> None:
    """SDK-owned page limits are independent of the total collection cap."""
    assert resolve_page_size(cap, 1000) == expected


@pytest.mark.parametrize(("cap", "maximum"), [(0, 1000), (-1, 1000), (None, 0), (1, -1)])
def test_resolve_page_size_rejects_invalid_limits(cap: int | None, maximum: int) -> None:
    """Invalid limits fail before an SDK can issue a request."""
    with pytest.raises(ValueError, match="must be >= 1"):
        resolve_page_size(cap, maximum)
