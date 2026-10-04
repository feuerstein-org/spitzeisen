"""Cursor pagination through the real request pipeline and malformed-response boundaries."""

import httpx2
import pytest
from pytest_httpx2 import HTTPXMock

from spitzeisen import (
    BearerHeader,
    ResponseShapeError,
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenOperationSpec,
)
from spitzeisen.pagination import CursorPagination, JsonObject, JsonValue, PaginationStrategy
from spitzeisen.params import SerializedQueryParam

RECORDS = SpitzeisenOperationSpec("/v1/records", pagination=CursorPagination())


async def test_cursor_urls_supply_only_a_token_and_keep_authentication(httpx2_mock: HTTPXMock) -> None:
    """An untrusted next URL cannot change destination, filters, or credentials."""
    httpx2_mock.add_response(
        url="https://api.example.com/v1/records",
        match_params={"active": "true", "limit": "1000"},
        json={
            "results": [{"id": "first"}],
            "next_url": "https://elsewhere.example/other?cursor=a%2Bb%2Fc%3D%252F+%20&api_key=untrusted&active=false",
        },
    )
    httpx2_mock.add_response(
        url="https://api.example.com/v1/records",
        match_params={"cursor": "a+b/c=%2F  "},
        json={"results": [{"id": "second"}], "next_url": None},
    )
    original = [SerializedQueryParam("active", "true"), SerializedQueryParam("limit", "1000")]
    async with httpx2.AsyncClient() as client:
        api = SpitzeisenApi(
            SpitzeisenConfig(
                base_url="https://api.example.com",
                auth=BearerHeader("test-key"),
                http_client=client,
            ),
        )
        assert await api.get_records(RECORDS, params=original) == [{"id": "first"}, {"id": "second"}]

    assert all(request.headers["Authorization"] == "Bearer test-key" for request in httpx2_mock.get_requests())
    assert original == [SerializedQueryParam("active", "true"), SerializedQueryParam("limit", "1000")]


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
    assert strategy.next_params(page, [], first) == [
        SerializedQueryParam("limit", "100"),
        SerializedQueryParam("scope", "one"),
        SerializedQueryParam("scope", "two"),
        SerializedQueryParam("after", "a+b%2F /=="),
    ]
    assert isinstance(strategy, PaginationStrategy)


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
        CursorPagination().next_params({"results": [], "next_url": continuation}, [], [])


@pytest.mark.parametrize("cursor_from_url", [True, False])
def test_current_cursor_cannot_repeat(cursor_from_url: bool) -> None:
    """The strategy catches a stationary cursor without storing state across callers."""
    continuation = "?cursor=same" if cursor_from_url else "same"
    with pytest.raises(ResponseShapeError, match="repeated the current"):
        CursorPagination(cursor_from_url=cursor_from_url).next_params(
            {"next_url": continuation},
            [],
            [SerializedQueryParam("cursor", "same")],
        )


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
        CursorPagination().next_params([], [], [])
