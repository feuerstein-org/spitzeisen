"""Current Weather API."""

from weather_sdk._sync._generated.current_weather import SyncCurrentWeatherApiBase

# This file is created once by spitzeisen-gen and is safe to customize.


class SyncCurrentWeatherApi(SyncCurrentWeatherApiBase):
    """Returns the current weather observation for one latitude and longitude."""
