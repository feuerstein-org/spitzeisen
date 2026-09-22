"""Shared model policy for SDK response types."""

from pydantic import BaseModel, ConfigDict

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
