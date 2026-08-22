"""The validated, reference-aware OpenAPI compiler frontend."""

import json
from pathlib import Path
from typing import Any

import pytest

from spitzeisen.codegen.diagnostics import CodegenError
from spitzeisen.codegen.document import OpenAPIDocument
from spitzeisen.codegen.ir import (
    HTTPMethod,
    LiteralType,
    OpenAPIDialect,
    ParameterLocation,
    PrimitiveKind,
    PrimitiveType,
    ReferenceType,
    UnionType,
)
from spitzeisen.codegen.lower import lower_document
from spitzeisen.codegen.manifest import Manifest
from spitzeisen.codegen.policy import compile_manifest


def openapi_document(*, version: str = "3.1.0", paths: dict[str, object] | None = None) -> dict[str, object]:
    """Build a small valid OpenAPI document."""
    return {
        "openapi": version,
        "info": {"title": "Compiler fixture", "version": "1.0.0"},
        "paths": paths or {},
    }


@pytest.mark.parametrize(
    ("version", "dialect"),
    [
        ("3.0.4", OpenAPIDialect.V3_0),
        ("3.1.1", OpenAPIDialect.V3_1),
        ("3.2.0", OpenAPIDialect.V3_2),
    ],
)
def test_supported_openapi_dialects_are_selected_explicitly(version: str, dialect: OpenAPIDialect) -> None:
    """The compiler accepts current 3.x dialects without pretending they are identical."""
    document = OpenAPIDocument.from_mapping(openapi_document(version=version))

    assert document.version == version
    assert document.dialect is dialect


def test_validation_errors_include_a_source_uri_and_pointer() -> None:
    """Invalid vendor input reports where it failed rather than leaking validator internals."""
    spec = openapi_document(
        paths={
            "/things": {
                "get": {
                    "parameters": [{"name": "where", "in": "planet", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    )

    with pytest.raises(CodegenError) as caught:
        OpenAPIDocument.from_mapping(spec, base_uri="urn:test:invalid")

    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "openapi.invalid"
    assert diagnostic.location.uri == "urn:test:invalid"
    assert diagnostic.location.pointer.startswith("/paths/~1things/get/parameters/0")


def test_local_parameter_references_are_resolved_before_lowering() -> None:
    """Reusable parameters become effective operation facts while retaining source identity."""
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

    operation = lower_document(OpenAPIDocument.from_mapping(spec)).operations[0]

    assert operation.parameters[0].wire_name == "account_id"
    assert operation.parameters[0].location is ParameterLocation.PATH
    assert operation.parameters[0].schema == PrimitiveType(PrimitiveKind.INTEGER)


def test_external_references_are_limited_to_the_declared_source_tree(tmp_path: Path) -> None:
    """A split specification can reference sibling files but cannot traverse above its root."""
    source_root = tmp_path / "spec"
    source_root.mkdir()
    (source_root / "parameters.json").write_text(
        json.dumps(
            {
                "AccountId": {
                    "name": "account_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                },
            },
        ),
    )
    root = source_root / "openapi.json"
    root.write_text(
        json.dumps(
            openapi_document(
                paths={
                    "/accounts/{account_id}": {
                        "get": {
                            "parameters": [{"$ref": "parameters.json#/AccountId"}],
                            "responses": {"200": {"description": "OK"}},
                        },
                    },
                },
            ),
        ),
    )

    operation = lower_document(OpenAPIDocument.from_path(root, reference_root=source_root)).operations[0]

    assert operation.parameters[0].wire_name == "account_id"

    outside = tmp_path / "outside.json"
    outside.write_text((source_root / "parameters.json").read_text())
    escaped = json.loads(root.read_text())
    escaped["paths"]["/accounts/{account_id}"]["get"]["parameters"] = [
        {"$ref": "../outside.json#/AccountId"},
    ]
    root.write_text(json.dumps(escaped))

    with pytest.raises(CodegenError, match="escapes the allowed OpenAPI directory"):
        lower_document(OpenAPIDocument.from_path(root, reference_root=source_root))


def test_remote_references_never_trigger_implicit_network_access() -> None:
    """Remote refs require vendoring, making generation hermetic and preventing SSRF."""
    spec = openapi_document(
        paths={
            "/things": {
                "get": {
                    "parameters": [{"$ref": "https://example.invalid/parameters.json#/Thing"}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    )

    with pytest.raises(CodegenError, match=r"remote OpenAPI reference.*is disabled"):
        lower_document(OpenAPIDocument.from_mapping(spec))


def test_parameter_precedence_path_order_and_response_patterns_are_normalized() -> None:
    """The IR owns the OpenAPI rules renderers would otherwise repeatedly reimplement."""
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
                        {
                            "name": "region",
                            "in": "query",
                            "schema": {"enum": ["eu", "us"], "default": "eu"},
                        },
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

    operation = lower_document(OpenAPIDocument.from_mapping(spec)).operation_named("readRecord")

    assert operation is not None
    assert operation.method is HTTPMethod.GET
    assert [parameter.wire_name for parameter in operation.parameters] == ["account_id", "record_id", "region"]
    assert operation.parameters[2].schema == LiteralType(("eu", "us"))
    assert operation.parameters[2].default == "eu"
    assert [response.status.raw for response in operation.responses] == ["200", "404", "2XX", "default"]


def test_operation_id_selection_also_checks_path_and_method_drift() -> None:
    """Stable operation IDs can select an endpoint without concealing a vendor wire change."""
    document = lower_document(
        OpenAPIDocument.from_mapping(
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

    client = compile_manifest(Manifest.model_validate(manifest_data), document)

    assert client.endpoints[0].path == "/things"
    manifest_data["endpoints"]["things"]["path"] = "/renamed"
    with pytest.raises(CodegenError, match="operation-drift"):
        compile_manifest(Manifest.model_validate(manifest_data), document)


def test_nullable_and_reference_schemas_remain_generator_neutral() -> None:
    """Dialect differences normalize into types without Python imports or template concepts."""
    responses = {
        "200": {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Thing"},
                },
            },
        },
    }
    v30 = {
        **openapi_document(version="3.0.4", paths={"/things": {"get": {"responses": responses}}}),
        "components": {"schemas": {"Thing": {"type": "string", "nullable": True}}},
    }
    operation = lower_document(OpenAPIDocument.from_mapping(v30)).operations[0]
    response_type = operation.responses[0].content[0].schema

    assert isinstance(response_type, ReferenceType)
    assert response_type.suggested_name == "Thing"

    inline = {
        **openapi_document(
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
        ),
    }
    schema = lower_document(OpenAPIDocument.from_mapping(inline)).operations[0].parameters[0].schema

    assert schema == UnionType((PrimitiveType(PrimitiveKind.STRING), PrimitiveType(PrimitiveKind.NULL)))
    assert all(word not in repr(schema) for word in ("Jinja", "Unset", "Import"))
