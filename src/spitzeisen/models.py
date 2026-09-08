"""Shared model policy for SDK response types."""

from typing import Annotated, Any, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationInfo
from pydantic_core import SchemaValidator, core_schema


def response_constraints(*, choices: tuple[str | int, ...] | None = None, **constraints: Any) -> AfterValidator:
    """
    Compile optional response checks once, selected by each call's validation context.

    SDK annotations keep ordinary Python types. Pydantic still parses them before
    these checks run; only the modeled constraints and known enum values are optional.
    The same context reaches nested models and collection members without shared state.
    """
    adapter = TypeAdapter[Any](Annotated[Any, Field(**constraints)]) if constraints else None
    enum = SchemaValidator(core_schema.literal_schema(list(choices))) if choices else None

    def validate(value: Any, info: ValidationInfo) -> Any:
        context = info.context
        if (
            value is None
            or not isinstance(context, dict)
            or not cast("dict[str, Any]", context).get("strict_response_validation")
        ):
            return value
        if enum is not None:
            enum.validate_python(value)
        if adapter is not None:
            adapter.validate_python(value)
        return value

    return AfterValidator(validate)


class SpitzeisenModel(BaseModel):
    """
    Optional base for handwritten response models; plain Pydantic BaseModel works too.

    Unknown response fields are ignored for compatibility with additive API changes.
    Pydantic field types, defaults, aliases, constraints, and validators behave normally.
    Fields explicitly annotated with ``response_constraints`` opt into contextual checks
    selected by ``strict_response_validation``; ordinary Field constraints always apply.
    """

    model_config = ConfigDict(extra="ignore")
