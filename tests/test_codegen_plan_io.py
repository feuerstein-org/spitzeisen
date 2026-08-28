"""Tests for the versioned Smithy-frontend rendering contract."""

import pytest
from smithy_fixtures import native_weather_model

from spitzeisen.codegen.inputs import parse_smithy
from spitzeisen.codegen.plan_io import client_plan_document, client_plan_from_document
from spitzeisen.codegen.policy import TargetSettings, compile_model
from spitzeisen.codegen.traits import model_customizations


def test_client_plan_json_contract_round_trips() -> None:
    """The Java boundary can reconstruct the exact immutable renderer plan."""
    model = native_weather_model()
    client = compile_model(
        TargetSettings(package="weather_sdk", client_name="WeatherApi"),
        parse_smithy(model),
        model_customizations(model),
    )

    assert client_plan_from_document(client_plan_document(client)) == client


def test_client_plan_json_contract_rejects_unknown_versions() -> None:
    """Contract changes require an explicit reader instead of accidental compatibility."""
    with pytest.raises(ValueError, match="unsupported client-plan schema version"):
        client_plan_from_document({"schema_version": 2, "client": {}})
