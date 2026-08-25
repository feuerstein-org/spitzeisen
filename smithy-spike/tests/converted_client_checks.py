"""Exercise a Smithy client generated from a converted OpenAPI 3.0 model."""

# ruff: noqa: D103, S101

import json
from urllib.parse import parse_qs

from converted_vendor_sdk.client import VendorService
from converted_vendor_sdk.config import Config
from converted_vendor_sdk.models import GetStocksV1DividendsInput
from smithy_http.testing import MockHTTPClient


async def test_openapi_converted_operation_executes_end_to_end() -> None:
    transport = MockHTTPClient()
    transport.add_response(
        headers=[("content-type", "application/json")],
        body=json.dumps(
            {
                "request_id": "request-1",
                "results": [],
                "status": "OK",
                "next_url": "https://api.example.test/stocks/v1/dividends?cursor=next",
            }
        ).encode(),
    )
    client = VendorService(
        Config(
            endpoint_uri="https://api.example.test",
            api_key="test-key",
            transport=transport,
        )
    )

    result = await client.get_stocks_v1_dividends(
        GetStocksV1DividendsInput(
            ticker="AAPL",
            tickerany_of="AAPL,MSFT",
            limit=50,
        )
    )

    assert result.body.request_id == "request-1"
    assert result.body.next_url.endswith("cursor=next")
    request = transport.captured_requests[0]
    assert request.method == "GET"
    assert request.destination.path == "/stocks/v1/dividends"
    assert request.fields["Authorization"].values == ["Bearer test-key"]
    assert parse_qs(request.destination.query or "") == {
        "limit": ["50"],
        "ticker": ["AAPL"],
        "ticker.any_of": ["AAPL,MSFT"],
    }


def test_converter_does_not_turn_vendor_pagination_into_a_paginator() -> None:
    client = VendorService(Config(api_key="test-key", transport=MockHTTPClient()))

    assert hasattr(client, "list_tickers")
    assert not hasattr(client, "list_tickers_paginated")
    assert not hasattr(client, "get_list_tickers_paginator")
