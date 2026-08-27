"""Spitzeisen-specific Smithy trait decoding."""

import pytest

from spitzeisen.codegen.traits import JSON_NAME, MODEL_PROPERTY, model_customizations


def test_model_properties_configure_the_pydantic_backend() -> None:
    """Model aliases use wire names while type overrides retain their shape path."""
    model = {
        "shapes": {
            "vendor.api#Observation": {
                "type": "structure",
                "members": {
                    "vendorCode": {
                        "target": "smithy.api#String",
                        "traits": {
                            JSON_NAME: "vendor_code",
                            MODEL_PROPERTY: {"name": "code", "type": "str | None"},
                        },
                    },
                },
            },
        },
    }

    customizations = model_customizations(model)

    assert customizations.aliases == {"Observation.vendor_code": "code"}
    assert customizations.type_overrides == {"Observation.vendor_code": "str | None"}


def test_model_property_must_be_structured() -> None:
    """Malformed trait values fail rather than silently losing customization."""
    model = {
        "shapes": {
            "vendor.api#Observation": {
                "type": "structure",
                "members": {
                    "code": {
                        "target": "smithy.api#String",
                        "traits": {MODEL_PROPERTY: "code"},
                    },
                },
            },
        },
    }

    with pytest.raises(TypeError, match="must be an object"):
        model_customizations(model)
