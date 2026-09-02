"""Strict decoding of Python-only target configuration."""

from __future__ import annotations

import pytest

from spitzeisen.codegen.python_plan import PythonImport, PythonTypePlan
from spitzeisen.codegen.python_settings_io import (
    PythonSettingsDocumentError,
    python_settings_from_document,
)


def test_python_target_settings_decode_external_symbols_adapters_and_selection() -> None:
    """Implementation imports stay structured and keyed by stable Smithy or adapter IDs."""
    settings = python_settings_from_document(
        {
            "external_models": {
                "example.weather#Forecast": {
                    "module": "weather_models.forecast",
                    "symbol": "Forecast",
                    "dependencies": ["weather-models>=2"],
                },
            },
            "input_adapters": {
                "example.adapters#date-range": {
                    "function": {
                        "module": "weather_sdk.params",
                        "name": "coerce_date_range",
                        "alias": "adapt_date_range",
                    },
                    "public_type": {
                        "kind": "list",
                        "members": [
                            {
                                "kind": "symbol",
                                "module": "weather_sdk.params",
                                "name": "DateRange",
                            },
                        ],
                    },
                    "dependencies": ["date-range-runtime>=1"],
                },
            },
            "protocol_preference": ["spitzeisen.protocols#genericRestJson"],
            "enabled_integrations": ["example-weather"],
        },
        package="weather_sdk",
        client_name="WeatherApi",
        vendor="example weather",
    )

    model = settings.external_models["example.weather#Forecast"]
    adapter = settings.input_adapters["example.adapters#date-range"]
    assert (model.module, model.symbol, model.dependencies) == (
        "weather_models.forecast",
        "Forecast",
        ("weather-models>=2",),
    )
    assert adapter.function == PythonImport(
        "weather_sdk.params",
        "coerce_date_range",
        "adapt_date_range",
    )
    assert adapter.public_type == PythonTypePlan(
        "list",
        members=(PythonTypePlan("symbol", name="DateRange", module="weather_sdk.params"),),
    )
    assert settings.protocol_preference == ("spitzeisen.protocols#genericRestJson",)
    assert settings.enabled_integrations == ("example-weather",)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"surprise": True}, "unknown fields"),
        ({"external_models": {"Forecast": {}}}, "must be a Smithy ShapeId"),
        (
            {
                "input_adapters": {
                    "date-range": {
                        "function": {"module": "weather.params", "name": "adapt"},
                        "public_type": {"kind": "custom", "name": "DateRange"},
                    },
                },
            },
            "must be one of",
        ),
        (
            {"protocol_preference": ["example.protocols#rest", "example.protocols#rest"]},
            "duplicate values",
        ),
    ],
)
def test_python_target_settings_reject_ambiguous_or_unstructured_values(
    document: object,
    message: str,
) -> None:
    """A malformed target config fails at its boundary rather than leaking into templates."""
    with pytest.raises(PythonSettingsDocumentError, match=message):
        python_settings_from_document(
            document,
            package="weather_sdk",
            client_name="WeatherApi",
        )
