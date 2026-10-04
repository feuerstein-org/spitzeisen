"""
Core request-path behaviour, exercised on both surfaces.

Every test here runs twice: once against the awaitable core and once against the
`_sync` tree generated from it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import nullcontext
from json import JSONDecodeError
from typing import TYPE_CHECKING, assert_type, cast

import httpx2
import pytest
from pydantic import ValidationError
from pytest_httpx2 import HTTPXMock

from spitzeisen import (
    AuthenticationError,
    BearerHeader,
    CursorPagination,
    MaxRetriesExceededError,
    NoLimit,
    NoPagination,
    NotFoundError,
    PageNumber,
    QueryParamAuth,
    ResponseShapeError,
    ServerError,
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenOperationSpec,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
    TransportError,
    serialize_query_param,
)
from spitzeisen.pagination import JsonObject

if TYPE_CHECKING:
    from spitzeisen.pagination import JsonValue
    from spitzeisen.params import QueryParams

THINGS = SpitzeisenOperationSpec(path="/v1/things", pagination=PageNumber())
ONE_THING = SpitzeisenOperationSpec(path="/v1/things/{thing_id}", pagination=NoPagination())

SERVER_ERROR = 500
TOO_MANY_REQUESTS = 429
THINGS_URL = "https://fake.test/v1/things"
# Tests using this matcher assert the encoded query separately on the recorded request.
THINGS_QUERY_URL = re.compile(r"https://fake\.test/v1/things(?:\?.*)?$")


class Driver:
    """Calls whichever surface is under test, so test bodies stay surface-agnostic."""

    def __init__(self, surface: str, mock: HTTPXMock) -> None:
        """Build a config and operation instance for the surface under test."""
        self.is_async = surface == "async"
        self.mock = mock
        self.http_client: httpx2.AsyncClient | httpx2.Client
        self.config: SpitzeisenConfig | SyncSpitzeisenConfig
        self.api: SpitzeisenApi | SyncSpitzeisenApi
        if self.is_async:
            self.http_client = httpx2.AsyncClient()
            self.config = SpitzeisenConfig(
                base_url="https://fake.test",
                http_client=self.http_client,
                owns_http_client=True,
                retry_backoff_base=0,
                retry_backoff_floor=0,
            )
            self.api = SpitzeisenApi(self.config)
        else:
            self.http_client = httpx2.Client()
            self.config = SyncSpitzeisenConfig(
                base_url="https://fake.test",
                http_client=self.http_client,
                owns_http_client=True,
                retry_backoff_base=0,
                retry_backoff_floor=0,
            )
            self.api = SyncSpitzeisenApi(self.config)

    async def call(self, name: str, *args: object, **kwargs: object) -> object:
        """Invoke a core helper, awaiting it only on the awaitable surface."""
        result = getattr(self.api, name)(*args, **kwargs)
        return await result if self.is_async else result

    async def pages(self, spec: SpitzeisenOperationSpec, **kwargs: object) -> list[JsonObject]:
        """Collect all records for `spec`."""
        return await self.call("get_records", spec, **kwargs)  # type: ignore[return-value]


@pytest.fixture(params=["async", "sync"])
async def driver(request: pytest.FixtureRequest, httpx2_mock: HTTPXMock) -> AsyncIterator[Driver]:
    """Supply a fresh SDK on each surface and always close its HTTP client."""
    instance = Driver(request.param, httpx2_mock)
    try:
        yield instance
    finally:
        if isinstance(instance.http_client, httpx2.AsyncClient):
            await instance.http_client.aclose()
        else:
            instance.http_client.close()


async def test_get_json_returns_decoded_body(driver: Driver) -> None:
    """Decode a successful body."""
    driver.mock.add_response(url=THINGS_URL, method="GET", json={"results": [{"id": "a", "size": 1}]})

    assert isinstance(driver.config.limiter, NoLimit)
    assert await driver.call("get_json", THINGS) == {"results": [{"id": "a", "size": 1}]}


@pytest.mark.parametrize("not_found_ok", [False, True])
async def test_get_json_returns_successful_null(driver: Driver, not_found_ok: bool) -> None:
    """JSON null is a valid body regardless of the HTTP 404 policy."""
    driver.mock.add_response(url=THINGS_URL, method="GET", text="null", headers={"Content-Type": "application/json"})

    assert await driver.call("get_json", THINGS, not_found_ok=not_found_ok) is None
    assert len(driver.mock.get_requests()) == 1


class RecordingLimiter:
    """A limiter that remembers what each request drew, for either surface."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.costs: list[float] = []

    def __call__(self, cost: float, /) -> nullcontext[None]:
        """Record what was drawn and admit the request."""
        self.costs.append(cost)
        return nullcontext()


async def test_raw_response_shares_auth_retries_and_cost(driver: Driver) -> None:
    """A non-JSON endpoint returns its buffered response after the usual transport policy."""
    driver.config.auth = QueryParamAuth("token", "test-token")
    driver.config.limiter = limiter = RecordingLimiter()
    driver.mock.add_response(
        url="https://fake.test/quotes/AAPL", match_params={"token": "test-token"}, method="GET", status_code=429
    )
    driver.mock.add_response(
        url="https://fake.test/quotes/AAPL",
        match_params={"token": "test-token"},
        method="GET",
        text="id,size\na,2\n",
        headers={"Content-Type": "text/csv", "X-Source": "vendor"},
    )
    response = await driver.call(
        "request",
        SpitzeisenOperationSpec(path="/quotes/{ticker}", cost=2.5),
        path_params={"ticker": "AAPL"},
        headers={"Accept": "text/csv"},
    )
    assert isinstance(response, httpx2.Response)
    assert response.status_code == 200
    assert response.headers["x-source"] == "vendor"
    assert response.text == "id,size\na,2\n"
    assert response.content == b"id,size\na,2\n"
    assert limiter.costs == [2.5, 2.5]
    assert all(dict(request.url.params) == {"token": "test-token"} for request in driver.mock.get_requests())
    assert all(request.headers["accept"] == "text/csv" for request in driver.mock.get_requests())


@pytest.mark.parametrize("retryable", [None, True])
async def test_post_retries_only_with_explicit_opt_in(driver: Driver, retryable: bool | None) -> None:
    """POST bodies are replayed only when the operation explicitly declares that safe."""
    driver.mock.add_response(url="https://fake.test/scan", method="POST", status_code=503)
    driver.mock.add_response(
        url="https://fake.test/scan", method="POST", json={"results": []}, is_optional=not retryable
    )
    operation = SpitzeisenOperationSpec(path="/scan", method="POST", retryable=retryable)
    with nullcontext() if retryable else pytest.raises(ServerError):
        response = await driver.call(
            "request",
            operation,
            content=b'{"ticker":"AAPL"}',
            headers={"Content-Type": "application/json"},
        )
        assert isinstance(response, httpx2.Response)
        assert response.json() == {"results": []}
    assert len(driver.mock.get_requests()) == (2 if retryable else 1)
    assert all(request.method == "POST" for request in driver.mock.get_requests())
    assert all(request.content == b'{"ticker":"AAPL"}' for request in driver.mock.get_requests())


async def test_json_decode_failure_is_not_retried(driver: Driver) -> None:
    """Malformed JSON does not issue another paid request or become an optional 404."""
    driver.mock.add_response(url=THINGS_URL, method="GET", text="bad,csv")

    with pytest.raises(JSONDecodeError):
        await driver.call("get_json", THINGS, not_found_ok=True)
    assert len(driver.mock.get_requests()) == 1


async def test_non_json_error_preserves_status_body_and_headers(driver: Driver) -> None:
    """Vendor plain-text failures raise typed HTTP errors before returning a response."""
    driver.mock.add_response(
        url=THINGS_URL,
        method="GET",
        status_code=404,
        text="Indicator or Country are Not Found.",
        headers={"Content-Type": "text/plain", "X-Request-Id": "abc"},
    )

    with pytest.raises(NotFoundError) as caught:
        await driver.call("request", THINGS)
    assert caught.value.status == 404
    assert caught.value.message == "Indicator or Country are Not Found."
    assert caught.value.body == b"Indicator or Country are Not Found."
    assert caught.value.headers["x-request-id"] == "abc"


async def test_request_rejects_non_replayable_content(driver: Driver) -> None:
    """Streaming request bodies require a separate design before they can be retried safely."""
    with pytest.raises(TypeError, match="replayable bytes"):
        await driver.call("request", THINGS, content=iter([b"a"]))
    assert not driver.mock.get_requests()


async def test_auth_and_params_reach_the_wire(driver: Driver) -> None:
    """Auth strategies apply to headers or params, and query params survive verbatim."""
    driver.config.auth = BearerHeader("secret")
    driver.mock.add_response(url=THINGS_QUERY_URL, method="GET", json={"results": []})

    await driver.call("get_json", THINGS, params=serialize_query_param("3", name="size.gte"))

    recorded = driver.mock.get_requests()[0]
    assert recorded.headers["Authorization"] == "Bearer secret"
    # Dotted filter names must not be mangled on the way out.
    assert dict(recorded.url.params) == {"size.gte": "3"}


async def test_client_default_params_preserve_operation_values_and_httpx_encoding(driver: Driver) -> None:
    """HTTP client defaults merge with operation values using the client's query encoding."""
    driver.http_client.params = {"locale": "en GB", "q": "default"}
    driver.config.auth = QueryParamAuth("token", "secret")
    driver.mock.add_response(url=THINGS_QUERY_URL, method="GET", json={"results": []})
    await driver.call("get_json", THINGS, params=serialize_query_param(["a b", "c+d"], name="q"))
    sent = driver.mock.get_requests()[0]
    assert sent.url.params == httpx2.QueryParams({"locale": "en GB", "q": ["a b", "c+d"], "token": "secret"})
    assert "locale=en+GB" in str(sent.url)
    assert "q=a+b&q=c%2Bd" in str(sent.url)


@pytest.mark.parametrize(("start", "step"), [(1, 1), (7, 1), (100, 100)])
async def test_page_number_pagination_walks_every_page(driver: Driver, start: int, step: int) -> None:
    """Seed and advance pages, preserving filters and extracting each response once."""

    class CountingPages(PageNumber):
        calls = 0

        def records(self, page: JsonValue) -> list[JsonObject]:
            self.calls += 1
            return super().records(page)

    strategy = CountingPages(start=start, step=step)
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=strategy)
    for index, page in enumerate([[{"id": "a"}], [{"id": "b"}], []]):
        driver.mock.add_response(
            url=THINGS_URL,
            match_params={"limit": "100", "symbol": ["AAPL", "MSFT"], "page": str(start + index * step)},
            method="GET",
            json=page,
        )
    params = [*serialize_query_param("100", name="limit"), *serialize_query_param(["AAPL", "MSFT"], name="symbol")]

    assert await driver.pages(spec, params=params) == [{"id": "a"}, {"id": "b"}]
    assert len(driver.mock.get_requests()) == strategy.calls == 3


async def test_iter_records_yields_lazily_and_raises_on_later_404(driver: Driver) -> None:
    """Deliver records before requesting the next page, propagating a later page's failure."""
    driver.mock.add_response(url=THINGS_URL, match_params={"page": "1"}, method="GET", json=[{"id": "a"}, {"id": "b"}])
    driver.mock.add_response(url=THINGS_URL, match_params={"page": "2"}, method="GET", status_code=404)

    records = driver.api.iter_records(THINGS)

    async def take_record() -> JsonObject:
        """Advance the iterator on either API surface."""
        if isinstance(records, AsyncIterator):
            return await anext(records)
        return next(records)

    assert not driver.mock.get_requests()

    received = [await take_record()]
    assert received == [{"id": "a"}]
    assert len(driver.mock.get_requests()) == 1

    received.append(await take_record())
    assert received == [{"id": "a"}, {"id": "b"}]
    assert len(driver.mock.get_requests()) == 1

    with pytest.raises(NotFoundError):
        await take_record()

    assert received == [{"id": "a"}, {"id": "b"}]
    assert len(driver.mock.get_requests()) == 2


async def test_a_step_below_one_is_rejected() -> None:
    """A step of zero would re-request the same page forever against a rate-limited API."""
    with pytest.raises(ValueError, match="step must be >= 1, got 0"):
        PageNumber(step=0)


@pytest.mark.parametrize("max_results", [1, 3])
async def test_max_results_stops_pagination_early(driver: Driver, max_results: int) -> None:
    """The cap truncates raw records and stops before another request."""
    driver.mock.add_response(url=THINGS_URL, match_params={"page": "1"}, method="GET", json=[{"id": "a"}, {"id": "b"}])
    if max_results == 3:
        driver.mock.add_response(
            url=THINGS_URL, match_params={"page": "2"}, method="GET", json=[{"id": "c"}, {"id": "d"}]
        )

    assert await driver.pages(THINGS, max_results=max_results) == [{"id": "a"}, {"id": "b"}, {"id": "c"}][:max_results]
    assert len(driver.mock.get_requests()) == (1 if max_results == 1 else 2)


@pytest.mark.parametrize("max_results", [0, -1])
async def test_non_positive_max_results_is_rejected_before_request(driver: Driver, max_results: int) -> None:
    """Invalid collection caps fail before HTTP."""
    with pytest.raises(ValueError, match=rf"max_results must be >= 1, got {max_results}"):
        await driver.pages(THINGS, max_results=max_results)
    assert not driver.mock.get_requests()


async def test_bare_list_envelope(driver: Driver) -> None:
    """An API whose body is the list needs no envelope key."""
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=NoPagination(results_key=None))
    driver.mock.add_response(url=THINGS_URL, method="GET", json=[{"id": "a"}, {"id": "b"}])

    records = await driver.pages(spec)

    assert len(records) == 2


async def test_persistent_429_raises_max_retries(driver: Driver) -> None:
    """Exhausted 429 retries surface as MaxRetriesExceededError."""
    driver.config.max_retries = 1
    for _ in range(2):
        driver.mock.add_response(url=THINGS_URL, method="GET", status_code=TOO_MANY_REQUESTS, json={"error": "nope"})

    with pytest.raises(MaxRetriesExceededError) as excinfo:
        await driver.call("get_json", THINGS)

    assert excinfo.value.retries == 1


async def test_persistent_5xx_raises_server_error(driver: Driver) -> None:
    """Exhausted 5xx retries surface as ServerError carrying the server message."""
    driver.config.max_retries = 1
    for _ in range(2):
        driver.mock.add_response(url=THINGS_URL, method="GET", status_code=SERVER_ERROR, json={"message": "boom"})

    with pytest.raises(ServerError) as excinfo:
        await driver.call("get_json", THINGS)

    assert excinfo.value.status == SERVER_ERROR
    assert excinfo.value.message == "boom"


async def test_auth_errors_are_not_retried(driver: Driver) -> None:
    """A 401 is a permanent failure; retrying it would only waste rate-limit budget."""
    driver.config.max_retries = 3
    driver.mock.add_response(url=THINGS_URL, method="GET", status_code=401, json={"error": "bad key"})

    with pytest.raises(AuthenticationError):
        await driver.call("get_json", THINGS)

    assert len(driver.mock.get_requests()) == 1


async def test_transport_failures_are_retried_then_raised(driver: Driver) -> None:
    """Transport faults retry, and the last one propagates."""
    driver.config.max_retries = 1
    for _ in range(2):
        driver.mock.add_exception(httpx2.ConnectError("connection reset"), url=THINGS_URL, method="GET")

    with pytest.raises(TransportError):
        await driver.call("get_json", THINGS)

    assert len(driver.mock.get_requests()) == 2


@pytest.mark.parametrize("not_found_ok", [False, True])
async def test_empty_collection_preserves_result_and_return_types(driver: Driver, not_found_ok: bool) -> None:
    """Empty collections remain lists, with optional return types only for opted-in calls."""
    for _ in range(4):
        driver.mock.add_response(url=THINGS_URL, match_params={"page": "1"}, method="GET", json=[])

    if isinstance(driver.api, SpitzeisenApi):
        assert assert_type(await driver.api.get_records(THINGS), list[JsonObject]) == []
        assert assert_type(await driver.api.get_records(THINGS, not_found_ok=False), list[JsonObject]) == []
        assert assert_type(await driver.api.get_records(THINGS, not_found_ok=True), list[JsonObject] | None) == []
        result = await driver.api.get_records(THINGS, not_found_ok=not_found_ok)
        assert assert_type(result, list[JsonObject] | None) == []
    else:
        assert assert_type(driver.api.get_records(THINGS), list[JsonObject]) == []
        assert assert_type(driver.api.get_records(THINGS, not_found_ok=False), list[JsonObject]) == []
        assert assert_type(driver.api.get_records(THINGS, not_found_ok=True), list[JsonObject] | None) == []
        assert assert_type(driver.api.get_records(THINGS, not_found_ok=not_found_ok), list[JsonObject] | None) == []


@pytest.mark.parametrize("not_found_ok", [False, True])
async def test_collection_rejects_successful_null(driver: Driver, not_found_ok: bool) -> None:
    """The 404 policy does not turn an invalid collection shape into an absent collection."""
    driver.mock.add_response(
        url=THINGS_URL,
        match_params={"page": "1"},
        method="GET",
        text="null",
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(ResponseShapeError):
        await driver.call("get_records", THINGS, not_found_ok=not_found_ok)
    assert len(driver.mock.get_requests()) == 1


@pytest.mark.parametrize(("status", "error_type"), [(401, AuthenticationError), (500, ServerError)])
@pytest.mark.parametrize("method", ["get_json", "get_object", "get_records"])
async def test_not_found_policy_preserves_other_errors(
    driver: Driver, status: int, error_type: type[AuthenticationError | ServerError], method: str
) -> None:
    """Only 404 becomes None - everything else still raises."""
    driver.config.max_retries = 0
    driver.mock.add_response(url="https://fake.test/v1/things/x", method="GET", status_code=status, json={})

    with pytest.raises(error_type):
        await driver.call(method, ONE_THING, not_found_ok=True, params=None, thing_id="x")
    assert len(driver.mock.get_requests()) == 1


async def test_a_caller_supplied_client_is_left_open(driver: Driver) -> None:
    """
    A client spitzeisen did not build is a client spitzeisen does not close.

    Callers share one connection pool across a process, so tearing theirs down on the way out of
    an `async with` would break every other user of it.
    """
    driver.config.owns_http_client = False

    if driver.is_async:
        api = cast("SpitzeisenApi", driver.api)
        async with api:
            pass
    else:
        with cast("SyncSpitzeisenApi", driver.api):
            pass

    assert not driver.http_client.is_closed


async def test_configs_reject_the_other_surfaces_http_client() -> None:
    """A surface mismatch in a caller-supplied HTTP client fails while building the config."""
    async_client = httpx2.AsyncClient()
    sync_client = httpx2.Client()
    try:
        with pytest.raises(ValidationError):
            SpitzeisenConfig(base_url="https://fake.test", http_client=sync_client)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            SyncSpitzeisenConfig(base_url="https://fake.test", http_client=async_client)  # type: ignore[arg-type]
    finally:
        await async_client.aclose()
        sync_client.close()


@pytest.mark.parametrize("result_key", [None, "result"])
async def test_raw_object_preserves_fields_and_forwards_request_options(driver: Driver, result_key: str | None) -> None:
    """Raw objects retain vendor fields and types while sharing the normal request options."""
    payload = {"id": "a", "size": "2", "extra": {"nullable": None}}
    driver.mock.add_response(
        url="https://fake.test/v1/things/a",
        match_params={"lang": "en"},
        method="GET",
        json=payload if result_key is None else {result_key: payload},
    )

    result = await driver.call(
        "get_object",
        ONE_THING,
        result_key=result_key,
        thing_id="a",
        params=serialize_query_param("en", name="lang"),
        headers={"X-Workspace-ID": "workspace-1"},
    )

    assert result == payload
    assert len(driver.mock.get_requests()) == 1
    request = driver.mock.get_requests()[0]
    assert dict(request.url.params) == {"lang": "en"}
    assert request.headers["X-Workspace-ID"] == "workspace-1"


@pytest.mark.parametrize("not_found_ok", [False, True])
async def test_empty_object_preserves_result_and_return_types(driver: Driver, not_found_ok: bool) -> None:
    """An empty object stays a dictionary; only optional 404 handling adds None to its type."""
    for _ in range(4):
        driver.mock.add_response(url=THINGS_URL, method="GET", json={})

    if isinstance(driver.api, SpitzeisenApi):
        assert assert_type(await driver.api.get_object(THINGS), JsonObject) == {}
        assert assert_type(await driver.api.get_object(THINGS, not_found_ok=False), JsonObject) == {}
        assert assert_type(await driver.api.get_object(THINGS, not_found_ok=True), JsonObject | None) == {}
        assert assert_type(await driver.api.get_object(THINGS, not_found_ok=not_found_ok), JsonObject | None) == {}
    else:
        assert assert_type(driver.api.get_object(THINGS), JsonObject) == {}
        assert assert_type(driver.api.get_object(THINGS, not_found_ok=False), JsonObject) == {}
        assert assert_type(driver.api.get_object(THINGS, not_found_ok=True), JsonObject | None) == {}
        assert assert_type(driver.api.get_object(THINGS, not_found_ok=not_found_ok), JsonObject | None) == {}


@pytest.mark.parametrize("payload", [{}, {"result": None}, {"result": []}, {"result": "bad"}, []])
async def test_optional_object_does_not_hide_bad_envelopes(driver: Driver, payload: JsonValue) -> None:
    """Optional lookup semantics apply to HTTP 404, not malformed successful responses."""
    driver.mock.add_response(url="https://fake.test/v1/things/a", method="GET", json=payload)
    with pytest.raises(ResponseShapeError):
        await driver.call("get_object", ONE_THING, result_key="result", not_found_ok=True, thing_id="a")
    assert len(driver.mock.get_requests()) == 1


@pytest.mark.parametrize(
    ("content", "not_found_ok"),
    [("null", False), ("null", True), ("[]", True), ('"text"', True), ("1", True), ("true", True)],
)
async def test_object_lookup_rejects_successful_non_object(driver: Driver, content: str, not_found_ok: bool) -> None:
    """An object lookup rejects null, lists, and scalars even when an HTTP 404 would return None."""
    driver.mock.add_response(
        url="https://fake.test/v1/things/a", method="GET", text=content, headers={"Content-Type": "application/json"}
    )

    with pytest.raises(ResponseShapeError, match="expected a response object"):
        await driver.call("get_object", ONE_THING, not_found_ok=not_found_ok, thing_id="a")
    assert len(driver.mock.get_requests()) == 1


@pytest.mark.parametrize("method", ["get_json", "get_object"])
@pytest.mark.parametrize(
    "kwargs", [{}, {"not_found_ok": False}, {"not_found_ok": True}], ids=["default", "raise", "allow"]
)
async def test_lookup_requires_explicit_not_found_policy(driver: Driver, method: str, kwargs: dict[str, bool]) -> None:
    """An SDK selects 404-as-None per operation instead of weakening the whole transport."""
    driver.mock.add_response(url="https://fake.test/v1/things/gone", method="GET", status_code=404, text="not found")
    with nullcontext() if kwargs.get("not_found_ok") else pytest.raises(NotFoundError):
        assert await driver.call(method, ONE_THING, thing_id="gone", **kwargs) is None
    assert len(driver.mock.get_requests()) == 1


async def test_api_groups_are_cached_by_class_and_share_lifecycle(driver: Driver) -> None:
    """Groups share config and ownership, including nested reentry of the same root."""
    if isinstance(driver.api, SpitzeisenApi):
        child = driver.api.api(SpitzeisenApi)
        assert child is driver.api.api(SpitzeisenApi)
        assert child.config is driver.config
        async with driver.api:
            async with driver.api, child:
                assert not driver.http_client.is_closed
            assert not driver.http_client.is_closed
    else:
        sync_child = driver.api.api(SyncSpitzeisenApi)
        assert sync_child is driver.api.api(SyncSpitzeisenApi)
        assert sync_child.config is driver.config
        with driver.api:
            with driver.api, sync_child:
                assert not driver.http_client.is_closed
            assert not driver.http_client.is_closed
    assert driver.http_client.is_closed
    assert driver.config.http_client is None
    # Re-entering now leaves lazy creation possible rather than reusing a closed client.
    assert not driver.config.owns_http_client


async def test_configured_query_auth_replaces_caller_credentials(driver: Driver) -> None:
    """Every request carries exactly the configured credential even when query params conflict."""
    driver.config.auth = QueryParamAuth("token", "configured")
    driver.mock.add_response(url=THINGS_QUERY_URL, method="GET", json=[])
    await driver.call("get_json", THINGS, params=serialize_query_param(["wrong", "also-wrong"], name="token"))
    assert dict(driver.mock.get_requests()[0].url.params) == {"token": "configured"}
    assert "Authorization" not in driver.mock.get_requests()[0].headers


async def test_pagination_strategy_can_repeat_request_params(driver: Driver) -> None:
    """A custom strategy can request identical parameters until the server signals completion."""

    class RepeatUntilEmpty(NoPagination):
        """Read successive server-side pages using the same session parameter."""

        def next_params(self, page: JsonValue, records: list[JsonObject], params: QueryParams) -> QueryParams | None:
            """Continue with the original parameters until an empty page arrives."""
            return params if records else None

    spec = SpitzeisenOperationSpec("/v1/things", pagination=RepeatUntilEmpty())
    for page in [[{"id": "a"}], [{"id": "b"}], []]:
        driver.mock.add_response(url=THINGS_QUERY_URL, method="GET", json=page)
    records = await driver.call("get_records", spec, params=serialize_query_param("abc", name="session"))
    assert records == [{"id": "a"}, {"id": "b"}]
    assert [dict(request.url.params) for request in driver.mock.get_requests()] == [{"session": "abc"}] * 3


async def test_cursor_pagination_can_be_reused_between_calls(driver: Driver) -> None:
    """One shared operation spec can be called repeatedly with the same filters and cursors."""
    spec = SpitzeisenOperationSpec("/v1/things", pagination=CursorPagination())
    for _ in range(2):
        driver.mock.add_response(url=THINGS_URL, method="GET", json={"results": [], "next_url": "?cursor=next"})
        driver.mock.add_response(
            url=THINGS_URL, match_params={"cursor": "next"}, method="GET", json={"results": [{"id": "a"}]}
        )
    assert await driver.pages(spec) == [{"id": "a"}]
    assert await driver.pages(spec) == [{"id": "a"}]
    assert len(driver.mock.get_requests()) == 4
