# Generated from src/spitzeisen/_async/ by build_sync.py -- do not edit.
# Change the async module and run `mise run build-sync`.
"""
The request core.

The HTTP client, retry semantics, pagination and validation live here. Everything
vendor-specific arrives as a `SpitzeisenOperationSpec` or a strategy object.

Documentation written neutrally in regards to async/sync since unasync generates
the sync counterpart.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Self, cast

import structlog
from httpx2 import Client, NetworkError, Response, TimeoutException
from httpx2 import QueryParams as HttpxQueryParams
from pydantic import BaseModel, TypeAdapter, ValidationError

from spitzeisen.exceptions import (
    HTTP_SERVER_ERROR_MIN,
    HTTP_TOO_MANY_REQUESTS,
    MaxRetriesExceededError,
    NotFoundError,
    TransportError,
    http_error_from_status,
)
from spitzeisen.params import QueryParams, SerializedQueryParam

if TYPE_CHECKING:
    from spitzeisen._sync.config import SyncSpitzeisenConfig, ValidationMode
    from spitzeisen.operations import SpitzeisenOperationSpec
    from spitzeisen.pagination import JsonObject, JsonValue

logger = structlog.get_logger(__name__)

RETRYABLE = (TimeoutException, NetworkError)


@functools.cache
def _list_adapter(model: type[BaseModel]) -> TypeAdapter[list[Any]]:
    """Build (and cache) a TypeAdapter that validates a whole list of `model` instances."""
    return TypeAdapter(list[model])  # type: ignore[valid-type]


def _is_retryable(status: int) -> bool:
    """Whether an HTTP status is worth another attempt: rate limiting or a transient fault."""
    return status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR_MIN


class SyncSpitzeisenApi:
    """
    Base class for generated operation classes: one rate-limited, retrying, paginating request path.

    Subclasses describe *what* to call with a `SpitzeisenOperationSpec` and call the
    underscore-prefixed helpers; they never touch the HTTP client or the limiter directly.
    """

    def __init__(self, config: SyncSpitzeisenConfig) -> None:
        """Bind the shared configuration."""
        self.config = config

    def __enter__(self) -> Self:
        """Register as a holder of the shared HTTP client; nothing is opened until first use."""
        self.config.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        """Close the HTTP client, unless another holder is still using it or the caller owns it."""
        if self.config.release():
            self._http_client.close()

    @property
    def _http_client(self) -> Client:
        """
        The shared HTTP client, built on first use.

        What spitzeisen builds, spitzeisen closes, a client supplied on the config is left open,
        because the caller may be sharing it with the rest of their process.
        """
        if self.config.http_client is None:
            self.config.http_client = Client(timeout=self.config.request_timeout)
            self.config.owns_http_client = True
        return self.config.http_client

    def _request(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> JsonValue:
        """
        Perform one rate-limited request, retrying retryable statuses and transport failures.

        Returns the decoded JSON body.

        Raises:
            MaxRetriesExceededError: If 429 responses persist past `max_retries`.
            ServerError: If 5xx responses persist past `max_retries`.
            TransportError: If timeouts or connection errors persist past `max_retries`.
            HTTPError: For any other error status (AuthenticationError on 401/403,
                NotFoundError on 404), raised immediately without retrying.

        """
        url = operation_spec.url(self.config.base_url, **path_params)
        request_params = list(params or [])
        request_headers = dict(headers or {})
        auth_params: dict[str, str] = {}
        self.config.auth.apply(request_headers, auth_params)
        request_params.extend(SerializedQueryParam(name, value) for name, value in auth_params.items())
        httpx_params = HttpxQueryParams([(item.name, item.value) for item in request_params])

        for attempt in range(self.config.max_retries + 1):
            try:
                with self.config.limiter(operation_spec.cost):
                    response = self._http_client.request(
                        "GET",
                        url,
                        params=httpx_params,
                        headers=request_headers,
                        timeout=self.config.request_timeout,
                    )
            except RETRYABLE as exc:
                if attempt >= self.config.max_retries:
                    raise TransportError(exc) from exc
                logger.warning(
                    "request_retrying",
                    url=url,
                    reason="transport",
                    error=str(exc),
                    attempt=attempt + 1,
                    backoff=self.config.backoff(attempt),
                )
                time.sleep(self.config.backoff(attempt))
                continue

            status = response.status_code
            if not _is_retryable(status):
                if status >= 400:  # noqa: PLR2004 - the HTTP error boundary
                    raise http_error_from_status(status, _message_of(response))
                return cast("JsonValue", response.json())

            if attempt >= self.config.max_retries:
                error = http_error_from_status(status, _message_of(response))
                if status == HTTP_TOO_MANY_REQUESTS:
                    raise MaxRetriesExceededError(self.config.max_retries, status) from error
                raise error
            logger.warning(
                "request_retrying",
                url=url,
                reason="status",
                status=status,
                attempt=attempt + 1,
                backoff=self.config.backoff(attempt),
            )
            time.sleep(self.config.backoff(attempt))

        msg = "Unexpected end of retry loop"
        raise RuntimeError(msg)

    def _request_optional(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> JsonValue:
        """Like `_request`, but return None when the resource does not exist (HTTP 404)."""
        try:
            return self._request(operation_spec, params=params, headers=headers, **path_params)
        except NotFoundError:
            return None

    def _paginate(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> Iterator[JsonObject]:
        """
        Yield records across pages, following the operation's pagination strategy.

        Stops after `max_results` records (None means every record). Each page costs one
        limiter acquisition.
        """
        if max_results is not None and max_results < 1:
            msg = f"max_results must be >= 1, got {max_results}"
            raise ValueError(msg)

        # Get first page
        current = operation_spec.pagination.first_params(list(params or []))
        yielded = 0
        while True:
            page = self._request(operation_spec, params=current, headers=headers, **path_params)
            # Extract the actual content from the HTTP response and yield each individual record
            records = operation_spec.pagination.records(page)
            for record in records:
                yield record
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            # The strategy decides what the next page needs, or if this was the final page returns None.
            next_params = operation_spec.pagination.next_params(page, records, current, yielded)
            if next_params is None:
                return
            current = list(next_params)

    def _get_all_pages(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> list[JsonObject]:
        """Collect records across pages into a list of raw dicts, capped at `max_results`."""
        return [
            record
            for record in self._paginate(
                operation_spec,
                params=params,
                max_results=max_results,
                headers=headers,
                **path_params,
            )
        ]

    def _resolve_validation_mode(self, override: ValidationMode | None) -> ValidationMode:
        """Resolve the effective validation mode from a per-call override and the config default."""
        return override if override is not None else self.config.on_validation_error

    def _validate_records[ModelT: BaseModel](
        self,
        records: list[JsonObject],
        model: type[ModelT],
        mode: ValidationMode,
    ) -> list[ModelT]:
        """
        Validate raw records into `model` instances, validating the whole list at once.

        With mode "raise", an invalid record raises `pydantic.ValidationError` aggregating
        every bad row by index. With mode "skip", we first try the same path as for "raise" but
        on failure we rerun the validation this time with individual items and log the failing ones.
        """
        adapter = _list_adapter(model)
        if mode == "raise":
            return cast("list[ModelT]", adapter.validate_python(records))
        try:
            return cast("list[ModelT]", adapter.validate_python(records))
        except ValidationError:
            validated: list[ModelT] = []
            for record in records:
                try:
                    validated.append(model.model_validate(record))
                except ValidationError as exc:
                    logger.warning("dropping_invalid_record", model=model.__name__, errors=exc.errors())
            return validated


def _message_of(response: Response) -> str:
    """Best-effort error message from an error response, without letting decoding fail the call."""
    # TODO: Add support for a single string returned in body?
    # Also maybe just return the dict (stringified and truncated) instead of fishing for keys
    try:
        body = cast("JsonValue", response.json())
    except Exception:  # noqa: BLE001 - an unparseable error body must not mask the HTTP error
        return ""
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                return value
    return ""
