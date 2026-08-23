"""
Neutrality guards.

spitzeisen exists to serve any REST API, so two things are asserted here: that the generated
weather client in `examples/` works end to end against the fake transport, and that no vendor or
domain vocabulary has crept into the package.
"""

import re
import sys
from inspect import signature
from pathlib import Path

import httpx2
import pytest
from pydantic import ValidationError

from spitzeisen import AsyncSpitzeisenConfig, AuthenticationError, NoLimit, QueryParamAuth
from spitzeisen.testing import FakeRouter

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from weather_sdk import AsyncWeatherApi
from weather_sdk.models import CurrentWeatherResponse, Wind

SRC = Path(__file__).parent.parent / "src" / "spitzeisen"

# Words that would mean the framework had learned about somebody's domain.
FORBIDDEN = re.compile(
    r"\b(massive|eodhd|orats|polygon|ticker|dividend|equit(y|ies)|ohlcv|weather|forecast)s?\b",
    re.IGNORECASE,
)


def weather_payload() -> dict[str, object]:
    """A response matching OpenWeather's official Current Weather example shape."""
    return {
        "coord": {"lon": 13.405, "lat": 52.52},
        "weather": [{"id": 800, "main": "Clear", "description": "clear sky", "icon": "01d"}],
        "base": "stations",
        "main": {
            "temp": 22.4,
            "feels_like": 21.9,
            "temp_min": 20.8,
            "temp_max": 23.1,
            "pressure": 1015,
            "humidity": 64,
            "sea_level": 1015,
            "grnd_level": 1012,
        },
        "visibility": 10000,
        "wind": {"speed": 3.1, "deg": 240, "gust": 5.2},
        "clouds": {"all": 0},
        "dt": 1787227200,
        "sys": {
            "type": 2,
            "id": 2011538,
            "country": "DE",
            "sunrise": 1787197612,
            "sunset": 1787249461,
        },
        "timezone": 7200,
        "id": 2950159,
        "name": "Berlin",
        "cod": 200,
    }


def test_nested_weather_models_have_supported_public_imports() -> None:
    """A type appearing in a public response graph can be named without a private import."""
    weather = CurrentWeatherResponse.model_validate(weather_payload())

    assert weather.wind is not None
    assert isinstance(weather.wind, Wind)

    def wind_speed(wind: Wind) -> float:
        return wind.speed

    assert wind_speed(weather.wind) == 3.1


@pytest.fixture
def weather_api() -> tuple[AsyncWeatherApi, FakeRouter]:
    """The generated example client, wired to a scripted HTTP client."""
    router = FakeRouter()
    config = AsyncSpitzeisenConfig(
        base_url="https://api.openweathermap.org",
        auth=QueryParamAuth("appid", "test-key"),
        http_client=httpx2.AsyncClient(transport=router.mock_transport()),
        limiter=NoLimit(),
    )
    return AsyncWeatherApi(config), router


async def test_weather_client_returns_a_validated_response(
    weather_api: tuple[AsyncWeatherApi, FakeRouter],
) -> None:
    """The real operation reaches generated nested models through the public client."""
    api, router = weather_api
    router.add("/data/2.5/weather", json=weather_payload())

    weather = await api.current_weather_api.get_current_weather(
        latitude=52.52,
        longitude=13.405,
        units="metric",
        language="en",
    )

    assert weather.name == "Berlin"
    assert weather.main.temp == 22.4
    assert weather.weather[0].description == "clear sky"
    assert router.requests[0].params == {
        "lat": "52.52",
        "lon": "13.405",
        "units": "metric",
        "lang": "en",
        "appid": "test-key",
    }


async def test_weather_client_keeps_authentication_out_of_the_method_signature(
    weather_api: tuple[AsyncWeatherApi, FakeRouter],
) -> None:
    """The OpenAPI security scheme keeps appid out because the shared auth strategy owns it."""
    api, router = weather_api
    router.add("/data/2.5/weather", json=weather_payload())

    parameters = signature(api.current_weather_api.get_current_weather).parameters
    assert "appid" not in parameters
    assert "mode" not in parameters
    assert {"latitude", "longitude", "units", "language"} <= parameters.keys()

    await api.current_weather_api.get_current_weather(latitude=52.52, longitude=13.405)

    assert router.requests[0].params["appid"] == "test-key"


async def test_weather_client_maps_the_real_unauthorized_response(
    weather_api: tuple[AsyncWeatherApi, FakeRouter],
) -> None:
    """The spec's 401 response travels through the shared typed-error path."""
    api, router = weather_api
    router.add("/data/2.5/weather", status=401, json={"message": "Invalid API key"})

    with pytest.raises(AuthenticationError, match="Invalid API key"):
        await api.current_weather_api.get_current_weather(latitude=52.52, longitude=13.405)


async def test_public_weather_model_runs_its_custom_validator(
    weather_api: tuple[AsyncWeatherApi, FakeRouter],
) -> None:
    """The endpoint imports the preserved public subclass rather than the generated schema base."""
    api, router = weather_api
    payload = weather_payload()
    payload["weather"] = []
    router.add("/data/2.5/weather", json=payload)

    with pytest.raises(ValidationError, match="current weather must contain at least one weather condition"):
        await api.current_weather_api.get_current_weather(latitude=52.52, longitude=13.405)


async def test_weather_spec_constraints_reach_the_generated_model(
    weather_api: tuple[AsyncWeatherApi, FakeRouter],
) -> None:
    """The transcribed specification's percentage semantics become generated validation."""
    api, router = weather_api
    payload = weather_payload()
    measurements = payload["main"]
    assert isinstance(measurements, dict)
    measurements["humidity"] = 101
    router.add("/data/2.5/weather", json=payload)

    with pytest.raises(ValidationError, match="less than or equal to 100"):
        await api.current_weather_api.get_current_weather(latitude=52.52, longitude=13.405)


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.name))
def test_no_domain_vocabulary_in_package(path: Path) -> None:
    """
    The framework must not name anybody's API or domain.

    Docstrings included: an example mentioning tickers would be a sign the abstraction had
    been shaped around one caller.
    """
    matches = FORBIDDEN.findall(path.read_text())

    assert not matches, f"{path.name} mentions {sorted(set(matches))}"
