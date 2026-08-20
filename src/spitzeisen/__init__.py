"""
spitzeisen: a neutral client framework for rate-limited REST APIs.

Provides the machinery every API client re-implements — session lifecycle, retries with
backoff, rate limiting, pagination, batch validation. httpx2 does the HTTP, confined to the
request core so that no httpx2 type appears in a generated client's endpoint signatures.
Nothing here knows or cares what domain the API serves.

Endpoint classes subclass `AsyncSpitzeisenApi` (or `SyncSpitzeisenApi` for the blocking surface)
and describe calls with a `SpitzeisenEndpointSpec`.

Naming: anything tied to one surface carries an `Async`/`Sync` prefix, and always a prefix —
`AsyncSpitzeisenApi`/`SyncSpitzeisenApi`, `AsyncSpitzeisenConfig`/`SyncSpitzeisenConfig`,
`AsyncLimiter`/`SyncLimiter`. An unmarked name is shared by both surfaces, which most of
spitzeisen is: `SpitzeisenEndpointSpec`, the auth and pagination strategies, every exception.
Generated clients follow the same rule. The prefix is
load-bearing rather than cosmetic — unasync converts `AsyncFoo` to `SyncFoo` on its own, so a
type named this way needs no entry in `build_sync.py`'s replacement table.
"""

from spitzeisen._async.config import AsyncSpitzeisenConfig, ValidationMode
from spitzeisen._async.core import AsyncSpitzeisenApi
from spitzeisen._sync.config import SyncSpitzeisenConfig
from spitzeisen._sync.core import SyncSpitzeisenApi
from spitzeisen.auth import AuthStrategy, BearerHeader, HeaderKey, NoAuth, QueryParamAuth
from spitzeisen.concurrency import gather_bounded, map_bounded
from spitzeisen.endpoints import SpitzeisenEndpointSpec
from spitzeisen.exceptions import (
    AuthenticationError,
    HTTPError,
    MaxRetriesExceededError,
    NotFoundError,
    ResponseShapeError,
    ServerError,
    SpitzeisenError,
    TransportError,
    http_error_from_status,
)
from spitzeisen.limits import (
    AsyncLimiter,
    NoLimit,
    SyncLimiter,
    async_single_bucket,
    sync_single_bucket,
)
from spitzeisen.models import SpitzeisenModel
from spitzeisen.pagination import (
    NoPagination,
    PageNumber,
    PaginationStrategy,
    extract_records,
)
from spitzeisen.params import (
    build_header_params,
    coerce_choice,
    coerce_choices,
    coerce_date,
    coerce_sort,
    require_value,
    serialize_query_param,
)

__all__ = (
    "AsyncLimiter",
    "AsyncSpitzeisenApi",
    "AsyncSpitzeisenConfig",
    "AuthStrategy",
    "AuthenticationError",
    "BearerHeader",
    "HTTPError",
    "HeaderKey",
    "MaxRetriesExceededError",
    "NoAuth",
    "NoLimit",
    "NoPagination",
    "NotFoundError",
    "PageNumber",
    "PaginationStrategy",
    "QueryParamAuth",
    "ResponseShapeError",
    "ServerError",
    "SpitzeisenEndpointSpec",
    "SpitzeisenError",
    "SpitzeisenModel",
    "SyncLimiter",
    "SyncSpitzeisenApi",
    "SyncSpitzeisenConfig",
    "TransportError",
    "ValidationMode",
    "async_single_bucket",
    "build_header_params",
    "coerce_choice",
    "coerce_choices",
    "coerce_date",
    "coerce_sort",
    "extract_records",
    "gather_bounded",
    "http_error_from_status",
    "map_bounded",
    "require_value",
    "serialize_query_param",
    "sync_single_bucket",
)
