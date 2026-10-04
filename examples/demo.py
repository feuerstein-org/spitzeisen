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


# This is to return fake data and not call the actual API - can be ignored
def mock_response(request: httpx2.Request) -> httpx2.Response:
    """Return a saved response for either client's demo request."""
    responses = Path(__file__).parent / "responses"
    filename = {"/data/2.5/weather": "current_weather.json", "/data/2.5/forecast": "forecast.json"}[request.url.path]
    return httpx2.Response(200, json=json.loads((responses / filename).read_text()))


def print_weather(weather: CurrentWeather | None, forecast: Forecast | None) -> None:
    """Display the shared response models returned by both clients."""
    if weather is not None:
        print(f"city: {weather.name}")
        print(f"temperature: {weather.measurements.temperature} °C")
        print(f"condition: {weather.conditions[0].description}")
    if forecast is not None:
        print(f"forecast city: {forecast.city.name}")
        for reading in forecast.readings:
            print(
                f"forecast: {reading.time.isoformat()} - "
                f"{reading.measurements.temperature} °C - {reading.conditions[0].description}"
            )


# Asynchronous example
async def main_async() -> None:
    """Fetch current conditions and forecasts with the async client."""
    config = WeatherApiConfig(
        api_key="example-key",
        on_validation_error="skip",  # If a single record from the API fails validation - skip it
        validate_inputs=True,  # Check inputs before sending request
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(mock_response)),
        owns_http_client=True,  # Spitzeisen owns it and will close it when no longer needed
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
        on_validation_error="skip",  # If a single record from the API fails validation - skip it
        validate_inputs=True,  # Check inputs before sending request
        http_client=httpx2.Client(transport=httpx2.MockTransport(mock_response)),
        owns_http_client=True,  # Spitzeisen owns it and will close it when no longer needed
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
