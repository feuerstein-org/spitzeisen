"""Model-backend finalization preserves source documentation and Pydantic semantics."""

from typing import Any

import pytest
from pydantic import ValidationError

from spitzeisen.codegen.model_validation import runtime_response_constraints


def test_runtime_annotations_preserve_aliases_defaults_and_documentation() -> None:
    """Aliases emitted before classes, root enums, and assigned Fields all respect the policy."""
    source = '''# Backend provenance: température
from typing import Annotated, Literal
from typing_extensions import TypeAliasType
from pydantic import Field, RootModel
from spitzeisen import SpitzeisenModel

Code = TypeAliasType("Code", Annotated[str, Field(pattern="^[a-z]+$")])

class State(RootModel[Literal["known", None]]):
    root: Literal["known", None]

class Result(SpitzeisenModel):
    """Documentation with accents: température."""
    count: int = Field(default=2, alias="quantité", ge=0)  # Wire alias
    """A documented field."""
    codes: list[Code]
'''
    converted = runtime_response_constraints(source)
    assert converted.startswith("# Backend provenance: température\n")
    assert '"""Documentation with accents: température."""' in converted
    assert '"""A documented field."""' in converted
    assert "# Wire alias" in converted
    namespace: dict[str, Any] = {}
    exec(converted, namespace)  # noqa: S102 - execute the controlled generated fixture
    model = namespace["Result"]
    assert model.model_validate({"codes": ["AB"]}).count == 2
    assert model.model_validate({"quantité": -1, "codes": []}).count == -1
    context = {"strict_response_validation": True}
    with pytest.raises(ValidationError, match="quantité"):
        model.model_validate({"quantité": -1, "codes": []}, context=context)
    with pytest.raises(ValidationError, match="codes"):
        model.model_validate({"codes": ["AB"]}, context=context)
    state = namespace["State"]
    assert state.model_validate(None, context=context).root is None
    assert state.model_validate("future").root == "future"
    with pytest.raises(ValidationError):
        state.model_validate("future", context=context)
