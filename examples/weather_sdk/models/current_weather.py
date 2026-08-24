"""
Public CurrentWeatherResponse response model.

Created once by spitzeisen-gen. Add model-specific validators and behaviour here; regeneration
preserves this file and refreshes only the schema-derived base in `models._generated`.
"""

from typing import Literal

from pydantic import field_validator

from weather_sdk.models._generated import (
    CurrentWeatherResponse as GeneratedCurrentWeatherResponse,
)
from weather_sdk.models._generated import WeatherCondition


class CurrentWeatherResponse(GeneratedCurrentWeatherResponse):
    """Current-weather observation with SDK-owned invariants."""

    @field_validator("weather")
    @classmethod
    def weather_conditions_present(cls, conditions: list[WeatherCondition]) -> list[WeatherCondition]:
        """Reject a successful observation that contains no weather condition."""
        if not conditions:
            msg = "current weather must contain at least one weather condition"
            raise ValueError(msg)
        return conditions


Test = Literal["one", "two", "three"]
