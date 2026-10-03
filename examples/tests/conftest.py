"""Real weather clients built once per test with the harness's HTTP dependency."""

import json
from pathlib import Path

import pytest
from weather_sdk import SyncWeatherApi, SyncWeatherApiConfig, WeatherApi, WeatherApiConfig

from spitzeisen import JsonObject
from spitzeisen.testing import ApiFactory, SyncApiFactory

RESPONSES = Path(__file__).parent.parent / "responses"


@pytest.fixture
def current_weather_response() -> JsonObject:
    """Current Weather Api response"""
    return json.loads((RESPONSES / "current_weather.json").read_text())


@pytest.fixture
def forecast_response() -> JsonObject:
    """Forecast Api response"""
    return json.loads((RESPONSES / "forecast.json").read_text())


@pytest.fixture
async def weather(api_factory: ApiFactory) -> WeatherApi:
    """Supply SDK configuration, the factory handles HTTP setup and cleanup."""
    return await api_factory.create(
        WeatherApi,
        config=WeatherApiConfig(api_key="test-key", max_retries=0),
    )


@pytest.fixture
def sync_weather(sync_api_factory: SyncApiFactory) -> SyncWeatherApi:
    """Supply SDK configuration, the factory handles HTTP setup and cleanup."""
    return sync_api_factory.create(
        SyncWeatherApi,
        config=SyncWeatherApiConfig(api_key="test-key", max_retries=0),
    )
