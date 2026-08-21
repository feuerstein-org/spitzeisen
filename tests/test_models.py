"""Shared response-model policy."""

import pytest
from pydantic import Field, ValidationError

from spitzeisen import SpitzeisenModel


class ExampleModel(SpitzeisenModel):
    """A generated-model stand-in."""

    value: int


def test_spitzeisen_model_keeps_pydantics_default_extra_handling() -> None:
    """The shared base currently ignores unknown fields like an unconfigured BaseModel."""
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
