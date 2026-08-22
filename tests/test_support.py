"""Auth strategies, limiter construction, fan-out helpers and the mock factory."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from spitzeisen import (
    AsyncSpitzeisenApi,
    BearerHeader,
    HeaderKey,
    NoAuth,
    NoPagination,
    QueryParamAuth,
    ResponseShapeError,
    SpitzeisenEndpointSpec,
    async_single_bucket,
    extract_records,
    gather_bounded,
    map_bounded,
    serialize_query_param,
    sync_single_bucket,
)
from spitzeisen.limits import REFILL_INTERVAL_SECONDS
from spitzeisen.testing import MockApiFactory

if TYPE_CHECKING:
    from spitzeisen.pagination import JsonObject, JsonValue

RECORDS = SpitzeisenEndpointSpec(path="/v1/records", pagination=NoPagination("results"))


@pytest.mark.parametrize(
    ("page", "results_key", "expected"),
    [
        ({"results": [{"id": "a"}]}, "results", [{"id": "a"}]),
        ({"results": []}, "results", []),
        # An explicit null is a vendor writing "nothing matched" the sloppy way.
        ({"results": None}, "results", []),
        ([{"id": "a"}], None, [{"id": "a"}]),
        ([], None, []),
    ],
    ids=["records", "empty-list", "explicit-null", "bare-array", "bare-empty-array"],
)
def test_extract_records_reads_every_shape_that_means_something(
    page: JsonValue,
    results_key: str | None,
    expected: list[JsonObject],
) -> None:
    """The readings under which "no records" is a truthful answer."""
    assert extract_records(page, results_key) == expected


@pytest.mark.parametrize(
    ("page", "results_key", "match"),
    [
        ({"data": [{"id": "a"}]}, "results", "expected 'results' in the response envelope"),
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
def test_extract_records_refuses_a_shape_it_cannot_read(
    page: JsonValue,
    results_key: str | None,
    match: str,
) -> None:
    """
    Every one of these would otherwise return `[]`, indistinguishable from "nothing matched".

    A `results_key` that does not match the envelope is the common case, and silence there means
    every call returns nothing until somebody notices. The single-object case is worse still: a
    record actually arrived and would have been dropped.
    """
    with pytest.raises(ResponseShapeError, match=match):
        extract_records(page, results_key)


def test_no_auth_changes_nothing() -> None:
    """An open API needs no strategy and gets no headers."""
    headers: dict[str, str] = {}
    params: dict[str, str] = {}

    NoAuth().apply(headers, params)

    assert (headers, params) == ({}, {})


def test_bearer_header() -> None:
    """The most common scheme."""
    headers: dict[str, str] = {}

    BearerHeader("t0ken").apply(headers, {})

    assert headers == {"Authorization": "Bearer t0ken"}


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


def test_shape_errors_name_the_keys_that_did_arrive() -> None:
    """The message has to be enough to fix the manifest without reading a packet capture."""
    with pytest.raises(ResponseShapeError, match=r"got an object with keys \['data', 'status'\]"):
        extract_records({"status": "ok", "data": []}, "results")


def test_single_bucket_spreads_the_allowance_smoothly() -> None:
    """
    A period's allowance becomes capacity, refilled in small increments.

    Refilling little and often is what keeps the outgoing rate even instead of letting a
    burst spend the whole period at once.
    """
    bucket = async_single_bucket("test", 100, 1)

    assert bucket.capacity == 100
    assert bucket.refill_frequency == REFILL_INTERVAL_SECONDS
    assert bucket.refill_amount == pytest.approx(10.0)


def test_single_bucket_handles_a_slow_tier() -> None:
    """Five calls a minute is a valid, sub-one-per-second bucket."""
    bucket = async_single_bucket("test", 5, 60)

    assert bucket.capacity == 5
    assert bucket.refill_amount == pytest.approx(5 / 60 * REFILL_INTERVAL_SECONDS)


def test_single_bucket_builds_both_surfaces() -> None:
    """
    The two factories must configure their buckets identically.

    They are written out separately rather than sharing a helper, so this is what stops the
    blocking surface from quietly metering at a different rate than the awaitable one.
    """
    async_bucket = async_single_bucket("n", 10, 1, max_sleep=7)
    sync_bucket = sync_single_bucket("n", 10, 1, max_sleep=7)

    fields = ("capacity", "refill_frequency", "refill_amount", "max_sleep", "expiry", "name")
    assert [getattr(async_bucket, f) for f in fields] == [getattr(sync_bucket, f) for f in fields]
    assert async_bucket.refill_amount == pytest.approx(10 / 1 * REFILL_INTERVAL_SECONDS)


async def test_gather_bounded_preserves_order() -> None:
    """Results come back in input order regardless of completion order."""

    async def value(n: int) -> int:
        await asyncio.sleep(0.01 if n == 0 else 0)
        return n

    assert await gather_bounded(2, *(value(n) for n in range(4))) == [0, 1, 2, 3]


async def test_gather_bounded_caps_concurrency() -> None:
    """No more than `limit` coroutines are ever in flight."""
    in_flight = 0
    peak = 0

    async def track() -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    await gather_bounded(2, *(track() for _ in range(6)))

    assert peak <= 2


@pytest.mark.parametrize("bad_limit", [0, -1])
async def test_gather_bounded_rejects_a_useless_limit(bad_limit: int) -> None:
    """A limit below one would never run anything."""
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await gather_bounded(bad_limit)


def test_map_bounded_preserves_order() -> None:
    """The blocking fan-out matches its awaitable counterpart's contract."""
    assert map_bounded(lambda n: n * 2, [1, 2, 3], limit=2) == [2, 4, 6]


def test_map_bounded_rejects_a_useless_limit() -> None:
    """The underlying thread pool rejects a non-positive worker count."""
    with pytest.raises(ValueError, match="max_workers must be greater than 0"):
        map_bounded(str, [1], limit=0)


class Records(AsyncSpitzeisenApi):
    """An endpoint class whose logic can be tested without any session."""

    async def list_records(self, *, active: bool = True) -> list[JsonObject]:
        """Return records, passing the filter down to the request path."""
        return await self._get_all_pages(
            RECORDS,
            serialize_query_param(str(active).lower(), name="active"),
        )


async def test_mock_api_factory_stubs_the_request_path(mocker: object) -> None:
    """Endpoint logic is testable without a session, a limiter or a URL."""
    factory = MockApiFactory(mocker)
    api, mocks = factory.create(Records, pages=[{"id": "a"}])

    records = await api.list_records(active=False)

    assert records == [{"id": "a"}]
    assert mocks.get_all_pages.call_args.args[1] == serialize_query_param("false", name="active")
