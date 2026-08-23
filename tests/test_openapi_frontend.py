"""The upstream-shaped OpenAPI loading and parser workflow."""

from typing import Any

import pytest

from spitzeisen.codegen import load_yaml_or_json
from spitzeisen.codegen.ir import (
    HTTPMethod,
    LiteralType,
    ParameterLocation,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    UnionType,
)
from spitzeisen.codegen.manifest import Manifest
from spitzeisen.codegen.parser import GeneratorData
from spitzeisen.codegen.parser.errors import GeneratorError
from spitzeisen.codegen.policy import compile_manifest


def openapi_document(*, version: str = "3.1.0", paths: dict[str, object] | None = None) -> dict[str, Any]:
    """Build a small OpenAPI document accepted by the vendored Pydantic model."""
    return {
        "openapi": version,
        "info": {"title": "Parser fixture", "version": "1.0.0"},
        "paths": paths or {},
    }


def parsed(spec: dict[str, Any]) -> GeneratorData:
    """Parse a fixture and assert that hydration succeeded."""
    result = GeneratorData.from_dict(spec)
    assert isinstance(result, GeneratorData)
    return result


def test_yaml_and_json_loading_match_upstream_content_type_rules() -> None:
    """Exact JSON content types select JSON; every other source is parsed as YAML."""
    as_json = load_yaml_or_json(b'{"openapi": "3.1.0"}', "application/json")
    as_yaml = load_yaml_or_json(b"openapi: 3.1.0\n", "application/yaml")

    assert as_json == {"openapi": "3.1.0"}
    assert as_yaml == {"openapi": "3.1.0"}


def test_pydantic_hydration_is_the_only_document_validation_layer() -> None:
    """The frontend returns upstream-style errors instead of structured diagnostics."""
    invalid = GeneratorData.from_dict(openapi_document(version="3.2.0"))
    permissive = parsed(
        openapi_document(
            paths={
                "/things/{thing_id}": {
                    "get": {
                        "parameters": [
                            {"name": "thing_id", "in": "path", "required": False, "schema": {"type": "string"}},
                        ],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
        ),
    )

    assert isinstance(invalid, GeneratorError)
    assert invalid.header == "Failed to parse OpenAPI document"
    assert "Only OpenAPI versions 3.1.* are supported" in (invalid.detail or "")
    assert permissive.endpoints[0].path_parameters[0].required is False


def test_local_parameter_references_are_built_before_endpoints() -> None:
    """Reusable parameters are resolved through the component registry."""
    spec = {
        **openapi_document(
            paths={
                "/accounts/{account_id}": {
                    "get": {
                        "parameters": [{"$ref": "#/components/parameters/AccountId"}],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
        ),
        "components": {
            "parameters": {
                "AccountId": {
                    "name": "account_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                },
            },
        },
    }

    operation = parsed(spec).endpoints[0]

    assert operation.parameters[0].wire_name == "account_id"
    assert operation.parameters[0].location is ParameterLocation.PATH
    assert operation.parameters[0].schema == PrimitiveType(PrimitiveKind.INTEGER)


def test_remote_parameter_references_become_endpoint_warnings() -> None:
    """Unsupported references omit that endpoint, as they do upstream."""
    spec = openapi_document(
        paths={
            "/things": {
                "get": {
                    "parameters": [{"$ref": "parameters.yaml#/Thing"}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    )

    result = parsed(spec)
    collection = result.endpoint_collections_by_tag["default"]

    assert collection.endpoints == []
    assert len(collection.parse_errors) == 1
    assert "Remote references" in (collection.parse_errors[0].detail or "")


def test_parameter_precedence_path_order_and_response_patterns_follow_upstream() -> None:
    """Operation parameters win, path parameters are sorted, and responses use parser precedence."""
    spec = openapi_document(
        paths={
            "/accounts/{account_id}/{record_id}": {
                "parameters": [
                    {"name": "record_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "region", "in": "query", "schema": {"type": "string"}},
                    {"name": "account_id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "get": {
                    "operationId": "readRecord",
                    "parameters": [
                        {"name": "region", "in": "query", "schema": {"enum": ["eu", "us"], "default": "eu"}},
                    ],
                    "responses": {
                        "default": {"description": "Fallback"},
                        "2XX": {"description": "Any success"},
                        "404": {"description": "Missing"},
                        "200": {"description": "Found"},
                    },
                },
            },
        },
    )

    operation = parsed(spec).operation_named("readRecord")

    assert operation is not None
    assert operation.method is HTTPMethod.GET
    assert [parameter.wire_name for parameter in operation.parameters] == ["account_id", "record_id", "region"]
    assert operation.parameters[2].schema == LiteralType(("eu", "us"))
    assert operation.parameters[2].default == "eu"
    assert [response.status.raw for response in operation.responses] == ["200", "404", "2XX", "default"]


def test_operation_id_selection_still_checks_manifest_wire_drift() -> None:
    """An operation ID selects an endpoint without concealing a path change."""
    openapi = parsed(
        openapi_document(
            paths={
                "/things": {
                    "get": {
                        "operationId": "listThings",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
        ),
    )
    manifest_data: dict[str, Any] = {
        "vendor": "example",
        "base_url": "https://api.example.test",
        "package": "example_sdk",
        "client_name": "ExampleApi",
        "endpoints": {
            "things": {
                "operation_id": "listThings",
                "path": "/things",
                "method_name": "get_things",
                "model": "Thing",
            },
        },
    }

    client = compile_manifest(Manifest.model_validate(manifest_data), openapi)

    assert client.endpoints[0].path == "/things"
    manifest_data["endpoints"]["things"]["path"] = "/renamed"
    with pytest.raises(ValueError, match="resolves to GET /things"):
        compile_manifest(Manifest.model_validate(manifest_data), openapi)


def test_nullable_and_reference_schemas_remain_generator_neutral() -> None:
    """The vendored model normalizes nullable before the small type IR is built."""
    responses = {
        "200": {
            "description": "OK",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}},
        },
    }
    v30 = {
        **openapi_document(version="3.0.4", paths={"/things": {"get": {"responses": responses}}}),
        "components": {"schemas": {"Thing": {"type": "string", "nullable": True}}},
    }
    response_type = parsed(v30).endpoints[0].responses[0].content[0].schema

    assert isinstance(response_type, ReferenceType)
    assert response_type.suggested_name == "Thing"

    inline = openapi_document(
        paths={
            "/things": {
                "get": {
                    "parameters": [
                        {"name": "value", "in": "query", "schema": {"type": ["string", "null"]}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    )
    schema = parsed(inline).endpoints[0].parameters[0].schema

    assert schema == UnionType((PrimitiveType(PrimitiveKind.STRING), PrimitiveType(PrimitiveKind.NULL)))
    assert all(word not in repr(schema) for word in ("Jinja", "Unset", "Import"))
