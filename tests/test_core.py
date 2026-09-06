"""
Core request-path behaviour, exercised on both surfaces.

Every test here runs twice: once against the hand-written awaitable core and once against the
`_sync` tree generated from it. That is what makes the generated surface trustworthy — a
mis-transform fails a test rather than shipping.
"""

from __future__ import annotations

import csv
import io
from contextlib import nullcontext
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from spitzeisen import (
    AsyncSpitzeisenApi,
    AsyncSpitzeisenConfig,
    AuthenticationError,
    BearerHeader,
    MaxRetriesExceededError,
    NoLimit,
    NoPagination,
    NotFoundError,
    PageNumber,
    QueryParamAuth,
    ServerError,
    SpitzeisenOperationSpec,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
    TransportError,
    extract_records,
    serialize_query_param,
)
from spitzeisen.params import QueryParams, SerializedQueryParam
from spitzeisen.testing import FakeRouter

if TYPE_CHECKING:
    from spitzeisen.pagination import JsonObject, JsonValue

THINGS = SpitzeisenOperationSpec(path="/v1/things", pagination=PageNumber())
ONE_THING = SpitzeisenOperationSpec(path="/v1/things/{thing_id}", pagination=NoPagination())

SERVER_ERROR = 500
TOO_MANY_REQUESTS = 429


class Thing(BaseModel):
    """A record with nothing domain-specific about it."""

    id: str
    size: int


class AsyncThings(AsyncSpitzeisenApi):
    """Operation class under test (awaitable)."""


class SyncThings(SyncSpitzeisenApi):
    """Operation class under test (blocking)."""


@pytest.fixture(params=["async", "sync"])
def surface(request: pytest.FixtureRequest) -> str:
    """Run each test once per surface."""
    return request.param


class Driver:
    """Calls whichever surface is under test, so test bodies stay surface-agnostic."""

    def __init__(self, surface: str, **config_kwargs: object) -> None:
        """Build a config and operation instance for the surface under test."""
        self.is_async = surface == "async"
        self.router = FakeRouter()
        self.http_client: httpx2.AsyncClient | httpx2.Client
        self.config: AsyncSpitzeisenConfig | SyncSpitzeisenConfig
        self.api: AsyncThings | SyncThings
        # A real httpx2 client over a mocked pipeline: the request path under test is the one
        # that ships, right down to query encoding.
        if self.is_async:
            self.http_client = httpx2.AsyncClient(transport=self.router.mock_transport())
            self.config = AsyncSpitzeisenConfig(
                base_url="https://fake.test",
                http_client=self.http_client,
                owns_http_client=True,
                limiter=NoLimit(),
                retry_backoff_base=0,
                retry_backoff_floor=0,
                **config_kwargs,  # type: ignore[arg-type]
            )
            self.api = AsyncThings(self.config)
        else:
            self.http_client = httpx2.Client(transport=self.router.mock_transport())
            self.config = SyncSpitzeisenConfig(
                base_url="https://fake.test",
                http_client=self.http_client,
                owns_http_client=True,
                limiter=NoLimit(),
                retry_backoff_base=0,
                retry_backoff_floor=0,
                **config_kwargs,  # type: ignore[arg-type]
            )
            self.api = SyncThings(self.config)

    async def call(self, name: str, *args: object, **kwargs: object) -> object:
        """Invoke a core helper, awaiting it only on the awaitable surface."""
        result = getattr(self.api, name)(*args, **kwargs)
        return await result if self.is_async else result

    async def pages(self, spec: SpitzeisenOperationSpec, **kwargs: object) -> list[JsonObject]:
        """Collect all records for `spec`."""
        return await self.call("_get_all_pages", spec, **kwargs)  # type: ignore[return-value]


async def test_request_returns_decoded_body(surface: str) -> None:
    """A 200 response is decoded and handed back untouched."""
    driver = Driver(surface)
    driver.router.add("/v1/things", json={"results": [{"id": "a", "size": 1}]})

    body = await driver.call("_request", THINGS)

    assert body == {"results": [{"id": "a", "size": 1}]}


async def test_handwritten_csv_decoder_shares_auth_retries_and_cost(surface: str) -> None:
    """A non-JSON endpoint uses the same transport policy and decodes only its final response."""
    driver = Driver(surface)
    driver.config.auth = QueryParamAuth("token", "test-token")
    costs: list[float] = []

    class Limiter:
        def __call__(self, cost: float) -> Limiter:
            costs.append(cost)
            return self

        def __enter__(self) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            pass

        async def __aenter__(self) -> None:
            pass

        async def __aexit__(self, *args: object) -> None:
            pass

    driver.config.limiter = Limiter()
    driver.router.add("/quotes/AAPL", status=429).add(
        "/quotes/AAPL",
        content="id,size\na,2\n",
        headers={"Content-Type": "text/csv", "X-Source": "vendor"},
    )
    decoded: list[int] = []

    def decode(response: httpx2.Response) -> list[Thing]:
        decoded.append(response.status_code)
        assert response.headers["x-source"] == "vendor"
        return [Thing.model_validate(row) for row in csv.DictReader(io.StringIO(response.text))]

    result = await driver.call(
        "request",
        SpitzeisenOperationSpec(path="/quotes/{ticker}", cost=2.5),
        decoder=decode,
        path_params={"ticker": "AAPL"},
        headers={"Accept": "text/csv"},
    )
    assert result == [Thing(id="a", size=2)]
    assert decoded == [200]
    assert costs == [2.5, 2.5]
    assert all(request.params == {"token": "test-token"} for request in driver.router.requests)
    assert all(request.headers["accept"] == "text/csv" for request in driver.router.requests)


@pytest.mark.parametrize("retryable", [None, True])
async def test_handwritten_post_retries_only_with_explicit_opt_in(surface: str, retryable: bool | None) -> None:
    """POST bodies are replayed only when the operation explicitly declares that safe."""
    driver = Driver(surface)
    driver.router.add("/scan", status=503).add("/scan", json={"results": []})
    operation = SpitzeisenOperationSpec(path="/scan", method="POST", retryable=retryable)
    with nullcontext() if retryable else pytest.raises(ServerError):
        result = await driver.call(
            "request",
            operation,
            decoder=httpx2.Response.json,
            content=b'{"ticker":"AAPL"}',
            headers={"Content-Type": "application/json"},
        )
        assert result == {"results": []}
    assert len(driver.router.requests) == (2 if retryable else 1)
    assert all(request.method == "POST" for request in driver.router.requests)
    assert all(request.body == b'{"ticker":"AAPL"}' for request in driver.router.requests)


async def test_decoder_failure_is_not_retried(surface: str) -> None:
    """A parsing bug or unexpected payload does not issue another paid API request."""
    driver = Driver(surface)
    driver.router.add("/v1/things", content="bad,csv")

    def fail(response: httpx2.Response) -> None:
        raise ValueError(response.text)

    with pytest.raises(ValueError, match="bad,csv"):
        await driver.call("request", THINGS, decoder=fail)
    assert len(driver.router.requests) == 1


async def test_non_json_error_preserves_status_body_and_headers(surface: str) -> None:
    """Vendor plain-text failures remain HTTP errors and bypass success decoders."""
    driver = Driver(surface)
    driver.router.add(
        "/v1/things",
        status=404,
        content="Indicator or Country are Not Found.",
        headers={"Content-Type": "text/plain", "X-Request-Id": "abc"},
    )

    def unexpected_decoder(_response: httpx2.Response) -> None:
        pytest.fail("error passed to success decoder")

    with pytest.raises(NotFoundError) as caught:
        await driver.call("request", THINGS, decoder=unexpected_decoder)
    assert caught.value.status == 404
    assert caught.value.message == "Indicator or Country are Not Found."
    assert caught.value.body == b"Indicator or Country are Not Found."
    assert caught.value.headers["x-request-id"] == "abc"


async def test_request_rejects_non_replayable_content(surface: str) -> None:
    """Streaming request bodies require a separate design before they can be retried safely."""
    driver = Driver(surface)
    with pytest.raises(TypeError, match="replayable bytes"):
        await driver.call("request", THINGS, decoder=httpx2.Response.json, content=iter([b"a"]))
    assert not driver.router.requests


async def test_auth_and_params_reach_the_wire(surface: str) -> None:
    """Auth strategies apply to headers or params, and query params survive verbatim."""
    driver = Driver(surface)
    driver.config.auth = BearerHeader("secret")
    driver.router.add("/v1/things", json={"results": []})

    await driver.call("_request", THINGS, params=serialize_query_param("3", name="size.gte"))

    recorded = driver.router.requests[0]
    assert recorded.headers["Authorization"] == "Bearer secret"
    # Dotted filter names must not be mangled on the way out.
    assert recorded.params == {"size.gte": "3"}


async def test_client_default_params_preserve_operation_values_and_rfc_encoding(surface: str) -> None:
    """HTTP client defaults must not overwrite generated queries or authentication."""
    driver = Driver(surface)
    driver.http_client.params = {"locale": "en GB", "q": "default"}
    driver.config.auth = QueryParamAuth("token", "secret")
    driver.router.add("/v1/things", json={"results": []})
    await driver.call("_request", THINGS, params=serialize_query_param(["a b", "c+d"], name="q"))
    sent = driver.router.requests[0]
    assert sent.params == {"locale": "en GB", "q": ["a b", "c+d"], "token": "secret"}
    assert "locale=en%20GB" in sent.url
    assert "q=a%20b&q=c%2Bd" in sent.url


async def test_query_param_auth(surface: str) -> None:
    """A key carried in the query string lands in the params, not the headers."""
    driver = Driver(surface)
    driver.config.auth = QueryParamAuth("appid", "k")
    driver.router.add("/v1/things", json={"results": []})

    await driver.call("_request", THINGS)

    assert driver.router.requests[0].params == {"appid": "k"}
    assert "Authorization" not in driver.router.requests[0].headers


async def test_request_headers_reach_the_wire(surface: str) -> None:
    """Generated OpenAPI header params stay attached to every request."""
    driver = Driver(surface)
    driver.router.add("/v1/things", json={"results": []})

    await driver.call(
        "_request",
        THINGS,
        headers={"X-Workspace-ID": "workspace-1"},
    )

    recorded = driver.router.requests[0]
    assert recorded.headers["X-Workspace-ID"] == "workspace-1"


async def test_path_params_are_substituted(surface: str) -> None:
    """Path placeholders are filled from keyword arguments."""
    driver = Driver(surface)
    driver.router.add("/v1/things/abc", json={"id": "abc"})

    await driver.call("_request", ONE_THING, params=None, thing_id="abc")

    assert driver.router.requests[0].url == "https://fake.test/v1/things/abc"


async def test_page_number_pagination_walks_every_page(surface: str) -> None:
    """Pages are requested by number until one comes back with no records."""
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}], [{"id": "b"}], []])

    records = await driver.pages(THINGS)

    assert [record["id"] for record in records] == ["a", "b"]
    # Every request carries the page, the first one included: an API that requires it is served,
    # and the strategy can trust the value it reads back.
    assert [request.params["page"] for request in driver.router.requests] == ["1", "2", "3"]


async def test_page_number_pagination_carries_the_original_params_forward(surface: str) -> None:
    """
    The page number is added to the query, not substituted for it.

    Dropping the caller's params would lose `limit` from the second page onwards, so an
    A generated operation's page-size param must survive every page. Otherwise it would ask
    for a large page once and take the vendor's default afterwards, silently multiplying the
    request count against a limiter this library exists to conserve.
    """
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}], []])

    params = [
        *serialize_query_param("100", name="limit"),
        *serialize_query_param("desc", name="order"),
    ]
    await driver.pages(THINGS, params=params)

    assert driver.router.requests[1].params == {"limit": "100", "order": "desc", "page": "2"}


async def test_pagination_preserves_repeated_query_params(surface: str) -> None:
    """An exploded OpenAPI array stays exploded on every requested page."""
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}], []])
    params = serialize_query_param(["AAPL", "MSFT"], name="symbol", style="form", explode=True)

    await driver.pages(THINGS, params=params)

    assert driver.router.requests[1].params == {"symbol": ["AAPL", "MSFT"], "page": "2"}


async def test_query_values_are_encoded_by_httpx2(surface: str) -> None:
    """Reserved characters remain part of their query value after standard percent encoding."""
    driver = Driver(surface)
    driver.router.add("/v1/things", json={"results": []})
    params = serialize_query_param("https://example.test/a/b#details", name="url")

    await driver.call("_request", THINGS, params=params)

    assert "url=https%3A%2F%2Fexample.test%2Fa%2Fb%23details" in driver.router.requests[0].url
    assert driver.router.requests[0].params["url"] == "https://example.test/a/b#details"


async def test_a_strategy_seeds_the_first_request(surface: str) -> None:
    """
    A vendor that will not serve a collection without its own param gets it up front.

    The core sends whatever `first_params` returns, so a strategy is never stuck hoping the
    API has a sensible default for a param it actually requires.
    """

    class DemandsAToken:
        """A strategy for an API that refuses a page-less request."""

        def first_params(self, params: QueryParams) -> QueryParams:
            return [*params, SerializedQueryParam("scroll", "open")]

        def records(self, page: JsonValue) -> list[JsonObject]:
            return extract_records(page, None)

        def next_params(
            self,
            page: JsonValue,
            records: list[JsonObject],
            params: QueryParams,
            yielded: int,
        ) -> None:
            return None

    driver = Driver(surface)
    driver.router.add("/v1/things", json=[{"id": "a"}])

    await driver.pages(
        SpitzeisenOperationSpec(path="/v1/things", pagination=DemandsAToken()),
        params=serialize_query_param("x", name="q"),
    )

    assert driver.router.requests[0].params == {"q": "x", "scroll": "open"}


async def test_page_number_pagination_starts_where_it_is_told(surface: str) -> None:
    """`start` is a real starting page, asked for explicitly rather than assumed."""
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=PageNumber(start=7))
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}]])

    await driver.pages(spec)

    assert [request.params["page"] for request in driver.router.requests] == ["7", "8"]


async def test_page_number_pagination_strides_by_step(surface: str) -> None:
    """A vendor counting in strides is walked in strides: 100, 200, 300."""
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=PageNumber(start=100, step=100))
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}], [{"id": "b"}], [{"id": "c"}]])

    records = await driver.pages(spec)

    assert [record["id"] for record in records] == ["a", "b", "c"]
    assert [request.params["page"] for request in driver.router.requests] == ["100", "200", "300", "400"]


async def test_a_step_below_one_is_rejected() -> None:
    """A step of zero would re-request the same page forever against a rate-limited API."""
    with pytest.raises(ValueError, match="step must be >= 1, got 0"):
        PageNumber(step=0)


async def test_max_results_stops_pagination_early(surface: str) -> None:
    """The cap halts the walk rather than draining the collection."""
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]])

    records = await driver.pages(THINGS, max_results=1)

    assert [record["id"] for record in records] == ["a"]
    assert len(driver.router.requests) == 1


@pytest.mark.parametrize("max_results", [0, -1])
async def test_non_positive_max_results_is_rejected_before_request(surface: str, max_results: int) -> None:
    """Every operation, generated or hand-written, validates the shared pagination cap."""
    driver = Driver(surface)

    with pytest.raises(ValueError, match=rf"max_results must be >= 1, got {max_results}"):
        await driver.pages(THINGS, max_results=max_results)

    assert driver.router.requests == []


async def test_bare_list_envelope(surface: str) -> None:
    """An API whose body *is* the list needs no envelope key."""
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=NoPagination(results_key=None))
    driver = Driver(surface)
    driver.router.add("/v1/things", json=[{"id": "a"}, {"id": "b"}])

    records = await driver.pages(spec)

    assert len(records) == 2


async def test_retries_then_succeeds(surface: str) -> None:
    """A 429 is retried and the following success is returned."""
    driver = Driver(surface, max_retries=2)
    driver.router.add("/v1/things", status=TOO_MANY_REQUESTS, json={"error": "slow down"})
    driver.router.add("/v1/things", json={"results": [{"id": "a"}]})

    body = await driver.call("_request", THINGS)

    assert body == {"results": [{"id": "a"}]}
    assert len(driver.router.requests) == 2


async def test_persistent_429_raises_max_retries(surface: str) -> None:
    """Exhausted 429 retries surface as MaxRetriesExceededError."""
    driver = Driver(surface, max_retries=1)
    driver.router.add("/v1/things", status=TOO_MANY_REQUESTS, json={"error": "nope"}, repeat=2)

    with pytest.raises(MaxRetriesExceededError) as excinfo:
        await driver.call("_request", THINGS)

    assert excinfo.value.retries == 1


async def test_persistent_5xx_raises_server_error(surface: str) -> None:
    """Exhausted 5xx retries surface as ServerError carrying the server message."""
    driver = Driver(surface, max_retries=1)
    driver.router.add("/v1/things", status=SERVER_ERROR, json={"message": "boom"}, repeat=2)

    with pytest.raises(ServerError) as excinfo:
        await driver.call("_request", THINGS)

    assert excinfo.value.status == SERVER_ERROR
    assert excinfo.value.message == "boom"


async def test_auth_errors_are_not_retried(surface: str) -> None:
    """A 401 is a permanent failure; retrying it would only waste rate-limit budget."""
    driver = Driver(surface, max_retries=3)
    driver.router.add("/v1/things", status=401, json={"error": "bad key"})

    with pytest.raises(AuthenticationError):
        await driver.call("_request", THINGS)

    assert len(driver.router.requests) == 1


async def test_transport_failures_are_retried_then_raised(surface: str) -> None:
    """Transport faults retry, and the last one propagates."""
    driver = Driver(surface, max_retries=1)
    driver.router.add("/v1/things", error=httpx2.ConnectError("connection reset"), repeat=2)

    with pytest.raises(TransportError):
        await driver.call("_request", THINGS)

    assert len(driver.router.requests) == 2


async def test_request_optional_maps_404_to_none(surface: str) -> None:
    """A missing resource is a routine outcome for single-resource lookups."""
    driver = Driver(surface)
    driver.router.add("/v1/things/gone", status=404, json={"error": "not found"})

    assert await driver.call("_request_optional", ONE_THING, params=None, thing_id="gone") is None


async def test_optional_collection_maps_404_to_none_on_both_surfaces(surface: str) -> None:
    """Collection-level absent semantics work identically in async and sync generated clients."""
    driver = Driver(surface)
    driver.router.add("/v1/things", status=404, json={"error": "not found"})

    assert await driver.call("_get_all_pages_optional", THINGS) is None


async def test_request_optional_propagates_other_errors(surface: str) -> None:
    """Only 404 becomes None; anything else still raises."""
    driver = Driver(surface)
    driver.router.add("/v1/things/x", status=401, json={})

    with pytest.raises(AuthenticationError):
        await driver.call("_request_optional", ONE_THING, params=None, thing_id="x")


async def test_not_found_is_raised_by_plain_request(surface: str) -> None:
    """`_request` keeps 404 as an error; only `_request_optional` softens it."""
    driver = Driver(surface)
    driver.router.add("/v1/things/x", status=404, json={})

    with pytest.raises(NotFoundError):
        await driver.call("_request", ONE_THING, params=None, thing_id="x")


async def test_validation_skips_bad_records(surface: str) -> None:
    """In skip mode invalid records are dropped rather than failing the whole call."""
    driver = Driver(surface)
    records: list[JsonObject] = [{"id": "a", "size": 1}, {"id": "b", "size": "huge"}]

    things = driver.api._validate_records(records, Thing, "skip")

    assert [thing.id for thing in things] == ["a"]


async def test_validation_raises_when_asked(surface: str) -> None:
    """In raise mode the first invalid record aborts the call."""
    driver = Driver(surface)
    records: list[JsonObject] = [{"id": "a", "size": "huge"}]

    with pytest.raises(ValidationError):
        driver.api._validate_records(records, Thing, "raise")


async def test_http_client_closes_after_last_holder_exits(surface: str) -> None:
    """The shared client survives nested holders and closes exactly once."""
    driver = Driver(surface)
    driver.router.add("/v1/things", json={"results": []})

    client = driver.http_client

    if driver.is_async:
        api = cast("AsyncThings", driver.api)
        async with api:
            async with api:
                assert not client.is_closed
            assert not client.is_closed
    else:
        sync_api = cast("SyncThings", driver.api)
        with sync_api, sync_api:
            assert not client.is_closed

    assert client.is_closed


async def test_a_caller_supplied_client_is_left_open(surface: str) -> None:
    """
    A client spitzeisen did not build is a client spitzeisen does not close.

    Callers share one connection pool across a process, so tearing theirs down on the way out of
    an `async with` would break every other user of it.
    """
    driver = Driver(surface)
    driver.config.owns_http_client = False

    if driver.is_async:
        api = cast("AsyncThings", driver.api)
        async with api:
            pass
    else:
        with cast("SyncThings", driver.api):
            pass

    assert not driver.http_client.is_closed


async def test_an_unconfigured_client_does_not_limit(surface: str) -> None:
    """
    A config built without a limiter gets `NoLimit`, and requests go straight through.

    spitzeisen knows nothing about this API's allowance, so any number it picked would be one
    nobody chose — either throttling a client entitled to more or, worse, giving false
    assurance while it hammers the vendor. The client states its own limit or accepts none.
    """
    driver = Driver(surface)
    if driver.is_async:
        config: AsyncSpitzeisenConfig | SyncSpitzeisenConfig = AsyncSpitzeisenConfig(
            base_url="https://fake.test",
            http_client=cast("httpx2.AsyncClient", driver.http_client),
        )
        cast("AsyncThings", driver.api).config = config
    else:
        config = SyncSpitzeisenConfig(
            base_url="https://fake.test",
            http_client=cast("httpx2.Client", driver.http_client),
        )
        cast("SyncThings", driver.api).config = config
    driver.router.add("/v1/things", json={"results": []})

    await driver.call("_request", THINGS)

    assert isinstance(config.limiter, NoLimit)
    assert len(driver.router.requests) == 1


async def test_configs_reject_the_other_surfaces_http_client() -> None:
    """A surface mismatch in a caller-supplied HTTP client fails while building the config."""
    async_client = httpx2.AsyncClient()
    sync_client = httpx2.Client()
    try:
        with pytest.raises(ValidationError):
            AsyncSpitzeisenConfig(base_url="https://fake.test", http_client=sync_client)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            SyncSpitzeisenConfig(base_url="https://fake.test", http_client=async_client)  # type: ignore[arg-type]
    finally:
        await async_client.aclose()
        sync_client.close()


class CountingStrategy:
    """A strategy that remembers how often the core asked it to read a page."""

    def __init__(self) -> None:
        """Start with nothing read."""
        self.calls = 0

    def first_params(self, params: QueryParams) -> QueryParams:
        """Nothing extra is needed to fetch the first page."""
        return list(params)

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Read the records, counting the call."""
        self.calls += 1
        return extract_records(page, None)

    def next_params(
        self,
        page: JsonValue,
        records: list[JsonObject],
        params: QueryParams,
        yielded: int,
    ) -> QueryParams | None:
        """Advance while records keep arriving, using what the core already extracted."""
        if not records:
            return None
        current = next((item.value for item in reversed(params) if item.name == "page"), "1")
        return [*(item for item in params if item.name != "page"), SerializedQueryParam("page", str(int(current) + 1))]


async def test_records_is_read_once_per_page(surface: str) -> None:
    """
    The core extracts a page's records once and hands them to `next_params`.

    A client whose envelope needs real work to unpack -- a nested payload, a reshape, a log
    line -- would otherwise do it twice for every page, silently.
    """
    strategy = CountingStrategy()
    spec = SpitzeisenOperationSpec(path="/v1/things", pagination=strategy)
    driver = Driver(surface)
    driver.router.add_pages("/v1/things", [[{"id": "a"}], [{"id": "b"}]], results_key=None)

    records = await driver.pages(spec)

    assert [record["id"] for record in records] == ["a", "b"]
    # Three pages fetched (two full, one empty terminator), three extractions.
    assert len(driver.router.requests) == 3
    assert strategy.calls == 3


class RecordingLimiter:
    """A limiter that remembers what each request drew, for either surface."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.costs: list[float] = []

    def __call__(self, cost: float, /) -> nullcontext[None]:
        """Record what was drawn and admit the request."""
        self.costs.append(cost)
        return nullcontext()


async def test_a_plain_cost_reaches_the_limiter(surface: str) -> None:
    """The common case: one bucket, one number."""
    driver = Driver(surface)
    driver.config.limiter = limiter = RecordingLimiter()
    driver.router.add("/v1/things", json={"results": []})

    await driver.call("_request", SpitzeisenOperationSpec(path="/v1/things", cost=3.0))

    assert limiter.costs == [3.0]
