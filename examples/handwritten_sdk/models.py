"""A deliberately small selection of handwritten vendor response types."""

from datetime import date

from pydantic import AliasPath, Field, field_validator

from spitzeisen import SpitzeisenModel


class Ticker(SpitzeisenModel):
    """Reference record; unrecognized vendor response fields remain compatible."""

    ticker: str
    active: bool
    name: str | None = None
    market: str | None = None

    @field_validator("ticker")
    @classmethod
    def nonempty_ticker(cls, value: str) -> str:
        """Keep domain validation in the SDK, including for shared batch validation."""
        if not value.strip():
            msg = "ticker must not be blank"
            raise ValueError(msg)
        return value


class TickerOverview(Ticker):
    """Detailed reference record with an SDK-owned nested field alias."""

    description: str | None = None
    list_date: date | None = None
    city: str | None = Field(default=None, validation_alias=AliasPath("address", "city"))


class Split(SpitzeisenModel):
    """A split with Pydantic date parsing and vendor-owned field names."""

    ticker: str
    execution_date: date
    adjustment_type: str
    split_from: float
    split_to: float
