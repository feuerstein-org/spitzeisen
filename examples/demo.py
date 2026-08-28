"""Run the generated example SDK end to end without making a network request."""

import asyncio

from weather_sdk import AsyncWeatherApi

from spitzeisen import AsyncSpitzeisenConfig, NoLimit, QueryParamAuth
from spitzeisen.testing import FakeRouter


async def main() -> None:
    """Fetch a generated response model through the public aggregate client."""
    router = FakeRouter().add(
        "/data/2.5/weather",
        json={
            "coord": {"lon": 13.405, "lat": 52.52},
            "weather": [
                {
                    "id": 800,
                    "main": "Clear",
                    "description": "clear sky",
                    "icon": "01d",
                },
            ],
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
        },
    )
    config = AsyncSpitzeisenConfig(
        base_url="https://api.openweathermap.org",
        auth=QueryParamAuth("appid", "example-key"),
        # http_client=httpx2.AsyncClient(transport=router.mock_transport()),
        # TODO: Why does this need to be defined, should be set as a default, maybe re-export AsyncSpitzeisenConfig
        # as AsyncWeatherSdkConfig or smth like that?
        limiter=NoLimit(),
    )

    async with AsyncWeatherApi(config) as api:
        weather = await api.current_weather_api.get_current_weather(
            latitude=55.52,
            longitude=10.405,
            units="metric",
            language="en",
        )

    if weather is None:
        print("weather station not found")
        return
    print(f"city: {weather.name}")
    print(f"temperature: {weather.main.temp}")
    print(f"condition: {weather.weather[0].description}")
    # print(f"wire query: {router.requests[0].params}")


if __name__ == "__main__":
    asyncio.run(main())
