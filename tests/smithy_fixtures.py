"""Small Smithy JSON AST builders for codegen policy tests."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, cast


def _shape_name(value: str) -> str:
    words = re.split(r"[^A-Za-z0-9]+", value)
    return "".join(word[:1].upper() + word[1:] for word in words if word) or "Operation"


def _parameter(value: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    reference = value.get("$ref")
    if not isinstance(reference, str):
        return value
    return deepcopy(components[reference.rsplit("/", maxsplit=1)[-1]])


def _target(
    schema: dict[str, Any],
    *,
    suggested_name: str,
    shapes: dict[str, Any],
) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        name = reference.rsplit("/", maxsplit=1)[-1]
        shapes.setdefault(f"example#{name}", {"type": "structure", "members": {}})
        return f"example#{name}"
    values = schema.get("enum")
    if isinstance(values, list):
        enum_values = cast("list[object]", values)
        shape_id = f"example#{suggested_name}"
        shapes[shape_id] = {
            "type": "enum",
            "members": {
                f"value{index}": {
                    "target": "smithy.api#Unit",
                    "traits": {"smithy.api#enumValue": value},
                }
                for index, value in enumerate(enum_values)
            },
        }
        return shape_id
    raw_type = schema.get("type", "string")
    if isinstance(raw_type, list):
        type_values = cast("list[object]", raw_type)
        raw_type = next((item for item in type_values if item != "null"), "string")
    if raw_type == "array":
        shape_id = f"example#{suggested_name}"
        shapes[shape_id] = {
            "type": "list",
            "member": {
                "target": _target(
                    schema.get("items", {"type": "string"}),
                    suggested_name=f"{suggested_name}Member",
                    shapes=shapes,
                ),
            },
        }
        return shape_id
    if raw_type == "object":
        return "smithy.api#Document"
    primitive = {
        "boolean": "Boolean",
        "integer": "Integer",
        "number": "Double",
        "string": "String",
    }.get(str(raw_type), "String")
    if schema.get("format") == "date":
        shape_id = f"example#{suggested_name}"
        shapes[shape_id] = {"type": "string", "traits": {"alloy#dateFormat": {}}}
        return shape_id
    return f"smithy.api#{primitive}"


def smithy_model(spec: dict[str, Any]) -> dict[str, Any]:  # noqa: C901, PLR0915
    """Build the subset of converted Smithy needed by rendering tests."""
    shapes: dict[str, Any] = {}
    operation_ids: list[str] = []
    components_root = spec.get("components", {})
    components: object = (
        cast("dict[str, Any]", components_root).get("parameters", {}) if isinstance(components_root, dict) else {}
    )
    parameter_components = cast("dict[str, Any]", components) if isinstance(components, dict) else {}
    methods = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
    raw_paths = spec.get("paths", {})
    paths = cast("dict[str, Any]", raw_paths) if isinstance(raw_paths, dict) else {}
    for path, raw_path_item in paths.items():
        if not isinstance(raw_path_item, dict):
            continue
        path_item = cast("dict[str, Any]", raw_path_item)
        raw_inherited = path_item.get("parameters", [])
        inherited = [
            _parameter(cast("dict[str, Any]", item), parameter_components)
            for item in cast("list[object]", raw_inherited)
            if isinstance(item, dict)
        ]
        for method in methods:
            raw_operation = path_item.get(method)
            if not isinstance(raw_operation, dict):
                continue
            operation = cast("dict[str, Any]", raw_operation)
            raw_operation_id = operation.get("operationId")
            operation_id = raw_operation_id if isinstance(raw_operation_id, str) else f"{method}_{path}"
            operation_name = _shape_name(operation_id)
            raw_operation_params = operation.get("parameters", [])
            operation_params = [
                _parameter(cast("dict[str, Any]", item), parameter_components)
                for item in cast("list[object]", raw_operation_params)
                if isinstance(item, dict)
            ]
            effective: dict[tuple[str, str], dict[str, Any]] = {(item["name"], item["in"]): item for item in inherited}
            effective.update({(item["name"], item["in"]): item for item in operation_params})
            members: dict[str, Any] = {}
            for index, param in enumerate(effective.values()):
                schema = param.get("schema")
                if not isinstance(schema, dict):
                    continue
                schema = cast("dict[str, Any]", schema)
                location = param["in"]
                binding = {
                    "query": "smithy.api#httpQuery",
                    "header": "smithy.api#httpHeader",
                    "path": "smithy.api#httpLabel",
                }.get(location)
                if binding is None:
                    continue
                member_traits: dict[str, Any] = {
                    binding: {} if location == "path" else param["name"],
                }
                if param.get("required"):
                    member_traits["smithy.api#required"] = {}
                if description := param.get("description"):
                    member_traits["smithy.api#documentation"] = description
                if "default" in schema:
                    member_traits["smithy.api#default"] = schema["default"]
                if "minimum" in schema or "maximum" in schema:
                    member_traits["smithy.api#range"] = {
                        key: schema[source]
                        for key, source in (("min", "minimum"), ("max", "maximum"))
                        if source in schema
                    }
                member_name = re.sub(r"[^A-Za-z0-9_]", "_", param["name"]) or f"param{index}"
                members[member_name] = {
                    "target": _target(
                        schema,
                        suggested_name=f"{operation_name}{_shape_name(member_name)}",
                        shapes=shapes,
                    ),
                    "traits": member_traits,
                }
            input_id = f"example#{operation_name}Input"
            output_id = f"example#{operation_name}Output"
            shapes[input_id] = {"type": "structure", "members": members}
            shapes[output_id] = {"type": "structure", "members": {}}
            traits: dict[str, Any] = {
                "smithy.api#http": {"method": method.upper(), "uri": path, "code": 200},
            }
            description = operation.get("description") or operation.get("summary")
            if isinstance(description, str) and description:
                traits["smithy.api#documentation"] = description
            operation_shape_id = f"example#{operation_name}"
            shapes[operation_shape_id] = {
                "type": "operation",
                "input": {"target": input_id},
                "output": {"target": output_id},
                "traits": traits,
            }
            operation_ids.append(operation_shape_id)
    shapes["example#ExampleService"] = {
        "type": "service",
        "operations": [{"target": operation_id} for operation_id in operation_ids],
    }
    return {"smithy": "2.0", "shapes": shapes}
