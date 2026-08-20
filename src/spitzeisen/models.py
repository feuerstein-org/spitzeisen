"""Shared model policy for generated SDK response types."""

from pydantic import BaseModel


class SpitzeisenModel(BaseModel):
    """
    Base for generated response models.

    Essentially the default Pydantic BaseModel
    """
