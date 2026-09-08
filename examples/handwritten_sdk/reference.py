"""Handwritten reference operations over the shared request and validation pipeline."""

import datetime as dt

from handwritten_sdk.models import Ticker, TickerOverview
from handwritten_sdk.params import Market, Order, TickerSort
from spitzeisen import (
    AsyncSpitzeisenApi,
    CursorPagination,
    JsonObject,
    SpitzeisenOperationSpec,
    ValidationMode,
    coerce_choice,
    coerce_date,
    resolve_page_size,
    serialize_path_param,
    serialize_query_map,
)

TICKERS = SpitzeisenOperationSpec(
    path="/v3/reference/tickers",
    pagination=CursorPagination(next_key="next_url", results_key="results", cursor_from_url=True),
)
TICKER_OVERVIEW = SpitzeisenOperationSpec(path="/v3/reference/tickers/{ticker}")
TICKERS_MAX_PAGE_SIZE = 1000


class AsyncReferenceApi(AsyncSpitzeisenApi):
    """Vendor paths, argument names, defaults, and response types are the whole adapter."""

    async def get_all_tickers_raw(
        self,
        *,
        ticker: str | None = None,
        ticker_type: str | None = None,
        market: Market | None = None,
        active: bool = True,
        date: str | dt.date | dt.datetime | None = None,
        search: str | None = None,
        max_results: int | None = None,
        sort: TickerSort = "ticker",
        order: Order = "asc",
    ) -> list[JsonObject]:
        """Fetch raw records across pages; max_results caps records before validation."""
        params = serialize_query_map(
            {
                "ticker": ticker,
                "type": ticker_type,
                "market": coerce_choice(market, Market, "market"),
                "active": active,
                "date": coerce_date(date, "date"),
                "search": search,
                "sort": coerce_choice(sort, TickerSort, "sort"),
                "order": coerce_choice(order, Order, "order"),
                "limit": resolve_page_size(max_results, TICKERS_MAX_PAGE_SIZE),
            },
        )
        return await self.get_records(TICKERS, params=params, max_results=max_results)

    async def get_all_tickers(
        self,
        *,
        ticker: str | None = None,
        ticker_type: str | None = None,
        market: Market | None = None,
        active: bool = True,
        date: str | dt.date | dt.datetime | None = None,
        search: str | None = None,
        max_results: int | None = None,
        sort: TickerSort = "ticker",
        order: Order = "asc",
        on_validation_error: ValidationMode | None = None,
    ) -> list[Ticker]:
        """Validate raw records with the configured raise/skip policy or a per-call override."""
        records = await self.get_all_tickers_raw(
            ticker=ticker,
            ticker_type=ticker_type,
            market=market,
            active=active,
            date=date,
            search=search,
            max_results=max_results,
            sort=sort,
            order=order,
        )
        return self.validate_records(records, Ticker, mode=on_validation_error)

    async def get_ticker_overview(
        self,
        ticker: str,
        *,
        date: str | dt.date | dt.datetime | None = None,
    ) -> TickerOverview | None:
        """Return a validated overview; only HTTP 404 means the ticker was not found."""
        return await self.get_model(
            TICKER_OVERVIEW,
            TickerOverview,
            ticker=serialize_path_param(ticker),
            params=serialize_query_map({"date": coerce_date(date, "date")}),
            result_key="results",
            not_found_ok=True,
        )
