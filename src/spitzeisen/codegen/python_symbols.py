"""Central Python names, files, imports, and Smithy-to-Python type mapping."""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from spitzeisen.codegen.python_plan import PythonImport, PythonTypeKind, PythonTypePlan

if TYPE_CHECKING:
    from spitzeisen.codegen.python_context import PythonExternalModel
    from spitzeisen.codegen.service_plan import MemberPlan, ServicePlan, ShapePlan

PYTHON_OPERATION_TRAIT = "spitzeisen.python#operation"
PYTHON_PARAMETER_TRAIT = "spitzeisen.python#parameter"
PYTHON_MODEL_FIELD_TRAIT = "spitzeisen.python#modelField"

_FIRST_CAPITAL = re.compile(r"(.)([A-Z][a-z]+)")
_SECOND_CAPITAL = re.compile(r"([a-z0-9])([A-Z])")
_NON_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")
_WORDS = re.compile(r"[A-Za-z0-9]+")
_RESERVED = frozenset({*keyword.kwlist, *keyword.softkwlist, "cls", "self"})
_PRELUDE_KINDS: dict[str, PythonTypeKind] = {
    "smithy.api#BigDecimal": "decimal",
    "smithy.api#BigInteger": "int",
    "smithy.api#Blob": "bytes",
    "smithy.api#Boolean": "bool",
    "smithy.api#Byte": "int",
    "smithy.api#Document": "document",
    "smithy.api#Double": "float",
    "smithy.api#Float": "float",
    "smithy.api#Integer": "int",
    "smithy.api#Long": "int",
    "smithy.api#PrimitiveBoolean": "bool",
    "smithy.api#PrimitiveByte": "int",
    "smithy.api#PrimitiveDouble": "float",
    "smithy.api#PrimitiveFloat": "float",
    "smithy.api#PrimitiveInteger": "int",
    "smithy.api#PrimitiveLong": "int",
    "smithy.api#PrimitiveShort": "int",
    "smithy.api#Short": "int",
    "smithy.api#String": "str",
    "smithy.api#Timestamp": "datetime",
}


class PythonSymbolError(ValueError):
    """A Smithy symbol cannot be represented by the Python target."""


def _empty_properties() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class PythonSymbol:
    """The Python definition selected for one Smithy shape."""

    shape_id: str
    name: str
    module: str
    definition_file: str
    imports: tuple[PythonImport, ...] = ()
    dependencies: tuple[str, ...] = ()
    properties: dict[str, str] = field(default_factory=_empty_properties)


class NameAllocator:
    """Allocate stable valid Python names and resolve collisions per lexical scope."""

    def __init__(self) -> None:
        """Create an empty allocator with independent lexical scopes."""
        self._allocated: dict[tuple[str, str], str] = {}
        self._used: dict[str, set[str]] = {}

    def allocate(
        self,
        preferred: str,
        *,
        key: str,
        scope: str,
        style: Literal["pascal", "snake"] = "snake",
    ) -> str:
        """Return an idempotent name, adding a numeric suffix only on collision."""
        identity = (scope, key)
        if identity in self._allocated:
            return self._allocated[identity]
        candidate = pascal_case(preferred) if style == "pascal" else snake_case(preferred)
        used = self._used.setdefault(scope, set())
        name = candidate
        suffix = 2
        while name in used:
            name = f"{candidate}_{suffix}"
            suffix += 1
        used.add(name)
        self._allocated[identity] = name
        return name


class PythonSymbolProvider:
    """Map neutral ShapeIds and members into Python-specific target symbols."""

    def __init__(
        self,
        plan: ServicePlan,
        *,
        package: str,
        external_models: dict[str, PythonExternalModel] | None = None,
        allocator: NameAllocator | None = None,
    ) -> None:
        """Create a provider for one selected service and Python package."""
        self.plan = plan
        self.package = package
        self.external_models = external_models or {}
        self.allocator = allocator or NameAllocator()
        self._symbols: dict[str, PythonSymbol] = {}

    def to_shape_symbol(self, shape_id: str) -> PythonSymbol:
        """Return the stable named Python definition for a Smithy shape."""
        if shape_id in self._symbols:
            return self._symbols[shape_id]
        external = self.external_models.get(shape_id)
        if external is not None:
            symbol = PythonSymbol(
                shape_id=shape_id,
                name=external.symbol,
                module=external.module,
                definition_file="",
                imports=(PythonImport(external.module, external.symbol),),
                dependencies=external.dependencies,
                properties={"external": "true"},
            )
            self._symbols[shape_id] = symbol
            return symbol
        self.plan.shape(shape_id)
        preferred = _shape_name(shape_id)
        name = self.allocator.allocate(preferred, key=shape_id, scope="models", style="pascal")
        filename = snake_case(name)
        module = f"{self.package}.models.{filename}"
        symbol = PythonSymbol(
            shape_id=shape_id,
            name=name,
            module=module,
            definition_file=str(PurePosixPath(*self.package.split("."), "models", f"{filename}.py")),
        )
        self._symbols[shape_id] = symbol
        return symbol

    def to_type(self, shape_id: str) -> PythonTypePlan:
        """Map an exact Smithy kind to a structured Python annotation."""
        if shape_id in self.external_models:
            symbol = self.to_shape_symbol(shape_id)
            return PythonTypePlan("symbol", name=symbol.name, module=symbol.module)
        prelude = _PRELUDE_KINDS.get(shape_id)
        if prelude is not None:
            return PythonTypePlan(prelude)
        shape = self.plan.shape(shape_id)
        primitives: dict[str, PythonTypeKind] = {
            "blob": "bytes",
            "boolean": "bool",
            "byte": "int",
            "short": "int",
            "integer": "int",
            "long": "int",
            "bigInteger": "int",
            "float": "float",
            "double": "float",
            "string": "str",
            "timestamp": "datetime",
            "document": "document",
            "bigDecimal": "decimal",
        }
        if shape.kind in primitives:
            return PythonTypePlan(primitives[shape.kind])
        if shape.kind in {"structure", "union", "enum", "intEnum"}:
            symbol = self.to_shape_symbol(shape_id)
            return PythonTypePlan("symbol", name=symbol.name, module=symbol.module)
        if shape.kind in {"list", "set"}:
            member = _collection_member(shape)
            collection_kind: Literal["list", "set"] = "list" if shape.kind == "list" else "set"
            return PythonTypePlan(collection_kind, members=(self.to_type(member.target),))
        if shape.kind == "map":
            key = _named_member(shape, "key")
            value = _named_member(shape, "value")
            return PythonTypePlan("dict", members=(self.to_type(key.target), self.to_type(value.target)))
        msg = f"Python has no type mapping for Smithy {shape.kind!r} shape {shape_id}"
        raise PythonSymbolError(msg)

    def to_parameter_type(self, shape_id: str) -> PythonTypePlan:
        """Map a shape to its public input type, exposing enums as closed Literals."""
        if shape_id in self.external_models:
            return self.to_type(shape_id)
        prelude = _PRELUDE_KINDS.get(shape_id)
        if prelude is not None:
            return PythonTypePlan(prelude)
        shape = self.plan.shape(shape_id)
        if shape.kind in {"enum", "intEnum"}:
            return PythonTypePlan("literal", values=tuple(value.value for value in shape.enum_values))
        if shape.kind in {"list", "set"}:
            member = _collection_member(shape)
            collection_kind: Literal["list", "set"] = "list" if shape.kind == "list" else "set"
            return PythonTypePlan(collection_kind, members=(self.to_parameter_type(member.target),))
        if shape.kind == "map":
            key = _named_member(shape, "key")
            value = _named_member(shape, "value")
            return PythonTypePlan(
                "dict",
                members=(self.to_parameter_type(key.target), self.to_parameter_type(value.target)),
            )
        return self.to_type(shape_id)

    def to_member_name(
        self,
        member: MemberPlan,
        *,
        scope: str,
        purpose: Literal["model", "parameter"] = "parameter",
    ) -> str:
        """Allocate a member name, applying only the relevant Python extension."""
        trait_id = PYTHON_PARAMETER_TRAIT if purpose == "parameter" else PYTHON_MODEL_FIELD_TRAIT
        extension = member.extensions.get(trait_id)
        preferred = member.name
        if isinstance(extension, dict):
            configured = extension.get("name")
            if isinstance(configured, str):
                preferred = configured
        return self.allocator.allocate(preferred, key=f"{purpose}:{member.id}", scope=scope)


def snake_case(value: str) -> str:
    """Convert a Smithy or configured name to a valid non-keyword Python identifier."""
    value = _NON_IDENTIFIER.sub("_", value)
    value = _FIRST_CAPITAL.sub(r"\1_\2", value)
    value = _SECOND_CAPITAL.sub(r"\1_\2", value).lower().strip("_") or "value"
    if value[0].isdigit():
        value = f"_{value}"
    if value in _RESERVED or keyword.iskeyword(value):
        value = f"{value}_"
    return value


def pascal_case(value: str) -> str:
    """Convert a configured name to a valid Python class identifier."""
    words = _WORDS.findall(_FIRST_CAPITAL.sub(r"\1 \2", _SECOND_CAPITAL.sub(r"\1 \2", value)))
    result = "".join(word[:1].upper() + word[1:] for word in words) or "Value"
    if result[0].isdigit():
        result = f"_{result}"
    if result in _RESERVED or keyword.iskeyword(result):
        result = f"{result}_"
    return result


def _shape_name(shape_id: str) -> str:
    return shape_id.rsplit("#", maxsplit=1)[-1].split("$", maxsplit=1)[0]


def _collection_member(shape: ShapePlan) -> MemberPlan:
    if len(shape.members) != 1:
        msg = f"Smithy {shape.kind} shape {shape.id} must have exactly one member"
        raise PythonSymbolError(msg)
    return shape.members[0]


def _named_member(shape: ShapePlan, name: str) -> MemberPlan:
    try:
        return shape.member(name)
    except KeyError as error:
        msg = f"Smithy map shape {shape.id} has no {name!r} member"
        raise PythonSymbolError(msg) from error
