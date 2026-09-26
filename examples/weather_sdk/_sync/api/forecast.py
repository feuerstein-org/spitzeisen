# Generated from weather_sdk/_async by build_sync.py -- do not edit.
"""
Five-day weather forecast API endpoint.

Official documentation: https://openweathermap.org/api/forecast5
"""

from typing import Literal, overload

from spitzeisen import JsonObject, SpitzeisenOperationSpec, ValidationMode, serialize_query_map
from weather_sdk._sync.base import BaseWeatherApi
from weather_sdk.models import Forecast
from weather_sdk.params import UNITS_ADAPTER, Units

_FORECAST = SpitzeisenOperationSpec(path="/data/2.5/forecast")


class ForecastApi(BaseWeatherApi):
    """Fetch a city's five-day forecast as a raw object or a validated model."""

    # This method does not validate model fields; the return type is a dict.
    def get_forecast_raw(
        self,
        city: str,
        *,
        units: Units = "standard",
        language: str | None = None,
    ) -> JsonObject:
        """
        Fetch the complete forecast response as a raw dictionary.

        Docs: https://openweathermap.org/api/forecast5

        See `get_forecast` for parameters and request errors.
        """
        return self.get_object(
            _FORECAST,
            # serialize_query_map will construct the query string to send to the API.
            params=serialize_query_map(
                {
                    # "q" is what's actually sent to the API, city is the value.
                    "q": city,
                    # You can add your own validation of the input if required.
                    "units": UNITS_ADAPTER.validate_python(units),
                    "lang": language,
                },
            ),
        )

    # Make typing "better" for clients who pass on_validation_error "raise" - they don't need to do a None check
    # obviously we can also always use "raise" and disallow editing that option in the config
    @overload
    def get_forecast(
        self,
        city: str,
        *,
        units: Units = "standard",
        language: str | None = None,
        on_validation_error: Literal["raise"],
    ) -> Forecast: ...

    @overload
    def get_forecast(
        self,
        city: str,
        *,
        units: Units = "standard",
        language: str | None = None,
        on_validation_error: ValidationMode | None = None,
    ) -> Forecast | None: ...

    # This method will do actual Pydantic validation.
    def get_forecast(
        self,
        city: str,
        *,
        units: Units = "standard",
        language: str | None = None,
        on_validation_error: ValidationMode | None = None,
    ) -> Forecast | None:
        """
        Get a city's complete forecast, validated into a `Forecast` model.

        Docs: https://openweathermap.org/api/forecast5

        One response contains three-hour readings over five days and city metadata.

        Args:
            city: City name, optionally followed by a country code, such as "Berlin,DE".
            units: "standard" (the default, Kelvin), "metric" (Celsius), or "imperial"
                (Fahrenheit). Also controls the units of other measurements.
            language: OpenWeather language code for condition descriptions, such as "en"
                or "de". None omits `lang` and uses the API's default language.
            on_validation_error: "raise" rejects an invalid model with a Pydantic error,
                "skip" logs it and returns None. None uses the config's setting, which
                defaults to "skip". Input, HTTP, JSON, and response-shape errors always raise.

        Returns:
            A `Forecast` model with `.readings` and `.city`, or None if validation fails under "skip".

        Raises:
            pydantic.ValidationError: invalid input or API response are (if `on_validation_error` is "raise").
            ValueError: If the validation mode is unsupported or a successful response contains invalid JSON.
            SpitzeisenError: Invalid response shape, HTTP error, transport error etc.

        """
        raw = self.get_forecast_raw(city, units=units, language=language)
        # Validate the full object so readings and their metadata stay together.
        return self.validate_record(raw, Forecast, mode=on_validation_error)
