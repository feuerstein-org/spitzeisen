"""Small examples of SDK calls, request inspection, validation, and retry scripting."""

import pytest
from pydantic import ValidationError
from pytest_httpx2 import HTTPXMock
from weather_sdk import SyncWeatherApi, WeatherApi

from spitzeisen import JsonObject

# NOTE: Here we test sync and async classes separately, of course you can have each test run against both
# classes at the same time to avoid duplication.


async def test_raw_and_typed_weather(
    weather: WeatherApi, httpx2_mock: HTTPXMock, current_weather_response: JsonObject
) -> None:
    """Two SDK calls consume two responses; both pass through authentication and HTTP."""
    for _ in range(2):
        httpx2_mock.add_response(json=current_weather_response)
    endpoint = weather.current_weather_api

    raw = await endpoint.get_current_weather_raw(latitude=52.52, longitude=13.405, units="metric")
    current = await endpoint.get_current_weather(latitude=52.52, longitude=13.405, units="metric")

    assert raw == current_weather_response
    assert current is not None
    assert current.name == "Berlin"
    request = httpx2_mock.get_requests()[0]
    assert dict(request.url.params) == {"lat": "52.52", "lon": "13.405", "units": "metric", "appid": "test-key"}


def test_sync_forecast(sync_weather: SyncWeatherApi, httpx2_mock: HTTPXMock, forecast_response: JsonObject) -> None:
    """Use the sync fixture for an ordinary blocking call."""
    httpx2_mock.add_response(json=forecast_response)

    forecast = sync_weather.forecast_api.get_forecast("Berlin", units="metric", on_validation_error="raise")

    assert forecast.city.name == "Berlin"


async def test_invalid_units_never_reach_http(weather: WeatherApi, httpx2_mock: HTTPXMock) -> None:
    """No request is made when input validation fails."""
    weather.config.validate_inputs = True
    with pytest.raises(ValidationError, match="literal_error"):
        await weather.current_weather_api.get_current_weather(
            latitude=52.52,
            longitude=13.405,
            units="unknown",  # type: ignore[arg-type]
        )

    assert not httpx2_mock.get_requests()


async def test_response_validation_can_raise_or_skip(
    weather: WeatherApi, httpx2_mock: HTTPXMock, current_weather_response: JsonObject
) -> None:
    """Change a fresh response fixture to demonstrate both model-validation policies."""
    del current_weather_response["name"]
    for _ in range(2):
        httpx2_mock.add_response(json=current_weather_response)
    endpoint = weather.current_weather_api

    assert await endpoint.get_current_weather(latitude=52.52, longitude=13.405, on_validation_error="skip") is None
    with pytest.raises(ValidationError, match="name"):
        await endpoint.get_current_weather(latitude=52.52, longitude=13.405, on_validation_error="raise")


async def test_retry_with_queued_responses(
    weather: WeatherApi, httpx2_mock: HTTPXMock, forecast_response: JsonObject
) -> None:
    """Queue a failure then a success, enabling one retry without a backoff delay."""
    weather.config.max_retries = 1
    weather.config.retry_backoff_base = 0
    weather.config.retry_backoff_floor = 0
    httpx2_mock.add_response(status_code=503)
    httpx2_mock.add_response(json=forecast_response)

    forecast = await weather.forecast_api.get_forecast("Berlin")

    assert forecast is not None
    assert forecast.city.name == "Berlin"
    assert len(httpx2_mock.get_requests()) == 2
