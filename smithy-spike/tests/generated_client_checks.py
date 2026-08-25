"""Exercise a client generated directly from the representative Smithy model."""

# ruff: noqa: D103, PLR2004, S101

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import pytest
from smithy_http.testing import MockHTTPClient
from smithy_weather_sdk.client import WeatherService
from smithy_weather_sdk.config import Config
from smithy_weather_sdk.models import (
    GetCurrentWeatherInput,
    InvalidLocation,
    ListForecastsInput,
    SubmitObservationInput,
    Units,
)


@pytest.fixture
def transport() -> MockHTTPClient:
    return MockHTTPClient()


@pytest.fixture
def client(transport: MockHTTPClient) -> WeatherService:
    return WeatherService(
        Config(
            endpoint_uri="https://api.example.test",
            api_key="test-key",
            transport=transport,
        )
    )


async def test_get_serialization_auth_and_deserialization(
    client: WeatherService,
    transport: MockHTTPClient,
) -> None:
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

    result = await client.get_current_weather(
        GetCurrentWeatherInput(
            latitude=52.52,
            longitude=13.405,
            units=Units.METRIC,
            language="de",
        )
    )

    assert result.name == "Berlin"
    assert result.main.temp == 21.5
    request = transport.captured_requests[0]
    assert request.method == "GET"
    assert parse_qs(request.destination.query or "") == {
        "appid": ["test-key"],
        "lang": ["de"],
        "lat": ["52.52"],
        "lon": ["13.405"],
        "units": ["metric"],
    }


async def test_post_body_is_generated(
    client: WeatherService,
    transport: MockHTTPClient,
) -> None:
    transport.add_response(
        status=201,
        headers=[("content-type", "application/json")],
        body=b'{"observationId":"observation-1"}',
    )

    result = await client.submit_observation(
        SubmitObservationInput(
            station_id="berlin-1",
            temperature=20.25,
            observed_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
    )

    assert result.observation_id == "observation-1"
    request = transport.captured_requests[0]
    assert request.method == "POST"
    assert json.loads(await request.consume_body_async()) == {
        "stationId": "berlin-1",
        "temperature": 20.25,
        "observedAt": 1787616000.0,
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "smithy-http 0.5.0 APIKeySigner copies destination.password into destination.path "
        "when query API-key auth is applied"
    ),
)
async def test_query_api_key_auth_preserves_the_operation_path(
    client: WeatherService,
    transport: MockHTTPClient,
) -> None:
    transport.add_response(
        headers=[("content-type", "application/json")],
        body=(
            b'{"coord":{"lon":13.405,"lat":52.52},"weather":[],"main":'
            b'{"temp":21.5},"dt":1725000000,"name":"Berlin"}'
        ),
    )

    await client.get_current_weather(
        GetCurrentWeatherInput(latitude=52.52, longitude=13.405)
    )

    assert transport.captured_requests[0].destination.path == "/data/2.5/weather"


async def test_modeled_error_is_raised(
    client: WeatherService,
    transport: MockHTTPClient,
) -> None:
    transport.add_response(
        status=400,
        headers=[
            ("content-type", "application/json"),
            ("x-amzn-errortype", "InvalidLocation"),
        ],
        body=b'{"message":"invalid coordinates"}',
    )

    with pytest.raises(InvalidLocation, match="invalid coordinates"):
        await client.get_current_weather(
            GetCurrentWeatherInput(latitude=999, longitude=999)
        )


def test_current_generator_does_not_enforce_input_constraints() -> None:
    value = ListForecastsInput(latitude=None, longitude=None, page_size=101)

    assert value.latitude is None
    assert value.page_size == 101


def test_current_generator_does_not_emit_a_paginator(client: WeatherService) -> None:
    assert hasattr(client, "list_forecasts")
    assert not hasattr(client, "list_forecasts_paginated")
    assert not hasattr(client, "get_list_forecasts_paginator")
