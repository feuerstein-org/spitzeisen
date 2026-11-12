"""Shared input-validation and response-model policy."""

from typing import TYPE_CHECKING, Literal, assert_type, cast

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator
from structlog.testing import capture_logs

from spitzeisen import (
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenModel,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
    UnknownValue,
    ValidationMode,
)

if TYPE_CHECKING:
    from spitzeisen.pagination import JsonObject


class ExampleModel(SpitzeisenModel):
    """An SDK response model."""

    value: int


def test_spitzeisen_model_explicitly_ignores_unknown_response_members() -> None:
    """New response members remain forward-compatible while known fields are validated."""
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


class ResponseDetails(BaseModel):
    """Nested response fields still receive ordinary Pydantic parsing."""

    attempts: int


class ResponseRecord(BaseModel):
    """A compatible response model accepts new enum strings and added fields."""

    id: str
    state: str
    score: int
    details: ResponseDetails


class ResponseContract(BaseModel):
    """An explicitly selected contract adds the SDK developer's stricter checks."""

    id: str
    state: Literal["ready", "pending"]
    score: int = Field(ge=0, le=10)
    details: ResponseDetails

    @field_validator("id")
    @classmethod
    def require_id(cls, value: str) -> str:
        """Reject an empty identifier using an ordinary custom validator."""
        if not value:
            msg = "id must not be empty"
            raise ValueError(msg)
        return value


@pytest.fixture(params=["async", "sync"])
def api(request: pytest.FixtureRequest) -> SpitzeisenApi | SyncSpitzeisenApi:
    """Exercise validation on both client surfaces without making requests."""
    if request.param == "async":
        return SpitzeisenApi(SpitzeisenConfig(base_url="https://test.example"))
    return SyncSpitzeisenApi(SyncSpitzeisenConfig(base_url="https://test.example"))


def test_validate_input_is_strict_by_default_and_can_be_disabled(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Input validation rejects coercion and allows omitted values until the config turns it off."""
    assert api.validate_input(1.5, float) == 1.5
    assert api.validate_input(None, float) is None
    with pytest.raises(ValidationError):
        api.validate_input("1.5", float)
    api.config.validate_inputs = False
    assert api.validate_input("1.5", float) == "1.5"


class OpenEnumModel(SpitzeisenModel):
    """A response model that can keep enum values the SDK does not know yet."""

    kind: Literal["a", "b"] | UnknownValue


def test_disallowing_unknown_enums_follows_the_validation_policy(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """With allow_unknown_enums off, a new value follows the raise or skip policy like any bad field."""
    api.config.allow_unknown_enums = False
    with pytest.raises(ValidationError, match="allow_unknown_enums"):
        api.validate_record({"kind": "c"}, OpenEnumModel, mode="raise")
    with pytest.raises(ValidationError, match="allow_unknown_enums"):
        api.validate_records([{"kind": "c"}], OpenEnumModel, mode="raise")
    assert api.validate_record({"kind": "c"}, OpenEnumModel, mode="skip") is None
    kept = api.validate_records([{"kind": "a"}, {"kind": "c"}], OpenEnumModel, mode="skip")
    assert [item.kind for item in kept] == ["a"]


def test_unknown_enum_value_is_kept_and_logged_by_default(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """A new string becomes an UnknownValue holding what the API sent, and known values stay plain."""
    with capture_logs() as known_logs:
        known = api.validate_record({"kind": "a"}, OpenEnumModel, mode="raise")
    with capture_logs() as unknown_logs:
        unknown = api.validate_record({"kind": "c"}, OpenEnumModel, mode="raise")
    listed = api.validate_records([{"kind": "a"}, {"kind": "c"}, {"kind": 5}], OpenEnumModel, mode="skip")

    # Confirm nothing was logged
    assert not known_logs
    assert unknown_logs == [
        {
            "event": "unknown_enum_value",
            "log_level": "warning",
            "model": "OpenEnumModel",
            "field": "kind",
            "value": "c",
            "count": 1,
        }
    ]
    assert type(known.kind) is str
    assert isinstance(unknown.kind, UnknownValue)
    assert unknown.kind == "c"
    assert unknown.model_dump() == {"kind": "c"}
    assert unknown.model_dump_json() == '{"kind":"c"}'
    assert [item.kind for item in listed] == ["a", "c"]


class OpenEnumWithIdModel(OpenEnumModel):
    """A response model where a record with a new enum value can still fail on another field."""

    id: int


def test_unknown_enum_values_log_one_line_per_call_with_a_count(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Every call reports each new value once with a count, including after the skip fallback's second pass."""
    valid: list[JsonObject] = [{"kind": "c"}, {"kind": "c"}, {"kind": "d"}]

    with capture_logs() as logs:
        api.validate_records(valid, OpenEnumModel, mode="raise")
        api.validate_records([*valid, {"kind": 5}], OpenEnumModel, mode="skip")

    counts = [(log["value"], log["count"]) for log in logs if log["event"] == "unknown_enum_value"]

    # Logged twice since we validated twice
    assert counts == [("c", 2), ("d", 1), ("c", 2), ("d", 1)]


def test_dropped_records_do_not_count_as_unknown_enum_values(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Only records the caller receives count, in both the single-record and list paths."""
    kept: JsonObject = {"kind": "c", "id": 1}
    dropped: JsonObject = {"kind": "c", "id": "bad"}

    with capture_logs() as logs:
        api.validate_record(dropped, OpenEnumWithIdModel, mode="skip")
        api.validate_records([kept, dropped], OpenEnumWithIdModel, mode="skip")

    counts = [(log["value"], log["count"]) for log in logs if log["event"] == "unknown_enum_value"]
    assert counts == [("c", 1)]


def test_unknown_value_is_rejected_outside_a_client() -> None:
    """Without a client there is no setting to read, so a new value fails."""
    with pytest.raises(ValidationError, match="on the client"):
        OpenEnumModel.model_validate({"kind": "c"})
    with pytest.raises(ValidationError, match="on the client"):
        OpenEnumModel.model_validate({"kind": "c"}, context={"tenant": "acme"})
    assert OpenEnumModel.model_validate({"kind": "a"}).kind == "a"


def test_response_models_accept_additions_and_parse_nested_values(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """The compatible model accepts vendor additions while parsing declared fields."""
    record: JsonObject = {
        "id": "a",
        "state": "new_vendor_state",
        "score": "100",
        "details": {"attempts": "3", "new_detail": True},
        "new_field": True,
    }

    parsed = api.validate_record(record, ResponseRecord, mode="raise")

    assert parsed == ResponseRecord(id="a", state="new_vendor_state", score=100, details=ResponseDetails(attempts=3))
    assert api.validate_records([record], ResponseRecord, "raise") == [parsed]
    assert not hasattr(parsed, "new_field")
    assert not hasattr(parsed.details, "new_detail")


def test_response_models_still_reject_missing_fields_and_bad_types(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Compatibility does not bypass required members or nested field types."""
    record: JsonObject = {"id": "a", "state": "ready", "score": 5, "details": {"attempts": 1}}
    missing = {name: value for name, value in record.items() if name != "state"}
    malformed: JsonObject = {**record, "details": {"attempts": "not a number"}}
    for invalid, location in [(missing, ("state",)), (malformed, ("details", "attempts"))]:
        with pytest.raises(ValidationError) as error:
            api.validate_record(invalid, ResponseRecord, mode="raise")
        assert error.value.errors()[0]["loc"] == location

    with pytest.raises(ValidationError):
        api.validate_records([record, missing, malformed], ResponseRecord, "raise")
    assert api.config.on_validation_error == "skip"
    assert api.validate_records([record, missing, malformed], ResponseRecord) == [
        api.validate_record(record, ResponseRecord, mode="raise")
    ]


def test_explicit_contract_honors_constraints_choices_and_custom_validators(
    api: SpitzeisenApi | SyncSpitzeisenApi,
) -> None:
    """Selecting a contract applies all its rules in single, raise, and skip paths."""
    record: JsonObject = {"id": "a", "state": "ready", "score": 5, "details": {"attempts": 1}}
    invalid_records: list[JsonObject] = [
        {**record, "score": 100},
        {**record, "state": "new_vendor_state"},
        {**record, "id": ""},
    ]
    assert len(api.validate_records(invalid_records, ResponseRecord, "raise")) == len(invalid_records)
    for invalid in invalid_records:
        with pytest.raises(ValidationError):
            api.validate_record(invalid, ResponseContract, mode="raise")

    with pytest.raises(ValidationError) as error:
        api.validate_records([record, *invalid_records], ResponseContract, "raise")
    assert {item["loc"] for item in error.value.errors()} == {(1, "score"), (2, "state"), (3, "id")}
    assert api.validate_records([record, *invalid_records], ResponseContract, "skip") == [
        api.validate_record(record, ResponseContract, mode="raise")
    ]


@pytest.mark.parametrize("config_mode", ["raise", "skip"])
@pytest.mark.parametrize("override", [None, "raise", "skip"])
def test_validate_record_uses_config_or_override(
    api: SpitzeisenApi | SyncSpitzeisenApi, config_mode: ValidationMode, override: ValidationMode | None
) -> None:
    """Nested failures follow the selected policy and preserve their original field paths."""
    api.config.on_validation_error = config_mode
    record: JsonObject = {"id": "a", "state": "ready", "score": 5, "details": {"attempts": "invalid"}}
    with capture_logs() as logs:
        if (override or config_mode) == "raise":
            with pytest.raises(ValidationError) as error:
                api.validate_record(record, ResponseRecord, mode=override)
            assert error.value.errors()[0]["loc"] == ("details", "attempts")
            assert logs == []
        else:
            assert api.validate_record(record, ResponseRecord, mode=override) is None
            assert len(logs) == 1
            assert logs[0]["event"] == "dropping_invalid_record"
            assert logs[0]["model"] == "ResponseRecord"
            assert logs[0]["errors"][0]["loc"] == ("details", "attempts")
    assert api.config.on_validation_error == config_mode


@pytest.mark.parametrize("value", [1, -1])
def test_validate_record_runs_custom_validation_once(api: SpitzeisenApi | SyncSpitzeisenApi, value: int) -> None:
    """Successful and skipped objects each run their model validators only once."""
    calls: list[int] = []

    class CountedRecord(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def validate_value(cls, value: int) -> int:
            calls.append(value)
            if value < 0:
                msg = "negative value"
                raise ValueError(msg)
            return value

    parsed = api.validate_record({"value": value}, CountedRecord, mode="skip")
    assert calls == [value]
    if value < 0:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed.value == value


def test_validation_rejects_invalid_mode(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Unsupported modes fail even when the response itself would validate."""
    with pytest.raises(ValueError, match="Invalid validation mode"):
        api.validate_record({"value": 1}, ExampleModel, mode=cast("ValidationMode", "ignore"))
    with pytest.raises(ValueError, match="Invalid validation mode"):
        api.validate_records([{"value": 1}], ExampleModel, mode=cast("ValidationMode", "ignore"))


def test_validate_record_only_skips_validation_errors(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Programming errors in model validators are not treated as bad response records."""

    class BrokenRecord(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def broken_validator(cls, _value: int) -> int:
            msg = "broken validator"
            raise TypeError(msg)

    with pytest.raises(TypeError, match="broken validator"):
        api.validate_record({"value": 1}, BrokenRecord, mode="skip")


def test_validate_record_return_types(api: SpitzeisenApi | SyncSpitzeisenApi) -> None:
    """Explicit raise guarantees a model; config-driven and skip modes may return None."""
    record: JsonObject = {"value": 1}
    assert_type(api.validate_record(record, ExampleModel, mode="raise"), ExampleModel)
    assert_type(api.validate_record(record, ExampleModel), ExampleModel | None)
    assert_type(api.validate_record(record, ExampleModel, mode="skip"), ExampleModel | None)
