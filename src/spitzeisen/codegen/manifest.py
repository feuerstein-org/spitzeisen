"""
The manifest: what an OpenAPI document cannot tell you.

A spec describes an API's shape — paths, parameters, response schemas. It says nothing about
what a call costs against a rate limit, how a collection paginates, or what the client should
name the method. There is no standard for any of that: pagination has been an open request on
the OpenAPI specification since 2019, and rate limiting sits in discussion classed as
"server-side policy", which the specification deliberately does not model.

So it lives here instead, keyed by endpoint rather than by JSON pointer, and **the spec is
optional**: an API that publishes no OpenAPI document can declare its parameters inline and
generate exactly the same client. That is deliberate — most APIs publish no spec.

Rate limits appear in no OpenAPI document, so the manifest records the scalar cost of each
endpoint. The client config owns the actual allowance, which may only be known at runtime.
"""

import keyword
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Shape = Literal["collection", "single"]
# What a not-found response means for this endpoint. "raise" is the default because a 404 is
# ambiguous on the wire: it says both "no such record" and "no such path", and only the vendor's
# documentation distinguishes them. "empty" declares the first reading, which is worth stating
# per endpoint rather than inferring from `shape` — a single-resource lookup can just as easily
# want the exception, and a collection can want an empty list.
NotFound = Literal["raise", "empty"]
PaginationStyle = Literal["none", "page_number"]
# "suffix": direction is a dot-separated suffix on the sort field.
# "param": a separate query parameter carries the direction.
# "none": sorting receives no special treatment; matching parameters remain ordinary inputs.
SortStyle = Literal["suffix", "param", "none"]
# "comma_list" joins a list of free-form values; "comma_choice_list" also validates each
# against a Literal. Vendors use comma-separated lists for both open and closed value sets.
CoercionStyle = Literal["plain", "date", "comma_list", "comma_choice_list", "choice"]
QueryStyle = Literal["form", "spaceDelimited", "pipeDelimited"]
# A declared parameter has no OpenAPI document to supply its location, so the manifest carries
# it explicitly. Path parameters remain part of the endpoint path template instead.
ParamLocation = Literal["query", "header"]


class Param(BaseModel):
    """
    How one request parameter is presented to callers.

    Separates Python argument names from wire parameter names: a wire parameter named
    `adjustment_type.any_of` can surface as `adjustment_types: list[...]`.

    A `coerce_function` names a client-owned function from `<package>.params`, allowing a manifest to
    retain vendor-specific parameter semantics without replacing the generated endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    # Used by `declared_params` when there is no OpenAPI document. A spec remains authoritative
    # about where its own parameters are sent.
    location: ParamLocation = "query"
    # OpenAPI's query-string representation. These two fields override a supplied spec or
    # describe a declared query parameter when no spec exists.
    style: QueryStyle | None = None
    explode: bool | None = None
    # Spitzeisen's input coercion behavior, deliberately distinct from OpenAPI's `style`.
    coercion_style: CoercionStyle = "plain"
    # A client-owned function imported from `<package>.params`. It receives the submitted
    # value plus `param_name` and `literal` keyword arguments, and returns a value suitable
    # for serialize_query_param. This is the escape hatch for vendor-specific coercion.
    coerce_function: str | None = None
    literal: str | None = None
    type: str | None = None
    description: str | None = None
    # OpenAPI-required parameters remain required. Without a spec, True makes a declared
    # parameter required. A required parameter has no Python default and is always sent.
    required: bool | None = None
    # A Python literal. A parameter with a default is always sent, so it stops being
    # optional in the signature: `active: bool = True` rather than `bool | None = None`.
    default: str | None = None

    @model_validator(mode="after")
    def check_literal(self) -> Self:
        """Check that a parameter's validation and custom-function settings are unambiguous."""
        if self.coercion_style in {"choice", "comma_choice_list"} and not self.literal:
            msg = (
                f"coercion_style={self.coercion_style!r} requires `literal` naming the Literal type to validate against"
            )
            raise ValueError(msg)
        if self.coerce_function and self.coercion_style != "plain":
            msg = "`coerce_function` cannot be combined with a non-plain `coercion_style`"
            raise ValueError(msg)
        if self.coerce_function and (
            not self.coerce_function.isidentifier() or keyword.iskeyword(self.coerce_function)
        ):
            msg = "`coerce_function` must be a valid Python identifier imported from the client package's params module"
            raise ValueError(msg)
        if self.required and self.default is not None:
            msg = "a required parameter cannot also declare a default"
            raise ValueError(msg)
        return self


class Endpoint(BaseModel):
    """One endpoint: where it lives, what it costs, how it paginates, what to call it."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    method_name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    summary: str = ""
    docs_url: str | None = None

    # Models are generated from the response schema unless the client hand-writes one.
    # Reshaping a payload — flattening a nested object, renaming single-letter wire keys,
    # parsing an epoch timestamp — is beyond what a schema can express, and a hand-written
    # model is better than a generated one plus a pile of overrides.
    generate_model: bool = True

    # "collection" walks a paginated list and validates records into a list of models;
    # "single" fetches one object. This is about the *return shape* only — how a not-found
    # response is treated is `not_found`, which is deliberately independent of it.
    shape: Shape = "collection"
    # See `NotFound`. Only meaningful for `shape: single` today; checked below.
    not_found: NotFound = "raise"
    # Path placeholders, mapped to the argument name callers use: {"id": "ticker_id"}.
    path_params: dict[str, str] = Field(default_factory=dict[str, str])

    cost: float = 1.0
    pagination: PaginationStyle = "none"
    results_key: str | None = None
    # The wire name is explicit because OpenAPI does not identify pagination parameters:
    # vendors use `limit`, `page_size`, `per_page`, and many other names.
    page_size_param: str | None = Field(default=None, min_length=1)
    max_page_size: int | None = Field(default=None, gt=0)
    sort_style: SortStyle = "none"
    # Sorting has no OpenAPI marker, so the manifest identifies its vendor wire names. Public
    # generated arguments remain `sort` and `order` regardless of those names.
    sort_param: str | None = Field(default=None, min_length=1)
    order_param: str | None = Field(default=None, min_length=1)
    # Optional client-owned Literal aliases imported from `<package>.models`. OpenAPI enums are
    # used when present, while these fields cover manifest-only APIs and incomplete documents.
    sort_literal: str | None = None
    order_literal: str | None = None
    # Defaults come from OpenAPI when published. A suffix-style dotted sort default supplies
    # both values; these fields fill in or override what the document does not say.
    sort_default: str | None = None
    order_default: str | None = None

    # Rename a model's fields: {"Bar.o": "open"} makes the wire name the pydantic alias.
    aliases: dict[str, str] = Field(default_factory=dict[str, str])
    # Replace a field's type with any importable annotated type, which is how a vendor's
    # per-field oddity — epoch milliseconds, an integer date, a comma decimal — is declared
    # rather than hand-written: {"Bar.timestamp": "massive_api.types.MassiveEpochMillis"}.
    # The type lives in the client library; spitzeisen only carries the pointer.
    type_overrides: dict[str, str] = Field(default_factory=dict[str, str])

    exclude_params: list[str] = Field(default_factory=list[str])
    params: dict[str, Param] = Field(default_factory=dict[str, Param])
    # Declared inline when there is no OpenAPI document to read them from.
    declared_params: dict[str, Param] = Field(default_factory=dict[str, Param])

    @model_validator(mode="after")
    def check_endpoint_settings(self) -> Self:
        """
        Reject endpoint settings whose combinations cannot be generated faithfully.

        Absent-as-empty-list is a coherent thing to want — a bulk walk over symbols outside a
        vendor's coverage reads better as `[]` than as an exception — but the pagination path
        would have to stop on the first absent page, and no vendor has asked for it. Failing
        here beats accepting a key that silently does nothing.
        """
        if self.not_found == "empty" and self.shape == "collection":
            msg = (
                "not_found='empty' is only supported for shape='single'. A collection would need "
                "`_paginate` to treat an absent page as the end of the walk, which is unimplemented."
            )
            raise ValueError(msg)
        if self.max_page_size is not None and self.page_size_param is None:
            msg = "`max_page_size` requires `page_size_param` naming its query parameter"
            raise ValueError(msg)
        self._check_sorting_settings()
        return self

    def _check_sorting_settings(self) -> None:
        """Reject missing, contradictory, or non-importable sorting metadata."""
        if self.sort_style == "none":
            sort_settings = {
                "sort_param": self.sort_param,
                "order_param": self.order_param,
                "sort_literal": self.sort_literal,
                "order_literal": self.order_literal,
                "sort_default": self.sort_default,
                "order_default": self.order_default,
            }
            configured = [name for name, value in sort_settings.items() if value is not None]
            if configured:
                msg = f"sort_style='none' cannot be combined with sorting settings {configured}"
                raise ValueError(msg)
        elif self.sort_param is None:
            msg = f"sort_style={self.sort_style!r} requires `sort_param` naming its query parameter"
            raise ValueError(msg)
        elif self.sort_style == "suffix" and self.order_param is not None:
            msg = "sort_style='suffix' carries direction in `sort_param` and cannot declare `order_param`"
            raise ValueError(msg)
        elif self.sort_style == "param" and self.order_param is None:
            msg = "sort_style='param' requires `order_param` naming its query parameter"
            raise ValueError(msg)
        elif self.sort_param == self.order_param:
            msg = "`sort_param` and `order_param` must name different query parameters"
            raise ValueError(msg)
        for field_name, literal in (("sort_literal", self.sort_literal), ("order_literal", self.order_literal)):
            if literal and (not literal.isidentifier() or keyword.iskeyword(literal)):
                msg = f"`{field_name}` must be a valid Python identifier imported from the client models module"
                raise ValueError(msg)

    @property
    def class_name(self) -> str:
        """`splits` -> `SplitsApi`."""
        return "".join(part.title() for part in self.key.split("_")) + "Api"

    @property
    def accessor(self) -> str:
        """`splits` -> `splits_api`, the property the client exposes it under."""
        return f"{self.key}_api"

    @property
    def const_name(self) -> str:
        """`splits` -> `SPLITS_ENDPOINT`."""
        return f"{self.key.upper()}_ENDPOINT"

    key: str = ""  # filled in by Manifest; endpoints are a mapping in the file


class Manifest(BaseModel):
    """A vendor's complete generation input."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    package: str = Field(min_length=1)
    client_name: str = Field(min_length=1)

    endpoints: dict[str, Endpoint]

    @model_validator(mode="after")
    def wire_endpoint_keys(self) -> Self:
        """Stamp each endpoint with its manifest key."""
        for key, endpoint in self.endpoints.items():
            endpoint.key = key
        return self

    def model_aliases(self) -> dict[str, str]:
        """Every endpoint's field renames, merged for the model generator."""
        return {key: value for ep in self.endpoints.values() for key, value in ep.aliases.items()}

    def model_type_overrides(self) -> dict[str, str]:
        """Every endpoint's custom field types, merged for the model generator."""
        return {key: value for ep in self.endpoints.values() for key, value in ep.type_overrides.items()}

    def resolve(self, spec: dict[str, Any] | None) -> None:
        """
        Check every endpoint against an OpenAPI document, when one is supplied.

        Failing loudly here is the point: if the vendor moves or renames a path, generation
        stops rather than quietly emitting a client for an endpoint that no longer exists.
        """
        if spec is None:
            return
        paths = spec.get("paths", {})
        for key, endpoint in self.endpoints.items():
            if endpoint.path not in paths:
                msg = (
                    f"endpoint {key!r} declares path {endpoint.path!r}, which is not in the "
                    f"OpenAPI document. Did the vendor move it?"
                )
                raise KeyError(msg)
            if "get" not in paths[endpoint.path]:
                msg = f"endpoint {key!r} declares path {endpoint.path!r}, which has no GET operation"
                raise KeyError(msg)
