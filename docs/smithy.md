# Deviations from Smithy

Spitzeisen does not read [Smithy](https://smithy.io/2.0/) models however in the future we intend to support it along with generating the actual clients. For now, you write the operations and response models by hand, so no tooling enforces the Smithy rules below. This page lists where Spitzeisen differs from what the Smithy 2.0 specification asks of a client. Read it if you build an SDK for an API that publishes a Smithy model.

Most of these differences are deliberate. Spitzeisen targets data APIs, where a wrong or partial result is worse than a failed call, so it fails early in places where Smithy asks a client to carry on. The cost is forward compatibility: an SDK can reject something a newer API version considers valid. Note that generally you can _always_ change things to follow Smithy's rules.


| Area | Smithy's rule | Spitzeisen by default | To follow Smithy |
| --- | --- | --- | --- |
| Input constraints | SHOULD NOT enforce | Enforces | `validate_inputs=False` |
| Sending unknown enum values | MUST be possible | Rejects | `validate_inputs=False` |
| Receiving unknown enum values | MUST NOT fail | Keeps them on `UnknownValue` fields, rejects them on plain `Literal` fields | Type the field with `UnknownValue` |
| Output constraints | SHOULD NOT enforce | Enforces constraints the model declares | Declare none on response models |
| Retry safety | Decided by traits | Decided by HTTP method | `retryable` on the operation |
| Timestamps | RFC 3339 or `http-date` | `datetime` raises `TypeError` | Format it in the SDK |

## Inputs are validated before sending

Smithy says constraint traits ["SHOULD NOT be enforced when serializing shapes or when deserializing output"](https://smithy.io/2.0/spec/constraint-traits.html#constraint-trait-enforcement). It also says ["Client implementations MUST provide the capability of sending and receiving unknown enum values"](https://smithy.io/2.0/spec/simple-types.html#enum-is-a-specialization-of-string).

`validate_inputs` defaults to `True`, so `validate_input` rejects any value that does not match its annotation. That includes an enum value the vendor added after the SDK was released. Without the check, a bad argument uses a rate limit token and reaches the vendor, and the response can be an empty result that looks valid.

To follow Smithy, set `validate_inputs=False`. This setting is how Spitzeisen meets the `MUST` for sending. It applies to the whole config, so you cannot let through a single new value.

## Plain `Literal` response fields reject unknown enum values

Smithy says ["Previously generated clients MUST NOT fail when they encounter an unknown enum value"](https://smithy.io/2.0/spec/simple-types.html#enum-is-a-specialization-of-string). For enums and unions, it adds ["These types SHOULD define an unknown variant to account for receiving unknown members"](https://smithy.io/2.0/spec/aggregate-types.html#client-error-correction).

To follow Smithy, an SDK types the field as `Literal[...] | UnknownValue`. The field then keeps a new value as an `UnknownValue`, a `str` subclass that compares and serializes like the string the API sent. Spitzeisen logs one warning per validation call for each new value, with a count. A value of another type, such as a number, still fails validation. Input parameters can stay plain `Literal`s.

To handle a new value, check `isinstance(value, UnknownValue)`.

A new value fails validation on a plain `Literal` or `Enum` field, and on any field when `allow_unknown_enums` is `False`. With `on_validation_error="raise"`, the call raises. With `"skip"`, the default, the record is dropped and only a warning is logged. This can start without any change on the caller's side, so missing rows can be the first sign.

`allow_unknown_enums` defaults to `True`. An SDK that prefers to fail early can redefine the field with a default of `False` in its config class. Callers can still override it.

## Response models enforce declared constraints

The same [enforcement rule](https://smithy.io/2.0/spec/constraint-traits.html#constraint-trait-enforcement) covers deserializing output. `SpitzeisenModel` applies every constraint a model declares, such as `Field(le=10)`. This is deliberate, because finding bad data is the reason to validate responses.

To follow Smithy, leave constraints off response models.

Two related rules apply to required members:

- Smithy's [client error correction](https://smithy.io/2.0/spec/aggregate-types.html#client-error-correction) says clients `MAY` fill in a default when a required member is missing. Spitzeisen does not do this by itself. An SDK can do it for one field by giving the field a Pydantic default, such as `cash_amount: float = 0.0`. A missing member then gets the default, while `null` still fails validation. The default is not in `model_fields_set`, so a consumer can tell a filled-in value from a real one. Use a default only where it is harmless, such as `[]` for a list, because a plausible value hides a broken response.
- For a member marked `@clientOptional`, clients ["MUST treat the member as if it is not required and has no default value"](https://smithy.io/2.0/spec/aggregate-types.html#requiring-members-to-be-optional). Declare these members with a `None` default, such as `frequency: int | None = None`.

## Timestamps must be formatted by the SDK

Smithy serializes timestamps as RFC 3339 in [labels](https://smithy.io/2.0/spec/http-bindings.html#httplabel-trait) and [query strings](https://smithy.io/2.0/spec/http-bindings.html#httpquery-trait), and as `http-date` in [headers](https://smithy.io/2.0/spec/http-bindings.html#httpheader-trait), unless `@timestampFormat` says otherwise.

Spitzeisen cannot know whether a vendor wants a date or a timestamp, or in which timezone, and a wrong guess can shift a value by a day.

To follow Smithy, format the value in the SDK method before serializing it. For example, `value.isoformat()` on a timezone-aware `datetime` produces RFC 3339.
