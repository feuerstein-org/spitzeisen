"""Record extraction, authentication, and bounded fan-out helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from spitzeisen import (
    BearerHeader,
    HeaderKey,
    QueryParamAuth,
    ResponseShapeError,
    extract_records,
    gather_bounded,
    map_bounded,
)

if TYPE_CHECKING:
    from spitzeisen.pagination import JsonValue


def test_extract_records_treats_a_null_member_as_empty() -> None:
    """A null envelope member means no records, unlike a null response body."""
    assert extract_records({"results": None}, "results") == []


@pytest.mark.parametrize(
    ("page", "results_key", "match"),
    [
        ({"status": "ok", "data": []}, "results", r"got an object with keys \['data', 'status'\]"),
        ({"results": {"id": "a"}}, "results", "expected a list under 'results'"),
        ([{"id": "a"}], "results", "expected an object carrying 'results'"),
        ({"results": [{"id": "a"}]}, None, "expected the response body to be a list"),
        ({}, "results", "expected 'results' in the response envelope"),
        ([{"id": "a"}, 1], None, "expected record 1 in the response body to be an object, got a int"),
        (
            {"results": [{"id": "a"}, "bad"]},
            "results",
            "expected record 1 under 'results' to be an object, got a str",
        ),
    ],
    ids=[
        "wrong-key",
        "single-object",
        "bare-array",
        "envelope-not-list",
        "empty-envelope",
        "non-object-in-bare-array",
        "non-object-in-envelope",
    ],
)
def test_extract_records_refuses_a_shape_it_cannot_read(page: JsonValue, results_key: str | None, match: str) -> None:
    """Malformed responses identify the bad shape instead of silently returning no records."""
    with pytest.raises(ResponseShapeError, match=match):
        extract_records(page, results_key)


def test_header_key_uses_the_configured_name() -> None:
    """APIs that want their own header name are equally well served."""
    headers: dict[str, str] = {}

    HeaderKey("X-API-Key", "abc").apply(headers, {})

    assert headers == {"X-API-Key": "abc"}


@pytest.mark.parametrize(
    "strategy",
    [BearerHeader("s3cret"), HeaderKey("X-API-Key", "s3cret"), QueryParamAuth("key", "s3cret")],
)
def test_credentials_are_not_in_repr(strategy: object) -> None:
    """A config landing in a log or traceback must not leak the key."""
    assert "s3cret" not in repr(strategy)


async def test_gather_bounded_caps_concurrency_and_preserves_order() -> None:
    """Out-of-order completions retain input order while at most two operations run."""
    in_flight = 0
    peak = 0

    async def track(n: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01 if n == 0 else 0)
        in_flight -= 1
        return n

    assert await gather_bounded(2, *(track(n) for n in range(6))) == [0, 1, 2, 3, 4, 5]
    assert peak <= 2


@pytest.mark.parametrize("bad_limit", [0, -1])
async def test_gather_bounded_rejects_a_useless_limit(bad_limit: int) -> None:
    """A limit below one would never run anything."""
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await gather_bounded(bad_limit)


def test_map_bounded_preserves_order_and_rejects_invalid_limits() -> None:
    """The blocking wrapper preserves result order and forwards the worker limit."""
    assert map_bounded(lambda n: n * 2, [1, 2, 3], limit=2) == [2, 4, 6]
    with pytest.raises(ValueError, match="max_workers"):
        map_bounded(str, [1], limit=0)
