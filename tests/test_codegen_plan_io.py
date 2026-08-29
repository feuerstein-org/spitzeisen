"""Tests for the versioned Smithy-frontend rendering contract."""

from typing import Any, cast

import pytest
from plan_fixtures import native_weather_client, splits_client

from spitzeisen.codegen.plan_io import client_plan_document, client_plan_from_document


def test_client_plan_json_contract_round_trips() -> None:
    """The Java boundary can reconstruct the exact immutable renderer plan."""
    client = native_weather_client()

    assert client_plan_from_document(client_plan_document(client)) == client


def test_client_plan_json_contract_rejects_unknown_versions() -> None:
    """Contract changes require an explicit reader instead of accidental compatibility."""
    with pytest.raises(ValueError, match="unsupported client-plan schema version"):
        client_plan_from_document({"schema_version": 2, "client": {}})


def test_client_plan_json_contract_rejects_unknown_enum_values() -> None:
    """A corrupt or newer frontend cannot silently select a renderer branch."""
    document = client_plan_document(native_weather_client())
    client = cast("dict[str, Any]", document["client"])
    operation = cast("dict[str, Any]", cast("list[object]", client["operations"])[0])
    operation["shape"] = "stream"

    with pytest.raises(ValueError, match=r"shape.*collection.*single.*stream"):
        client_plan_from_document(document)


def test_client_plan_json_contract_rejects_non_json_defaults() -> None:
    """Sorting defaults stay portable across the Java-to-Python JSON boundary."""
    document = client_plan_document(splits_client())
    client = cast("dict[str, Any]", document["client"])
    operation = cast("dict[str, Any]", cast("list[object]", client["operations"])[0])
    sorting = cast("dict[str, Any]", operation["sorting"])
    sort = cast("dict[str, Any]", sorting["sort"])
    sort["default"] = object()

    with pytest.raises(TypeError, match="must be a JSON value"):
        client_plan_from_document(document)
