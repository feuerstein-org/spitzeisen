"""Shared response-model policy."""

from typing import Annotated, Any

import pytest
from pydantic import Field, ValidationError

from spitzeisen import (
    AsyncSpitzeisenApi,
    AsyncSpitzeisenConfig,
    SpitzeisenModel,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
)
from spitzeisen.models import response_constraints


class ExampleModel(SpitzeisenModel):
    """A generated-model stand-in."""

    value: int


def test_spitzeisen_model_explicitly_ignores_unknown_response_members() -> None:
    """New Smithy output members remain forward-compatible while known fields are validated."""
    model = ExampleModel.model_validate({"value": 1, "unexpected": True})

    assert model == ExampleModel(value=1)
    assert not hasattr(model, "unexpected")


def test_spitzeisen_model_does_not_enable_population_by_field_name() -> None:
    """Aliases describe the wire and do not silently add a second accepted input name."""

    class Aliased(SpitzeisenModel):
        readable: int = Field(alias="wire_name")

    assert Aliased.model_validate({"wire_name": 1}).readable == 1
    with pytest.raises(ValidationError):
        Aliased.model_validate({"readable": 1})


class CheckedModel(SpitzeisenModel):
    """Generated constraints alongside an unconditional handwritten field rule."""

    score: Annotated[int, response_constraints(ge=0, le=10)]
    optional: Annotated[str | None, response_constraints(min_length=2)] = None
    handwritten: Annotated[int, Field(ge=0)] = 1


@pytest.mark.parametrize("context", [None, {}, {"strict_response_validation": False}, "application context"])
def test_response_checks_require_explicit_context(context: Any) -> None:
    """Direct constructors and unrelated caller context retain the compatible policy."""
    assert CheckedModel.model_validate({"score": "100"}, context=context) == CheckedModel(score=100)


@pytest.mark.parametrize("strict", [False, True])
def test_response_policy_is_independent_of_type_coercion_and_custom_rules(strict: bool) -> None:
    """Optional response checks preserve parsing, nullability, and handwritten validators."""
    context = {"strict_response_validation": strict}
    parsed = CheckedModel.model_validate({"score": "5", "optional": None}, context=context)
    assert parsed.score == 5
    assert parsed.optional is None
    with pytest.raises(ValidationError, match="score"):
        CheckedModel.model_validate({"score": "5"}, strict=True, context=context)
    with pytest.raises(ValidationError, match="handwritten"):
        CheckedModel.model_validate({"score": 5, "handwritten": -1}, context=context)


@pytest.mark.parametrize("async_", [False, True])
def test_existing_client_can_switch_response_checks(async_: bool) -> None:
    """Mutating one client's config takes effect without rebuilding it or cached model adapters."""
    api = (
        AsyncSpitzeisenApi(AsyncSpitzeisenConfig(base_url="https://test.example"))
        if async_
        else SyncSpitzeisenApi(SyncSpitzeisenConfig(base_url="https://test.example"))
    )
    for strict in (False, True, False):
        api.config.strict_response_validation = strict
        if strict:
            with pytest.raises(ValidationError, match="score"):
                api._validate_response({"score": 100}, CheckedModel)
            with pytest.raises(ValidationError, match="score"):
                api._validate_records([{"score": 100}], CheckedModel, "raise")
        else:
            assert api._validate_response({"score": 100}, CheckedModel).score == 100
            assert api._validate_records([{"score": 100}], CheckedModel, "raise") == [CheckedModel(score=100)]
