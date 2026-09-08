"""Run the handwritten SDK end to end without making a network request."""

import asyncio

import httpx2
from handwritten_sdk import AsyncMarketDataApi, AsyncMarketDataConfig

from spitzeisen import BearerHeader
from spitzeisen.testing import FakeRouter


async def main() -> None:
    """Fetch typed records through a handwritten endpoint and the shared HTTP pipeline."""
    router = (
        FakeRouter()
        .add(
            "/v3/reference/tickers",
            json={
                "results": [{"ticker": "AAPL", "name": "Apple Inc.", "active": True}],
                "next_url": "https://api.massive.com/v3/reference/tickers?cursor=page-two",
            },
        )
        .add(
            "/v3/reference/tickers",
            json={"results": [{"ticker": "MSFT", "name": "Microsoft Corporation", "active": True}]},
        )
    )
    config = AsyncMarketDataConfig(
        auth=BearerHeader("example-key"),
        http_client=httpx2.AsyncClient(transport=router.mock_transport()),
        owns_http_client=True,
    )

    async with AsyncMarketDataApi(config) as api:
        tickers = await api.reference_api.get_all_tickers(market="stocks", max_results=2)

    for ticker in tickers:
        print(f"{ticker.ticker}: {ticker.name}")
    print(f"first query: {router.requests[0].params}")
    print(f"cursor query: {router.requests[1].params}")


if __name__ == "__main__":
    asyncio.run(main())
