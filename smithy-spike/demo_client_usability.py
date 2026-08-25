"""
Runnable consumer demo for the client emitted by smithy-python 0.5.0.

Run this from the generated package after following README.md::

    cd smithy-spike/build/smithy/client/python-client-codegen
    .venv/bin/python ../../../../demo_client_usability.py

The mock transport keeps the demo deterministic. Replacing it with the default transport gives
the same public client surface for real HTTP calls.
"""

# ruff: noqa: INP001, T201
# This demo runs in the generated package's isolated environment, not Spitzeisen's root venv.
# pyright: reportMissingImports=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false

import asyncio
import json

from smithy_http.testing import MockHTTPClient
from smithy_weather_sdk.client import WeatherService
from smithy_weather_sdk.config import Config
from smithy_weather_sdk.models import (
    Forecast,
    GetCurrentWeatherInput,
    InvalidLocation,
    ListForecastsInput,
    Units,
)


async def list_all_forecasts(
    client: WeatherService,
    *,
    latitude: float,
    longitude: float,
) -> list[Forecast]:
    """Implement the paginator that the current generator does not emit."""
    items: list[Forecast] = []
    next_token: str | None = None

    while True:
        page = await client.list_forecasts(
            ListForecastsInput(
                latitude=latitude,
                longitude=longitude,
                next_token=next_token,
                page_size=100,
            )
        )
        items.extend(page.items)
        if page.next_token is None:
            return items
        next_token = page.next_token


def scripted_transport() -> MockHTTPClient:
    """Return responses for one weather call followed by two forecast pages."""
    transport = MockHTTPClient()
    transport.add_response(
        headers=[("content-type", "application/json")],
        body=json.dumps(
            {
                "coord": {"lon": 13.405, "lat": 52.52},
                "weather": [{"id": 800, "main": "Clear", "description": "clear sky"}],
                "main": {"temp": 21.5, "humidity": 55},
                "dt": 1_725_000_000,
                "name": "Berlin",
            }
        ).encode(),
    )
    transport.add_response(
        headers=[("content-type", "application/json")],
        body=b'{"nextToken":"page-2","items":[{"at":1725000000,"measurements":{"temp":20}}]}',
    )
    transport.add_response(
        headers=[("content-type", "application/json")],
        body=b'{"items":[{"at":1725003600,"measurements":{"temp":21}}]}',
    )
    return transport


async def main() -> None:
    """Show the complete generated-client call site."""
    config = Config(
        endpoint_uri="https://api.example.test",
        api_key="demo-key",
        transport=scripted_transport(),
    )

    async with WeatherService(config) as client:
        try:
            weather = await client.get_current_weather(
                GetCurrentWeatherInput(
                    latitude=52.52,
                    longitude=13.405,
                    units=Units.METRIC,
                    language="en",
                )
            )
        except InvalidLocation as error:
            print(f"Invalid location: {error}")
        else:
            print(f"{weather.name}: {weather.main.temp} C")

        forecasts = await list_all_forecasts(
            client,
            latitude=52.52,
            longitude=13.405,
        )
        print(f"Received {len(forecasts)} forecast records")


if __name__ == "__main__":
    asyncio.run(main())
