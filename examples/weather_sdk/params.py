"""
OpenWeather's supported measurement units and their runtime validator.

Pass "standard" for Kelvin temperatures, "metric" for Celsius, or "imperial"
for Fahrenheit. Endpoint methods use `UNITS_ADAPTER` to validate this choice
before sending the request.
"""

from typing import Literal

from pydantic import TypeAdapter

Units = Literal["standard", "metric", "imperial"]

# This can be passed to spitzeisen to validate the input, pydantic models would work as well
UNITS_ADAPTER = TypeAdapter[Units | None](Units | None)
