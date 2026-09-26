# Generated from weather_sdk/_async by build_sync.py -- do not edit.
"""
Current weather API endpoint.

Official documentation: https://openweathermap.org/api/current
"""

from spitzeisen import JsonObject, SpitzeisenOperationSpec, ValidationMode, serialize_query_map
from weather_sdk._sync.base import BaseWeatherApi
from weather_sdk.models import CurrentWeather
from weather_sdk.params import UNITS_ADAPTER, Units

_CURRENT_WEATHER = SpitzeisenOperationSpec(path="/data/2.5/weather")


class CurrentWeatherApi(BaseWeatherApi):
    """Fetch current conditions by coordinates as raw fields or a validated model."""

    # This method validates inputs but leaves response model fields untouched.
    def get_current_weather_raw(
        self,
        *,
        latitude: float,
        longitude: float,
        units: Units = "standard",
        language: str | None = None,
    ) -> JsonObject | None:
        """
        Fetch current weather as a raw dictionary, or return None for HTTP 404.

        Docs: https://openweathermap.org/api/current

        See `get_current_weather` for parameters and request errors.
        """
        return self.get_object(
            _CURRENT_WEATHER,
            not_found_ok=True,
            # serialize_query_map will construct the query string to send to the API.
            params=serialize_query_map(
                {
                    # Coordinate types are checked only when config.validate_inputs is True.
                    "lat": self.validate_input(latitude, float),
                    "lon": self.validate_input(longitude, float),
                    # Units are checked independently of validate_inputs.
                    "units": UNITS_ADAPTER.validate_python(units),
                    "lang": language,
                },
            ),
        )

    # This method will do actual Pydantic validation
    def get_current_weather(
        self,
        *,
        latitude: float,
        longitude: float,
        units: Units = "standard",
        language: str | None = None,
        on_validation_error: ValidationMode | None = None,
    ) -> CurrentWeather | None:
        """
        Get current weather conditions, validated into a `CurrentWeather` model.

        Docs: https://openweathermap.org/api/current

        Args:
            latitude: Latitude in decimal degrees, sent as `lat` (e.g. 52.52).
            longitude: Longitude in decimal degrees, sent as `lon` (e.g. 13.41).
            units: "standard" (the default, Kelvin), "metric" (Celsius), or "imperial"
                (Fahrenheit). Also controls the units of other measurements.
            language: OpenWeather language code for condition descriptions, such as "en"
                or "de". None omits `lang` and uses the API's default language.
            on_validation_error: "raise" rejects an invalid response with a Pydantic error,
                "skip" logs it and returns None. None uses the config's setting, which
                defaults to "skip". Input, HTTP, JSON, and response-shape errors still raise,
                except for HTTP 404, which returns None.

        Returns:
            A `CurrentWeather` model, or None for HTTP 404 (no weather data) or a model skipped after
            validation fails.

        Raises:
            pydantic.ValidationError: Invalid units, invalid coordinate types when `validate_inputs`
                is True, or an invalid response when the effective validation mode is "raise".
            ValueError: If the validation mode is unsupported or a successful response contains invalid JSON.
            SpitzeisenError: Invalid response shape, HTTP error, transport error etc.

        """
        raw = self.get_current_weather_raw(
            latitude=latitude,
            longitude=longitude,
            units=units,
            language=language,
        )
        # If our API returns 404 and that's considered "expected" we return None
        if raw is None:
            return None
        # Validate the object using the configured or per-call raise/skip policy.
        return self.validate_record(raw, CurrentWeather, mode=on_validation_error)
