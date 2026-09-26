"""An OpenWeather SDK example using the shared Spitzeisen runtime."""

from weather_sdk._async.api.current_weather import CurrentWeatherApi
from weather_sdk._async.api.forecast import ForecastApi
from weather_sdk._async.base import BaseWeatherApi
from weather_sdk._async.client import WeatherApi
from weather_sdk._async.config import WeatherApiConfig
from weather_sdk._sync.api.current_weather import CurrentWeatherApi as SyncCurrentWeatherApi
from weather_sdk._sync.api.forecast import ForecastApi as SyncForecastApi
from weather_sdk._sync.base import BaseWeatherApi as SyncBaseWeatherApi
from weather_sdk._sync.client import WeatherApi as SyncWeatherApi
from weather_sdk._sync.config import WeatherApiConfig as SyncWeatherApiConfig
from weather_sdk.models import (
    Clouds,
    Coordinates,
    CurrentWeather,
    CurrentWeatherSystem,
    Forecast,
    ForecastCity,
    ForecastReading,
    Precipitation,
    WeatherCondition,
    WeatherMeasurements,
    Wind,
)
from weather_sdk.params import Units

__all__ = (
    "BaseWeatherApi",
    "Clouds",
    "Coordinates",
    "CurrentWeather",
    "CurrentWeatherApi",
    "CurrentWeatherSystem",
    "Forecast",
    "ForecastApi",
    "ForecastCity",
    "ForecastReading",
    "Precipitation",
    "SyncBaseWeatherApi",
    "SyncCurrentWeatherApi",
    "SyncForecastApi",
    "SyncWeatherApi",
    "SyncWeatherApiConfig",
    "Units",
    "WeatherApi",
    "WeatherApiConfig",
    "WeatherCondition",
    "WeatherMeasurements",
    "Wind",
)
