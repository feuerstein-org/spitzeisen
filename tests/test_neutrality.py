"""Exercise a handwritten vendor SDK and keep its domain vocabulary out of the runtime."""

import re
import sys
from collections.abc import AsyncIterator
from datetime import date
from inspect import signature
from pathlib import Path

import httpx2
import pytest
from pydantic import ValidationError

from spitzeisen import AuthenticationError, BearerHeader, JsonObject, ResponseShapeError
from spitzeisen.testing import FakeRouter

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from handwritten_sdk import AsyncMarketDataApi, AsyncMarketDataConfig
from handwritten_sdk.models import Ticker, TickerOverview

SRC = Path(__file__).parent.parent / "src" / "spitzeisen"
FORBIDDEN = re.compile(
    r"\b(massive|eodhd|orats|polygon|ticker|dividend|equit(y|ies)|ohlcv|weather|forecast)s?\b",
    re.IGNORECASE,
)


def ticker_payload(ticker: str = "AAPL") -> JsonObject:
    """A representative subset of a vendor reference record."""
    return {"ticker": ticker, "name": "Apple Inc.", "market": "stocks", "active": True}


@pytest.fixture
async def market_api() -> AsyncIterator[tuple[AsyncMarketDataApi, FakeRouter]]:
    """Use the real shared HTTP pipeline with a scripted transport and owned session."""
    router = FakeRouter()
    config = AsyncMarketDataConfig(
        auth=BearerHeader("test-key"),
        http_client=httpx2.AsyncClient(transport=router.mock_transport()),
        owns_http_client=True,
        max_retries=0,
    )
    async with AsyncMarketDataApi(config) as api:
        yield api, router


async def test_handwritten_sdk_shares_cached_groups_and_session(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Groups reuse one configuration and leaving a nested context keeps the root usable."""
    api, router = market_api
    router.add("/v3/reference/tickers", json={"results": [ticker_payload()]})
    client = api.config.http_client
    assert client is not None
    assert api.reference_api is api.reference_api
    assert api.splits_api is api.splits_api
    assert api.reference_api.config is api.splits_api.config is api.config
    async with api.reference_api:
        await api.reference_api.get_all_tickers()
    assert not client.is_closed
    await api.reference_api.get_all_tickers()
    assert len(router.requests) == 2


async def test_handwritten_list_owns_vendor_defaults(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Default sorting, active flag, page size and shared auth reach the wire."""
    api, router = market_api
    router.add("/v3/reference/tickers", json={"results": [ticker_payload()]})

    records = await api.reference_api.get_all_tickers()

    assert isinstance(records[0], Ticker)
    assert records[0].ticker == "AAPL"
    assert router.requests[0].params == {"active": "true", "sort": "ticker", "order": "asc", "limit": "1000"}
    assert router.requests[0].headers["authorization"] == "Bearer test-key"
    assert "api_key" not in signature(api.reference_api.get_all_tickers).parameters


async def test_handwritten_filters_keep_false_and_encode_vendor_names(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Public argument names and dates map to the vendor's exact query bindings."""
    api, router = market_api
    router.add("/v3/reference/tickers", json={"results": []})

    assert (
        await api.reference_api.get_all_tickers_raw(
            ticker_type="CS",
            market="stocks",
            active=False,
            date=date(2026, 8, 31),
            max_results=25,
        )
        == []
    )

    assert router.requests[0].params == {
        "type": "CS",
        "market": "stocks",
        "active": "false",
        "date": "2026-08-31",
        "sort": "ticker",
        "order": "asc",
        "limit": "25",
    }


async def test_handwritten_pagination_keeps_original_endpoint_and_reapplies_auth(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Only the cursor from next_url reaches the configured endpoint, up to the total cap."""
    api, router = market_api
    router.add(
        "/v3/reference/tickers",
        json={
            "results": [ticker_payload()],
            "next_url": "https://other.example/wrong/path?cursor=second%2Bpage&apiKey=stale&market=fx",
        },
    ).add(
        "/v3/reference/tickers",
        json={
            "results": [ticker_payload("MSFT"), ticker_payload("AMZN")],
            "next_url": "https://api.massive.com/v3/reference/tickers?cursor=unused",
        },
    )

    records = await api.reference_api.get_all_tickers_raw(market="stocks", max_results=2)

    assert [record["ticker"] for record in records] == ["AAPL", "MSFT"]
    assert len(router.requests) == 2
    assert router.requests[1].params == {"cursor": "second+page"}
    assert router.requests[1].url.startswith("https://api.massive.com/v3/reference/tickers?")
    assert all(request.headers["authorization"] == "Bearer test-key" for request in router.requests)


@pytest.mark.parametrize("value", [0, -1])
async def test_handwritten_list_rejects_invalid_total_before_http(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
    value: int,
) -> None:
    """The adapter's page-size helper enforces the shared positive-result-limit contract."""
    api, router = market_api
    with pytest.raises(ValueError, match="max_results must be >= 1"):
        await api.reference_api.get_all_tickers(max_results=value)
    assert not router.requests


async def test_handwritten_list_rejects_invalid_vendor_choice_before_http(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """The SDK supplies allowed values, and the shared helper validates them."""
    api, router = market_api
    with pytest.raises(ValueError, match="Invalid market"):
        await api.reference_api.get_all_tickers(market="unknown")  # type: ignore[arg-type]
    assert not router.requests


async def test_handwritten_models_preserve_raw_data_and_apply_custom_validation(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Raw calls preserve unknown fields; typed calls use the SDK validator and shared skip policy."""
    api, router = market_api
    valid = {**ticker_payload(), "new_vendor_field": "preserved"}
    router.add("/v3/reference/tickers", json={"results": [valid, ticker_payload(" ")]})

    raw = await api.reference_api.get_all_tickers_raw()
    assert raw[0]["new_vendor_field"] == "preserved"
    assert raw[1]["ticker"] == " "
    records = await api.reference_api.get_all_tickers()
    assert len(records) == 1
    assert "new_vendor_field" not in records[0].model_dump()
    with pytest.raises(ValidationError, match="ticker must not be blank"):
        await api.reference_api.get_all_tickers(on_validation_error="raise")
    assert api.config.on_validation_error == "skip"


async def test_handwritten_overview_applies_nested_aliases_and_date_parsing(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """Single-model extraction honors handwritten Pydantic aliases and parsed dates."""
    api, router = market_api
    router.add(
        "/v3/reference/tickers/BRK.B",
        json={"results": {**ticker_payload("BRK.B"), "address": {"city": "Omaha"}, "list_date": "1980-03-17"}},
    )

    overview = await api.reference_api.get_ticker_overview("BRK.B", date="2026-08-31")

    assert isinstance(overview, TickerOverview)
    assert overview.city == "Omaha"
    assert overview.list_date == date(1980, 3, 17)
    assert router.requests[0].params == {"date": "2026-08-31"}


async def test_handwritten_overview_quotes_path_labels(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """A label containing URL syntax remains one encoded path label."""
    api, router = market_api
    router.add("/v3/reference/tickers/A%2FB%3F", json={"results": ticker_payload("A/B?")})
    overview = await api.reference_api.get_ticker_overview("A/B?")
    assert overview is not None
    assert overview.ticker == "A/B?"
    assert router.requests[0].params == {}


async def test_handwritten_overview_maps_only_not_found_to_none(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """An opted-in 404 is optional; authentication failures remain typed exceptions."""
    api, router = market_api
    router.add("/v3/reference/tickers/MISSING", status=404, json={"message": "Not found"})
    router.add("/v3/reference/tickers/AAPL", status=401, json={"message": "Invalid API key"})

    assert await api.reference_api.get_ticker_overview("MISSING") is None
    with pytest.raises(AuthenticationError, match="Invalid API key"):
        await api.reference_api.get_ticker_overview("AAPL")


async def test_handwritten_overview_rejects_malformed_success(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """A missing envelope and a bad model must not masquerade as absent records."""
    api, router = market_api
    router.add("/v3/reference/tickers/AAPL", json={"status": "OK"}).add(
        "/v3/reference/tickers/AAPL",
        json={"results": ticker_payload("")},
    )
    with pytest.raises(ResponseShapeError):
        await api.reference_api.get_ticker_overview("AAPL")
    with pytest.raises(ValidationError, match="ticker must not be blank"):
        await api.reference_api.get_ticker_overview("AAPL")


async def test_handwritten_splits_serialize_vendor_filter_conventions(
    market_api: tuple[AsyncMarketDataApi, FakeRouter],
) -> None:
    """A second group composes the same runtime helpers for another endpoint's conventions."""
    api, router = market_api
    router.add(
        "/stocks/v1/splits",
        json={
            "results": [
                {
                    "ticker": "AAPL",
                    "execution_date": "2020-08-31",
                    "adjustment_type": "forward_split",
                    "split_from": 1,
                    "split_to": 4,
                }
            ]
        },
    )

    splits = await api.splits_api.get_splits(
        ticker="AAPL",
        execution_date_gte=date(2020, 1, 1),
        adjustment_types=["forward_split", "reverse_split"],
        max_results=6000,
    )

    assert splits[0].execution_date == date(2020, 8, 31)
    assert splits[0].split_to == 4
    assert router.requests[0].params == {
        "ticker": "AAPL",
        "execution_date.gte": "2020-01-01",
        "sort": "execution_date.desc",
        "adjustment_type.any_of": "forward_split,reverse_split",
        "limit": "5000",
    }


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.name))
def test_no_domain_vocabulary_in_package(path: Path) -> None:
    """Domain terms, including in runtime docstrings, belong only in downstream SDKs."""
    matches = FORBIDDEN.findall(path.read_text())
    assert not matches, f"{path.name} mentions {sorted(set(matches))}"
