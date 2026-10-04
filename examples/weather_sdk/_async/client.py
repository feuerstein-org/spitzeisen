"""The OpenWeather client and its endpoint groups."""

from weather_sdk._async.api.current_weather import CurrentWeatherApi
from weather_sdk._async.api.forecast import ForecastApi
from weather_sdk._async.base import BaseWeatherApi


class WeatherApi(BaseWeatherApi):
    """
    OpenWeather client

    You can access the available api endpoints using the available methods like
    `current_weather_api`.

    Args:
        config: Existing configuration for this client surface. Use it to customize
            rate limits, retries, validation policy, or HTTP transport.
        api_key: OpenWeather API key used with default settings when `config` is omitted.
            If both arguments are supplied, the configuration takes precedence.

    """

    @property
    def current_weather_api(self) -> CurrentWeatherApi:
        """Access current conditions by latitude and longitude."""
        return self.api(CurrentWeatherApi)

    @property
    def forecast_api(self) -> ForecastApi:
        """Access five-day forecasts by city name."""
        return self.api(ForecastApi)
