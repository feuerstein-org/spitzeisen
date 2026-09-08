"""Cursor pagination through the real request pipeline and malformed-response boundaries."""

from collections.abc import Generator
from contextlib import contextmanager

import httpx2
import pytest

from spitzeisen import (
    AsyncSpitzeisenApi,
    AsyncSpitzeisenConfig,
    BearerHeader,
    ResponseShapeError,
    SpitzeisenOperationSpec,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
)
from spitzeisen.pagination import CursorPagination, JsonObject, JsonValue, PaginationStrategy
from spitzeisen.params import SerializedQueryParam
from spitzeisen.testing import FakeRouter

RECORDS = SpitzeisenOperationSpec("/v1/records", pagination=CursorPagination())


@contextmanager
def sync_api(router: FakeRouter) -> Generator[SyncSpitzeisenApi]:
    """Bind a blocking client to the same scripted transport used by async tests."""
    with httpx2.Client(transport=router.mock_transport()) as client:
        yield SyncSpitzeisenApi(SyncSpitzeisenConfig(base_url="https://api.example.com", http_client=client))


async def test_cursor_urls_supply_only_a_token_and_keep_authentication() -> None:
    """An untrusted next URL cannot change destination, filters, or credentials."""
    router = FakeRouter()
    router.add(
        "/v1/records",
        json={
            "results": [{"id": "first"}],
            "next_url": "https://elsewhere.example/other?cursor=a%2Bb%2Fc%3D%252F&api_key=untrusted&active=false",
        },
    ).add("/v1/records", json={"results": [{"id": "second"}], "next_url": None})
    original = [SerializedQueryParam("active", "true"), SerializedQueryParam("limit", "1000")]
    async with httpx2.AsyncClient(transport=router.mock_transport()) as client:
        api = AsyncSpitzeisenApi(
            AsyncSpitzeisenConfig(
                base_url="https://api.example.com",
                auth=BearerHeader("test-key"),
                http_client=client,
            ),
        )
        assert await api.get_records(RECORDS, params=original) == [{"id": "first"}, {"id": "second"}]

    first, second = router.requests
    assert first.params == {"active": "true", "limit": "1000"}
    assert second.params == {"cursor": "a+b/c=%2F"}
    assert second.url.startswith("https://api.example.com/v1/records?")
    assert all(request.headers["Authorization"] == "Bearer test-key" for request in router.requests)
    assert original == [SerializedQueryParam("active", "true"), SerializedQueryParam("limit", "1000")]


def test_cursor_pagination_works_through_the_sync_core() -> None:
    """The blocking surface follows the same strategy without asynchronous state."""
    router = FakeRouter()
    router.add(
        "/v1/records",
        json={"results": [{"id": 1}], "next_url": "/v1/records?cursor=two"},
    ).add("/v1/records", json={"results": [{"id": 2}]})
    with sync_api(router) as api:
        assert api.get_records(RECORDS) == [{"id": 1}, {"id": 2}]
    assert router.requests[1].params == {"cursor": "two"}


async def test_a_record_cap_stops_before_requesting_another_cursor() -> None:
    """The cap counts raw records and avoids the next HTTP request entirely."""
    router = FakeRouter().add(
        "/v1/records",
        json={"results": [{"id": 1}, {"id": 2}], "next_url": "?cursor=two"},
    )
    async with httpx2.AsyncClient(transport=router.mock_transport()) as client:
        api = AsyncSpitzeisenApi(AsyncSpitzeisenConfig(base_url="https://api.example.com", http_client=client))
        assert await api.get_records(RECORDS, max_results=1) == [{"id": 1}]
    assert len(router.requests) == 1


def test_empty_pages_can_still_have_a_continuation() -> None:
    """Cursor completion is defined by the continuation, even for empty pages."""
    router = FakeRouter()
    router.add(
        "/v1/records",
        json={"results": [], "next_url": "?cursor=two"},
    ).add("/v1/records", json={"results": [{"id": 2}]})
    with sync_api(router) as api:
        assert api.get_records(RECORDS) == [{"id": 2}]


def test_direct_tokens_keep_selected_parameters_without_reencoding() -> None:
    """Opaque tokens are unchanged, while repeated retained parameters keep their order."""
    strategy = CursorPagination(
        cursor_param="after",
        next_key="continuation",
        results_key="items",
        cursor_from_url=False,
        retain_params=("limit", "scope", "after"),
    )
    original = [
        SerializedQueryParam("limit", "100"),
        SerializedQueryParam("scope", "one"),
        SerializedQueryParam("scope", "two"),
        SerializedQueryParam("filter", "active"),
        SerializedQueryParam("after", "previous"),
    ]
    first = strategy.first_params(original)
    assert first == original
    assert first is not original
    page: JsonObject = {"items": [{"id": "next"}], "continuation": "a+b%2F /=="}
    assert strategy.records(page) == [{"id": "next"}]
    assert strategy.next_params(page, [], first, 1) == [
        SerializedQueryParam("limit", "100"),
        SerializedQueryParam("scope", "one"),
        SerializedQueryParam("scope", "two"),
        SerializedQueryParam("after", "a+b%2F /=="),
    ]
    assert isinstance(strategy, PaginationStrategy)


@pytest.mark.parametrize("page", [{"results": []}, {"results": [], "next_url": None}])
def test_absent_or_null_continuations_end_the_walk(page: JsonValue) -> None:
    """The two supported completion signals do not ask for another request."""
    assert CursorPagination().next_params(page, [], [], 0) is None


@pytest.mark.parametrize(
    "continuation",
    [
        "",
        7,
        False,
        [],
        {},
        "https://api.example.com/v1/records?other=value",
        "?cursor=",
        "?cursor=one&cursor=two",
        "?cursor=%GG",
        "?cursor=%FF",
        "https://[invalid/?cursor=one",
    ],
)
def test_malformed_url_continuations_raise(continuation: JsonValue) -> None:
    """A broken continuation must never silently truncate the collection."""
    with pytest.raises(ResponseShapeError):
        CursorPagination().next_params({"results": [], "next_url": continuation}, [], [], 0)


@pytest.mark.parametrize("continuation", ["", 7, False, [], {}])
def test_direct_tokens_must_be_nonempty_strings(continuation: JsonValue) -> None:
    """Direct-token APIs share the same explicit empty-token policy."""
    with pytest.raises(ResponseShapeError, match="non-empty string"):
        CursorPagination(cursor_from_url=False).next_params({"next_url": continuation}, [], [], 0)


def test_url_query_tokens_are_decoded_once() -> None:
    """Encoded punctuation and spaces survive the next request's later encoding step."""
    params = CursorPagination().next_params({"next_url": "?cursor=%252F%2B+%20"}, [], [], 0)
    assert params == [SerializedQueryParam("cursor", "%2F+  ")]


@pytest.mark.parametrize("cursor_from_url", [True, False])
def test_current_cursor_cannot_repeat(cursor_from_url: bool) -> None:
    """The strategy catches a stationary cursor without storing state across callers."""
    continuation = "?cursor=same" if cursor_from_url else "same"
    with pytest.raises(ResponseShapeError, match="repeated the current"):
        CursorPagination(cursor_from_url=cursor_from_url).next_params(
            {"next_url": continuation},
            [],
            [SerializedQueryParam("cursor", "same")],
            1,
        )


def test_a_shared_strategy_does_not_remember_another_calls_cursor() -> None:
    """Concurrent operations may independently receive the same continuation token."""
    strategy = CursorPagination()
    page: JsonObject = {"results": [], "next_url": "?cursor=two"}
    expected = [SerializedQueryParam("cursor", "two")]
    assert strategy.next_params(page, [], [], 0) == expected
    assert strategy.next_params(page, [], [], 0) == expected


@pytest.mark.parametrize("field", ["cursor_param", "next_key", "results_key"])
def test_empty_configuration_keys_are_rejected(field: str) -> None:
    """An empty key is a configuration error discovered before any request."""
    with pytest.raises(ValueError, match="must be non-empty"):
        CursorPagination(
            cursor_param="" if field == "cursor_param" else "cursor",
            next_key="" if field == "next_key" else "next_url",
            results_key="" if field == "results_key" else "results",
        )


def test_continuation_requires_an_envelope() -> None:
    """A bare array cannot carry the configured continuation member."""
    with pytest.raises(ResponseShapeError, match="expected an object"):
        CursorPagination().next_params([], [], [], 0)


def test_cursor_records_reject_an_unexpected_envelope() -> None:
    """Response-shape failures remain distinct from a valid empty collection."""
    with pytest.raises(ResponseShapeError, match="expected 'results'"):
        CursorPagination().records({"items": []})
