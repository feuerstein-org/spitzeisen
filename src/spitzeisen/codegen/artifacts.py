"""The small build manifest exchanged by the Java and Pydantic source generators."""

from pydantic import BaseModel, ConfigDict


class ModelArtifact(BaseModel):
    """An operation result's exact Python export and whether Pydantic generates it."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    module: str
    generated: bool


class ArtifactManifest(BaseModel):
    """Build outputs, not a second representation of Smithy semantics."""

    model_config = ConfigDict(extra="forbid", strict=True)
    files: dict[str, bool]
    models: list[ModelArtifact]
    model_names: dict[str, str]
    model_aliases: dict[str, str]
    dependencies: list[str]
    warnings: list[str]
