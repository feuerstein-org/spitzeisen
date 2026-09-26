"""
spitzeisen: a neutral client framework for rate-limited REST APIs.

Provides the machinery every API client re-implements — session lifecycle, retries with
backoff, rate limiting, pagination, batch validation. httpx2 does the HTTP, confined to the
request core so that no httpx2 type appears in a client's operation signatures.
Nothing here knows or cares what domain the API serves.

Operation classes subclass `SpitzeisenApi` (or `SyncSpitzeisenApi` for the
blocking surface) and describe calls with a `SpitzeisenOperationSpec`.

Public async clients and helpers use unprefixed names, blocking counterparts use `Sync` or `sync_`.
Shared models, strategies, and exceptions use the same names on both surfaces.
"""

from spitzeisen._async.config import SpitzeisenConfig, ValidationMode
from spitzeisen._async.core import SpitzeisenApi
from spitzeisen._sync.config import SpitzeisenConfig as SyncSpitzeisenConfig
from spitzeisen._sync.core import SpitzeisenApi as SyncSpitzeisenApi
from spitzeisen.auth import AuthStrategy, BearerHeader, HeaderKey, NoAuth, QueryParamAuth
from spitzeisen.concurrency import gather_bounded, map_bounded
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
    Limiter,
    NoLimit,
    SyncLimiter,
    single_bucket,
    sync_single_bucket,
)
from spitzeisen.models import SpitzeisenModel
from spitzeisen.operations import SpitzeisenOperationSpec
from spitzeisen.pagination import (
    CursorPagination,
    JsonObject,
    JsonValue,
    NoPagination,
    PageNumber,
    PaginationStrategy,
    extract_records,
)
from spitzeisen.params import (
    ParamScalar,
    ParamValue,
    QueryParams,
    SerializedQueryParam,
    build_header_params,
    resolve_page_size,
    serialize_path_param,
    serialize_query_map,
    serialize_query_param,
)

__all__ = (
    "AuthStrategy",
    "AuthenticationError",
    "BearerHeader",
    "CursorPagination",
    "HTTPError",
    "HeaderKey",
    "JsonObject",
    "JsonValue",
    "Limiter",
    "MaxRetriesExceededError",
    "NoAuth",
    "NoLimit",
    "NoPagination",
    "NotFoundError",
    "PageNumber",
    "PaginationStrategy",
    "ParamScalar",
    "ParamValue",
    "QueryParamAuth",
    "QueryParams",
    "ResponseShapeError",
    "SerializedQueryParam",
    "ServerError",
    "SpitzeisenApi",
    "SpitzeisenConfig",
    "SpitzeisenError",
    "SpitzeisenModel",
    "SpitzeisenOperationSpec",
    "SyncLimiter",
    "SyncSpitzeisenApi",
    "SyncSpitzeisenConfig",
    "TransportError",
    "ValidationMode",
    "build_header_params",
    "extract_records",
    "gather_bounded",
    "http_error_from_status",
    "map_bounded",
    "resolve_page_size",
    "serialize_path_param",
    "serialize_query_map",
    "serialize_query_param",
    "single_bucket",
    "sync_single_bucket",
)
