"""Code-generation input loading and the upstream-shaped OpenAPI parser workflow."""

from typing import Any

import pytest
from pydantic import ValidationError

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.inputs import load_mapping, parse_openapi
from spitzeisen.codegen.ir import (
    HTTPMethod,
    LiteralType,
    ParamLocationIR,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    UnionType,
)
from spitzeisen.codegen.manifest import Manifest
from spitzeisen.codegen.parser import ParsedOpenAPI
from spitzeisen.codegen.policy import compile_manifest


def openapi_document(*, version: str = "3.1.0", paths: dict[str, object] | None = None) -> dict[str, Any]:
    """Build a small OpenAPI document accepted by the vendored Pydantic model."""
    return {
        "openapi": version,
        "info": {"title": "Parser fixture", "version": "1.0.0"},
        "paths": paths or {},
    }


def parsed(spec: dict[str, Any]) -> ParsedOpenAPI:
    """Parse a fixture and assert that hydration succeeded."""
    return ParsedOpenAPI.from_dict(spec)


def test_yaml_and_json_loading_match_upstream_content_type_rules() -> None:
    """Exact JSON content types select JSON; every other source is parsed as YAML."""
    as_json = load_mapping(b'{"openapi": "3.1.0"}', "application/json")
    as_yaml = load_mapping(b"openapi: 3.1.0\n", "application/yaml")

    assert as_json == {"openapi": "3.1.0"}
    assert as_yaml == {"openapi": "3.1.0"}


def test_document_loading_raises_for_invalid_or_non_mapping_input() -> None:
    """Fatal source failures use exceptions and reject unusable top-level values early."""
    with pytest.raises(CodegenError, match="Expecting property name"):
        load_mapping(b"{", "application/json")
    with pytest.raises(CodegenError, match="mapping at its top level"):
        load_mapping(b"[]", "application/json")


def test_pydantic_hydration_is_the_only_document_validation_layer() -> None:
    """The parser raises Pydantic errors and the input layer adds CLI-facing context."""
    invalid = openapi_document(version="3.2.0")
    with pytest.raises(ValidationError, match=r"Only OpenAPI versions 3\.1\.\* are supported"):
        ParsedOpenAPI.from_dict(invalid)
    with pytest.raises(CodegenError, match=r"Only OpenAPI versions 3\.1\.\* are supported") as raised:
        parse_openapi(invalid)

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

    assert raised.value.header == "Failed to parse OpenAPI document"
    assert isinstance(raised.value.__cause__, ValidationError)
    assert permissive.operations[0].path_params[0].required is False


def test_input_layer_adds_the_swagger_hint_without_discarding_the_validation_error() -> None:
    """Swagger guidance is presentation policy layered over the original Pydantic failure."""
    with pytest.raises(CodegenError) as raised:
        parse_openapi({"swagger": "2.0", "info": {"title": "Old API", "version": "1.0"}, "paths": {}})

    assert "You may be trying to use a Swagger document" in (raised.value.detail or "")
    assert isinstance(raised.value.__cause__, ValidationError)


def test_local_param_references_are_built_before_operations() -> None:
    """Reusable params are resolved through the component registry."""
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

    operation = parsed(spec).operations[0]

    assert operation.params[0].wire_name == "account_id"
    assert operation.params[0].location is ParamLocationIR.PATH
    assert operation.params[0].schema == PrimitiveType(PrimitiveKind.INTEGER)


def test_remote_param_references_become_operation_warnings() -> None:
    """Unsupported references omit that operation, as they do upstream."""
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
    collection = result.operation_collections_by_tag["default"]

    assert collection.operations == []
    assert len(collection.parse_errors) == 1
    assert "Remote references" in (collection.parse_errors[0].detail or "")


def test_param_precedence_path_order_and_response_patterns_follow_upstream() -> None:
    """Operation params win, path params are sorted, and responses use parser precedence."""
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
    assert [param.wire_name for param in operation.params] == ["account_id", "record_id", "region"]
    assert operation.params[2].schema == LiteralType(("eu", "us"))
    assert operation.params[2].default == "eu"
    assert [response.status.raw for response in operation.responses] == ["200", "404", "2XX", "default"]


def test_operation_id_selection_still_checks_manifest_wire_drift() -> None:
    """An operation ID selects an operation without concealing a path change."""
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
        "operations": {
            "things": {
                "operation_id": "listThings",
                "path": "/things",
                "method_name": "get_things",
                "model": "Thing",
            },
        },
    }

    client = compile_manifest(Manifest.model_validate(manifest_data), openapi)

    assert client.operations[0].path == "/things"
    manifest_data["operations"]["things"]["path"] = "/renamed"
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
    response_type = parsed(v30).operations[0].responses[0].content[0].schema

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
    schema = parsed(inline).operations[0].params[0].schema

    assert schema == UnionType((PrimitiveType(PrimitiveKind.STRING), PrimitiveType(PrimitiveKind.NULL)))
    assert all(word not in repr(schema) for word in ("Jinja", "Unset", "Import"))
