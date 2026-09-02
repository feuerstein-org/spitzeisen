"""
Public CurrentWeatherResponse response model.

Created once by spitzeisen-gen. Add model-specific validators and behaviour here; regeneration
preserves this file and refreshes only the schema-derived base in `models._generated`.
"""

from pydantic import model_validator

from weather_sdk.models._generated import CurrentWeatherResponse as GeneratedCurrentWeatherResponse


class CurrentWeatherResponse(GeneratedCurrentWeatherResponse):
    """A current weather response returned by the API."""

    @model_validator(mode="after")
    def require_weather_condition(self) -> "CurrentWeatherResponse":
        """Reject payloads that omit the condition collection required by this example API."""
        if not self.weather:
            msg = "current weather must contain at least one weather condition"
            raise ValueError(msg)
        return self
