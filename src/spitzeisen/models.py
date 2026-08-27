"""Shared model policy for generated SDK response types."""

from pydantic import BaseModel, ConfigDict


class SpitzeisenModel(BaseModel):
    """
    Base for generated response models.

    Unknown response fields are deliberately ignored. Smithy clients must remain compatible
    when a service adds an output member, while declared fields and constraints are still
    validated by Pydantic.
    """

    model_config = ConfigDict(extra="ignore")
