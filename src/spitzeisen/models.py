"""Shared model policy for SDK response types."""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, ValidationInfo
from pydantic_core import PydanticCustomError, core_schema

# How many times one validation saw each (field, value) pair.
type UnknownEnumCounts = Counter[tuple[str | None, str]]


@dataclass(slots=True)
class ValidationContext:
    """What core passes to `UnknownValue` fields for one validation."""

    allow_unknown_enums: bool
    unknown_enums: UnknownEnumCounts = field(default_factory=Counter[tuple[str | None, str]])


# TODO: According to Smithy we need to add support to allow disabling validation
# of the output to allow an old client to support a newer API version, this
# can be achieved creating "strict" and "non-strict" pydantic models, the
# non-strict ones would have limitations like integer ranges or date requirements
# it would still enforce types through (e.g. if int is expected and bool returned)
# but that's fine.


class SpitzeisenModel(BaseModel):
    """
    Optional base for response models, plain Pydantic BaseModel works too.

    Unknown response fields are ignored for compatibility with additive API changes.
    Pydantic field types, defaults, aliases, constraints, and validators behave normally.
    SDKs choose compatible field types and may provide separate models for explicit
    contract checking. Declared constraints are always enforced during validation.
    """

    model_config = ConfigDict(extra="ignore")


class UnknownValue(str):
    """
    An enum value the SDK does not know yet, holding what the API sent.

    Type a response field as `Literal[...] | UnknownValue`, with the `Literal` first.
    Each validation call logs one warning per new value, with a count. The value fails
    validation instead if the client sets `allow_unknown_enums=False`, or if the model is validated
    without a client.
    """

    __slots__ = ()

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return core_schema.with_info_after_validator_function(cls._from_response, core_schema.str_schema())

    @classmethod
    def _from_response(cls, value: str, info: ValidationInfo) -> Self:
        """Keep and count the value, unless the client's validation context forbids unknown values."""
        context = (
            info.context
            if isinstance(info.context, ValidationContext)
            else ValidationContext(allow_unknown_enums=False)
        )
        if not context.allow_unknown_enums:
            error_type = "unknown_enum_value"
            msg = (
                "Unknown enum value '{value}'. "
                "Set allow_unknown_enums=True on the client and validate through it to keep it."
            )
            raise PydanticCustomError(error_type, msg, {"value": value})
        context.unknown_enums[info.field_name, value] += 1
        return cls(value)
