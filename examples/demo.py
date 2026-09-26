"""Run both weather clients end to end without making a network request."""

import asyncio
import json
from pathlib import Path

import httpx2
from weather_sdk import (
    CurrentWeather,
    Forecast,
    SyncWeatherApi,
    SyncWeatherApiConfig,
    WeatherApi,
    WeatherApiConfig,
)

from spitzeisen.testing import FakeRouter


# This is to return fake data and not call the actual API - can be ignored
def make_router() -> FakeRouter:
    """Script the same responses for either client."""
    responses = Path(__file__).parent / "responses"
    return (
        FakeRouter()
        .add("/data/2.5/weather", json=json.loads((responses / "current_weather.json").read_text()))
        .add("/data/2.5/forecast", json=json.loads((responses / "forecast.json").read_text()))
    )


def print_weather(weather: CurrentWeather | None, forecast: Forecast | None) -> None:
    """Display the shared response models returned by both clients."""
    if weather is not None:
        print(f"city: {weather.name}")
        print(f"temperature: {weather.main.temp} °C")
        print(f"condition: {weather.weather[0].description}")
    if forecast is not None:
        print(f"forecast city: {forecast.city.name}")
        for reading in forecast.readings:
            print(f"forecast: {reading.time.isoformat()} — {reading.main.temp} °C — {reading.weather[0].description}")


# Asynchronous example
async def main_async() -> None:
    """Fetch current conditions and forecasts with the async client."""
    config = WeatherApiConfig(
        api_key="example-key",
        on_validation_error="skip",
        validate_inputs=True,  # Check inputs before sending request
        http_client=httpx2.AsyncClient(transport=make_router().mock_transport()),
        owns_http_client=True,  # Close this supplied client when its last API context exits.
    )

    async with WeatherApi(config) as api:
        weather = await api.current_weather_api.get_current_weather(
            latitude=52.52,
            longitude=13.405,
            units="metric",
            language="en",
        )
        forecast = await api.forecast_api.get_forecast("Berlin", units="metric")

    print_weather(weather, forecast)


# Synchronous example
def main_sync() -> None:
    """Fetch the same models with the blocking client."""
    config = SyncWeatherApiConfig(
        api_key="example-key",
        on_validation_error="skip",
        validate_inputs=True,  # Check inputs before sending request
        http_client=httpx2.Client(transport=make_router().mock_transport()),
        owns_http_client=True,  # Close this supplied client when its last API context exits.
    )

    with SyncWeatherApi(config) as api:
        weather = api.current_weather_api.get_current_weather(
            latitude=52.52,
            longitude=13.405,
            units="metric",
            language="en",
        )
        forecast = api.forecast_api.get_forecast("Berlin", units="metric")

    print_weather(weather, forecast)


if __name__ == "__main__":
    print("Async client")
    asyncio.run(main_async())
    print("Sync client")
    main_sync()
