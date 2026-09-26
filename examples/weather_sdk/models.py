"""
OpenWeather response models shared by the async and sync clients.

Official documentation:
    https://openweathermap.org/api/current
    https://openweathermap.org/api/forecast5
"""

from datetime import datetime

from pydantic import AliasPath, Field

from spitzeisen import SpitzeisenModel


class Coordinates(SpitzeisenModel):
    """
    Geographic location.

    Attributes:
        longitude: Longitude in decimal degrees.
        latitude: Latitude in decimal degrees.

    """

    longitude: float = Field(alias="lon")
    latitude: float = Field(alias="lat")


class WeatherMeasurements(SpitzeisenModel):
    """
    Temperature, pressure, and humidity measurements.

    Temperatures use Kelvin for standard units, Celsius for metric, or Fahrenheit for imperial.

    Attributes:
        temperature: Reported temperature.
        feels_like: Temperature adjusted for human perception.
        minimum_temperature: Minimum temperature across the location at the reading's time.
        maximum_temperature: Maximum temperature across the location at the reading's time.
        pressure: Atmospheric pressure at sea level in hPa.
        humidity: Relative humidity as a percentage.
        sea_level_pressure: Sea-level pressure in hPa, or None when unavailable.
        ground_level_pressure: Ground-level pressure in hPa, or None when unavailable.
        temperature_adjustment: Internal OpenWeather forecast adjustment, or None when absent.

    """

    temperature: float = Field(alias="temp")
    feels_like: float
    minimum_temperature: float = Field(alias="temp_min")
    maximum_temperature: float = Field(alias="temp_max")
    pressure: int
    humidity: int
    sea_level_pressure: int | None = Field(default=None, alias="sea_level")
    ground_level_pressure: int | None = Field(default=None, alias="grnd_level")
    temperature_adjustment: float | None = Field(default=None, alias="temp_kf")


class WeatherCondition(SpitzeisenModel):
    """
    One weather condition, shared by current conditions and forecast readings.

    Attributes:
        id: OpenWeather weather condition identifier.
        group: Condition group, such as Rain, Snow, or Clouds.
        description: Human-readable conditions translated into the request's language.
        icon: OpenWeather icon code, such as "10d".

    """

    id: int
    group: str = Field(alias="main")
    description: str
    icon: str


class Wind(SpitzeisenModel):
    """
    Wind speed, direction, and optional gust.

    Attributes:
        speed: Wind speed in m/s for standard or metric units, or mph for imperial.
        direction: Meteorological wind direction in degrees.
        gust: Gust speed in the same units as `speed`, or None when unavailable.

    """

    speed: float
    direction: int = Field(alias="deg")
    gust: float | None = None


class Clouds(SpitzeisenModel):
    """
    Cloud coverage for the location.

    Attributes:
        coverage: Cloud cover as a percentage.

    """

    coverage: int = Field(alias="all")


class Precipitation(SpitzeisenModel):
    """
    Rain or snow with Python names for the API's numeric fields.

    Missing measurements default to None.

    Attributes:
        one_hour: Current precipitation in mm/h, read from the API's `1h` field.
        three_hours: Forecast precipitation over three hours in mm, read from the API's `3h` field.

    """

    one_hour: float | None = Field(default=None, alias="1h")
    three_hours: float | None = Field(default=None, alias="3h")


class CurrentWeatherSystem(SpitzeisenModel):
    """
    Country and sun times, plus internal OpenWeather metadata.

    All fields default to None when unavailable; Pydantic parses Unix timestamps as UTC datetimes.

    Attributes:
        system_type: Internal OpenWeather system type.
        system_id: Internal OpenWeather system identifier.
        message: Internal OpenWeather message value.
        country_code: Country code, such as "GB" or "JP".
        sunrise: Sunrise time as a UTC datetime.
        sunset: Sunset time as a UTC datetime.

    """

    system_type: int | None = Field(default=None, alias="type")
    system_id: int | None = Field(default=None, alias="id")
    message: float | None = None
    country_code: str | None = Field(default=None, alias="country")
    sunrise: datetime | None = None
    sunset: datetime | None = None


class CurrentWeather(SpitzeisenModel):
    """
    Complete current conditions returned by `get_current_weather` for a location.

    Attributes:
        coordinates: Geographic coordinates of the location.
        conditions: Reported weather conditions and their descriptions.
        source: Internal source identifier supplied by OpenWeather.
        measurements: Temperature, pressure, and humidity measurements.
        visibility: Visibility in meters, or None when unavailable.
        wind: Wind speed, direction, and optional gust.
        clouds: Cloud coverage for the location.
        rain: Rain measurements, or None when absent from the response.
        snow: Snow measurements, or None when absent from the response.
        time: Calculation time as a UTC datetime, parsed from the API's Unix timestamp `dt`.
        system: Country, sun times, and internal OpenWeather metadata.
        utc_offset: Local offset from UTC in seconds.
        city_id: OpenWeather city identifier.
        name: Location name supplied by OpenWeather.
        status_code: Internal status code, returned as an integer or string.

    """

    coordinates: Coordinates = Field(alias="coord")
    conditions: list[WeatherCondition] = Field(alias="weather")
    source: str = Field(alias="base")
    measurements: WeatherMeasurements = Field(alias="main")
    visibility: int | None = None
    wind: Wind
    clouds: Clouds
    rain: Precipitation | None = None
    snow: Precipitation | None = None
    time: datetime = Field(alias="dt")
    system: CurrentWeatherSystem = Field(alias="sys")
    utc_offset: int = Field(alias="timezone")
    city_id: int = Field(alias="id")
    name: str
    status_code: int | str = Field(alias="cod")


class ForecastReading(SpitzeisenModel):
    """
    One complete three-hour entry in a forecast's `readings` list.

    Attributes:
        time: Forecast time as a UTC datetime, parsed from the API's Unix timestamp `dt`.
        measurements: Forecast temperature, pressure, and humidity.
        conditions: Forecast weather conditions and their descriptions.
        clouds: Forecast cloud coverage.
        wind: Forecast wind speed, direction, and optional gust.
        visibility: Average visibility in meters, or None when unavailable.
        precipitation_probability: Precipitation probability from 0 (0%) to 1 (100%).
        rain: Rain measurements for the period, or None when absent from the response.
        snow: Snow measurements for the period, or None when absent from the response.
        part_of_day: Day/night code from `sys.pod`: "d" for day or "n" for night.
        time_text: The same forecast time as `time`, supplied as a UTC text string.

    """

    time: datetime = Field(alias="dt")
    measurements: WeatherMeasurements = Field(alias="main")
    conditions: list[WeatherCondition] = Field(alias="weather")
    clouds: Clouds
    wind: Wind
    visibility: int | None = None
    precipitation_probability: float = Field(alias="pop")
    rain: Precipitation | None = None
    snow: Precipitation | None = None
    part_of_day: str = Field(validation_alias=AliasPath("sys", "pod"))
    time_text: str = Field(alias="dt_txt")


class ForecastCity(SpitzeisenModel):
    """
    Location metadata accompanying the forecast readings.

    Pydantic parses the API's Unix sun timestamps as UTC datetimes.

    Attributes:
        id: OpenWeather city identifier.
        name: City name supplied by OpenWeather.
        coordinates: Geographic coordinates of the city.
        country_code: Country code, such as "GB" or "JP".
        population: City population reported by OpenWeather.
        utc_offset: Local offset from UTC in seconds.
        sunrise: Sunrise time as a UTC datetime, or None when unavailable.
        sunset: Sunset time as a UTC datetime, or None when unavailable.

    """

    id: int
    name: str
    coordinates: Coordinates = Field(alias="coord")
    country_code: str = Field(alias="country")
    population: int
    utc_offset: int = Field(alias="timezone")
    sunrise: datetime | None = None
    sunset: datetime | None = None


class Forecast(SpitzeisenModel):
    """
    Complete forecast response, including readings and city metadata.

    Attributes:
        status_code: Internal OpenWeather status code, returned as an integer or string.
        message: Internal OpenWeather message value.
        reading_count: Number of readings reported by the API.
        readings: Three-hour forecast entries, read from the API's `list` field.
        city: Location metadata accompanying the readings.

    """

    status_code: int | str = Field(alias="cod")
    message: float
    reading_count: int = Field(alias="cnt")
    readings: list[ForecastReading] = Field(alias="list")
    city: ForecastCity
