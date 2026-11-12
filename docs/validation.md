# Validation

Spitzeisen validates data in two places. It checks method arguments before a request is sent, and it checks response records after they arrive. Three settings control this, and all three exist on `SpitzeisenConfig` and `SyncSpitzeisenConfig`.

| Setting | Default | What it controls | Per-call override |
| --- | --- | --- | --- |
| `validate_inputs` | `True` | Whether `validate_input` checks a value | No |
| `on_validation_error` | `"skip"` | What happens to a response record that fails validation | Yes, the `mode` argument |
| `allow_unknown_enums` | `True` | Whether `UnknownValue` fields accept a new enum value | No |

[Deviations from Smithy](smithy.md) explains how these defaults compare with the Smithy specification.

## Input validation

`validate_input(value, annotation)` checks one value with Pydantic strict mode and returns it. A failure raises `pydantic.ValidationError` before the request is sent, so it uses no rate limit token. `on_validation_error` does not apply to inputs.

```python
params = serialize_query_map(
    {
        "units": self.validate_input(units, Units),
        "lat": self.validate_input(latitude, float),
    },
)
```

Spitzeisen checks only the values an SDK passes to `validate_input`. `None` always passes, because it means the argument was omitted. With `validate_inputs=False`, `validate_input` returns the value unchanged.

Use the returned value, because strict mode can convert it:

| Annotation | Accepts | Rejects |
| --- | --- | --- |
| `date` | `date` | `"2024-01-01"`, `datetime` |
| `float` | `1.5`, and `1`, which it returns as `1.0` | `"1.5"`, `True` |
| `int` | `1` | `"1"`, `1.0`, `True` |
| `Literal["a", "b"]` | `"a"`, and a `StrEnum` member with that value, which it returns as `"a"` | Any other value |
| `list[str]` | A list | A tuple, a set, `"abc"` |
| `Sequence[str]` | A list, a tuple | `"abc"` |

Annotate list arguments as `Sequence[...]`. This accepts tuples and still rejects a bare string, which a comma-joined filter would otherwise send as `a,b,c`.

These checks always run, whatever `validate_inputs` is set to:

- `serialize_path_param`, `serialize_query_param`, `serialize_query_map`, and `build_header_params` raise `TypeError` on a `datetime` or an unsupported type.
- `serialize_path_param` raises `ValueError` on an empty value.
- `resolve_page_size`, `get_records`, and `iter_records` raise `ValueError` if `max_results` is below 1.

TODO: Add a timestamp parameter?

## Response validation modes

`validate_record` and `validate_records` turn raw JSON objects into models. Pass `mode` to choose the policy for one call. `mode=None` uses `on_validation_error` from the config, and any other value raises `ValueError`.

| Method | `mode="raise"` | `mode="skip"` |
| --- | --- | --- |
| `validate_record` | Raises `ValidationError` | Logs a warning and returns `None` |
| `validate_records` | Raises one `ValidationError` listing every bad record | Logs one warning per bad record and returns the valid records in order |

- **Error locations.** With `validate_records`, the first item of each error's `loc` is the record's index in the list.
- **Warning.** Each dropped record logs a `dropping_invalid_record` warning with `model` and `errors`.
- **Cost of skip.** `validate_records` validates the whole list first. It validates records one at a time only if that fails, so a valid batch costs the same in both modes.
- **Return type.** `validate_record` with `mode="raise"` returns the model. Other modes return `Model | None`. The [forecast endpoint](../examples/weather_sdk/_async/api/forecast.py) shows how to pass this typing on with overloads.

### What fails a record

Response models use Pydantic's default mode, not strict mode. `"4"` becomes the integer `4`, and an ISO string becomes a `date`.

A record fails on a missing required field, a value that cannot be converted, a violated constraint, or a value outside a plain `Literal` or `Enum`. `SpitzeisenModel` ignores unknown members, so a new field never fails a record.

### What the modes do not cover

`on_validation_error` applies only to `pydantic.ValidationError` from these two methods. HTTP errors, transport errors, invalid JSON, and `ResponseShapeError` raise in both modes. `pydantic.ValidationError` is not a `SpitzeisenError`, so `except SpitzeisenError` does not catch it.

An SDK method that uses both `not_found_ok=True` and `skip` can return `None` for two reasons: an HTTP 404 or an invalid record. Use `raise` to tell them apart.

Helpers that return raw data, such as `get_records` and `get_object`, do no model validation. They still raise `ResponseShapeError` on an unexpected envelope.

### When validation runs

`get_records` collects every page before you validate the result. With `raise`, a bad record on the first page fails only after the last page is fetched. To fail sooner, validate each record from `iter_records`, as the [inventory example](../examples/inventory_sdk.py) does.

`max_results` counts records before validation, so `skip` can return fewer records than `max_results` even when more exist.

## Unknown enum values

Type a response field as `Literal[...] | UnknownValue`, with the `Literal` first:

```python
class Ticker(SpitzeisenModel):
    market: Literal["stocks", "crypto"] | UnknownValue
```

A known value stays a plain `str`. `allow_unknown_enums` decides what happens to a new string:

| `allow_unknown_enums` | `mode="raise"` | `mode="skip"` |
| --- | --- | --- |
| `True` | Kept as `UnknownValue` | Kept as `UnknownValue` |
| `False` | Raises `ValidationError` | Drops the record and logs a warning |

`UnknownValue` is a `str` subclass holding what the API sent. To handle it just check the type:

```python
if isinstance(ticker.market, UnknownValue):
    ...  # a value this SDK version does not know
```

- **Other types.** A non-string value fails validation at either setting.
- **Plain fields.** The setting affects only fields typed with `UnknownValue`. A plain `Literal` field rejects a new value at either setting.
- **Warning.** A kept value logs an `unknown_enum_value` warning with `field` and `value`. Each pair logs once per process, and Spitzeisen remembers the last 1,024 pairs. A long-running process does not warn about a remembered pair again to reduce log size.
- **Errors.** A rejected value produces two entries in `errors()`, one per union member. The `unknown_enum_value` entry names the value.
- **Direct validation.** `Model.model_validate(...)` called without a client has no config to read, so it rejects an unknown Enum value value. To apply a client's settings to stored data, such as raw responses saved earlier, validate it through the client with `validate_record` or `validate_records`. That also logs the counts.

## Change the defaults in an SDK

Redefine the field in the SDK's config class. Callers can still pass another value.

```python
class MyApiConfig(SpitzeisenConfig):
    base_url: str = "https://api.example.com"
    on_validation_error: ValidationMode = "raise"
    allow_unknown_enums: bool = False
```

To let callers choose the mode per call, accept `on_validation_error: ValidationMode | None = None` and pass it as `mode`. The [current weather endpoint](../examples/weather_sdk/_async/api/current_weather.py) does this.
