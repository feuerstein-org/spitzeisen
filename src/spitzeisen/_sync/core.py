# Generated from src/spitzeisen/_async/ by build_sync.py -- do not edit.
# Change the async module and run `python build_sync.py`.
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
from typing import TYPE_CHECKING, Any, Literal, Self, cast, overload

import structlog
from httpx2 import URL, Client, NetworkError, Response, TimeoutException
from httpx2 import QueryParams as HttpxQueryParams
from pydantic import BaseModel, TypeAdapter, ValidationError

from spitzeisen.exceptions import (
    HTTP_SERVER_ERROR_MIN,
    HTTP_TOO_MANY_REQUESTS,
    HTTPError,
    MaxRetriesExceededError,
    NotFoundError,
    ResponseShapeError,
    TransportError,
    http_error_from_status,
)
from spitzeisen.params import QueryParams, SerializedQueryParam

if TYPE_CHECKING:
    from spitzeisen._sync.config import SpitzeisenConfig, ValidationMode
    from spitzeisen.operations import SpitzeisenOperationSpec
    from spitzeisen.pagination import JsonObject, JsonValue

logger = structlog.get_logger(__name__)

TRANSPORT_RETRYABLE = (TimeoutException, NetworkError)


@functools.cache
def _list_adapter(model: type[BaseModel]) -> TypeAdapter[list[Any]]:
    """Build (and cache) a TypeAdapter that validates a whole list of Pydantic `model` instances."""
    return TypeAdapter(list[model])  # type: ignore[valid-type]


@functools.cache
def _input_adapter(annotation: Any) -> TypeAdapter[Any]:
    """Build and cache the adapter used by opt-in strict input validation."""
    return TypeAdapter(annotation)


def _is_retryable(status: int) -> bool:
    """Whether an HTTP status is worth another attempt: rate limiting or a transient fault."""
    return status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR_MIN


class SpitzeisenApi:
    """
    Shared transport, raw response helpers, and model validation for API clients.

    Subclasses describe *what* to call with a `SpitzeisenOperationSpec` and call the
    public request and collection helpers, they never touch the HTTP client or the limiter directly.
    """

    def __init__(self, config: SpitzeisenConfig) -> None:
        """Bind the shared configuration."""
        self.config = config
        self._apis: dict[type[SpitzeisenApi], SpitzeisenApi] = {}

    def api[ApiT: SpitzeisenApi](self, api_class: type[ApiT]) -> ApiT:
        """
        Return a cached API group sharing this client's config, connection, and limiter.

        Groups need only inherit this class and accept its config in their constructor.
        The root context owns the connection, accessing a group does not acquire another
        holder. A group may also be used as an independent context manager.
        """
        if api_class not in self._apis:
            self._apis[api_class] = api_class(self.config)
        return cast("ApiT", self._apis[api_class])

    def __enter__(self) -> Self:
        """Register as a holder of the shared HTTP client, nothing is opened until first use."""
        self.config.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        """Close the HTTP client, unless another holder is still using it or the caller owns it."""
        client = self.config.release()
        if client is not None:
            client.close()

    @property
    def _http_client(self) -> Client:
        """
        The shared HTTP client, built on first use.

        What spitzeisen builds, spitzeisen closes, a client supplied on the config is left open,
        because the caller may be sharing it with the rest of their process.
        """
        return self.config.get_http_client()

    def get_json(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> JsonValue:
        """
        Fetch the decoded JSON body through the shared request transport.

        HTTP 404 raises unless `not_found_ok=True`, in which case it returns None.
        A successful JSON null also returns None regardless of this flag. Other HTTP
        errors and invalid JSON still raise.

        Use `request` directly for response headers, status, text, or bytes. JSON parsing
        happens after HTTP handling and a parsing failure does not retry the request.
        """
        try:
            response = self.request(
                operation_spec,
                params=params,
                headers=headers,
                path_params=path_params,
            )
        except NotFoundError:
            if not_found_ok:
                return None
            raise
        return cast("JsonValue", response.json())

    @overload
    def get_object(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        result_key: str | None = None,
        not_found_ok: Literal[False] = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> JsonObject: ...

    @overload
    def get_object(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        result_key: str | None = None,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> JsonObject | None: ...

    def get_object(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        result_key: str | None = None,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> JsonObject | None:
        """
        Fetch one response object, optionally unwrapping a vendor envelope.

        HTTP 404 raises unless `not_found_ok=True`, which returns None. Successful
        nulls, other non-object values, and missing envelope keys raise ResponseShapeError.
        Returned fields are unchanged.
        """
        # don't pass not_found_ok to get_json since otherwise for both 404 and empty response
        # we would return a ResponseShapeError.
        try:
            value = self.get_json(operation_spec, not_found_ok=False, params=params, headers=headers, **path_params)
        except NotFoundError:
            if not_found_ok:
                return None
            raise
        if result_key is not None:
            if not isinstance(value, dict) or result_key not in value:
                msg = f"expected response envelope containing {result_key!r}"
                raise ResponseShapeError(msg)
            value = value[result_key]
        if not isinstance(value, dict):
            msg = "expected a response object"
            raise ResponseShapeError(msg)
        return value

    def request(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        path_params: Mapping[str, str] | None = None,
    ) -> Response:
        """
        Sends an HTTP request according to the details in the `operation_spec`, returns an HTTP response.

        This is the lower-level alternative to `get_json`. Read `response.headers` and
        `response.status_code`, decode with `response.json()` or `response.text`, or use
        `response.content` for bytes.

        HTTP errors are raised before returning and retain their response body and headers.

        POST and PATCH are not retried by default. The client config owns the usual request
        timeout and connection lifecycle.

        Returns:
            The httpx2 response after HTTP error handling, with its body buffered in memory.

        Raises:
            MaxRetriesExceededError: If 429 responses persist past `max_retries`.
            ServerError: If 5xx responses persist past `max_retries`.
            TransportError: If timeouts or connection errors persist past `max_retries`.
            HTTPError: For any other error status (AuthenticationError on 401/403,
                NotFoundError on 404), raised immediately without retrying.

        """
        if content is not None and not isinstance(cast("object", content), bytes):
            msg = "Request content must be replayable bytes"
            raise TypeError(msg)
        url = operation_spec.url(self.config.base_url, **(path_params or {}))
        attempts = self.config.max_retries if operation_spec.can_retry else 0
        request_params = list(params or [])
        request_headers = dict(headers or {})
        auth_params: dict[str, str] = {}
        self.config.auth.apply(request_headers, auth_params)
        request_params = [item for item in request_params if item.name not in auth_params]
        request_params.extend(SerializedQueryParam(name, value) for name, value in auth_params.items())
        httpx_params = URL(url).params.merge(HttpxQueryParams([(item.name, item.value) for item in request_params]))

        for attempt in range(attempts + 1):
            try:
                with self.config.limiter(operation_spec.cost):
                    client = self._http_client
                    request = client.build_request(
                        operation_spec.method,
                        url,
                        params=httpx_params,
                        headers=request_headers,
                        timeout=self.config.request_timeout,
                        content=content,
                    )
                    response = client.send(request)
            # This only triggers for non HTTP errors essentially, 5xx are handled below
            except TRANSPORT_RETRYABLE as exc:
                if attempt >= attempts:
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
                if status >= 400:  # noqa: PLR2004
                    raise _error_of(response)
                return response

            if attempt >= attempts:
                error = _error_of(response)
                if status == HTTP_TOO_MANY_REQUESTS and operation_spec.can_retry:
                    raise MaxRetriesExceededError(attempts, status) from error
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

    def iter_records(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> Iterator[JsonObject]:
        """
        Yield records across pages, following the operation's pagination strategy.

        Stops after `max_results` records (None means every record). Each page costs one
        limiter acquisition. HTTP 404 always raises, including after records were yielded.
        """
        for records in self._iter_record_pages(
            operation_spec,
            params=params,
            max_results=max_results,
            headers=headers,
            **path_params,
        ):
            for record in records:
                yield record

    def _iter_record_pages(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> Iterator[list[JsonObject]]:
        """Yield each successful page's records, including empty pages, capped at `max_results`."""
        if max_results is not None and max_results < 1:
            msg = f"max_results must be >= 1, got {max_results}"
            raise ValueError(msg)

        # Get first page
        current = operation_spec.pagination.first_params(list(params or []))
        yielded = 0
        while True:
            page = self.get_json(operation_spec, not_found_ok=False, params=current, headers=headers, **path_params)
            records = operation_spec.pagination.records(page)
            batch = records if max_results is None else records[: max_results - yielded]
            yield batch
            yielded += len(batch)
            if max_results is not None and yielded >= max_results:
                return
            # The strategy decides what the next page needs, or if this was the final page returns None.
            next_params = operation_spec.pagination.next_params(page, records, current)
            if next_params is None:
                return
            current = list(next_params)

    @overload
    def get_records(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        not_found_ok: Literal[False] = False,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> list[JsonObject]: ...

    @overload
    def get_records(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> list[JsonObject] | None: ...

    def get_records(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: str,
    ) -> list[JsonObject] | None:
        """
        Collect records across pages into a list of raw dicts, capped at `max_results`.

        HTTP 404 raises unless `not_found_ok=True` and the first requested page is missing,
        in which case None is returned. A 404 on a later page always raises, even if earlier
        pages were empty. A successful empty collection returns an empty list. Other HTTP,
        JSON decoding, and response-shape errors raise.
        """
        collected: list[JsonObject] = []
        received_page = False
        try:
            for records in self._iter_record_pages(
                operation_spec,
                params=params,
                max_results=max_results,
                headers=headers,
                **path_params,
            ):
                received_page = True
                collected.extend(records)
        except NotFoundError:
            if not_found_ok and not received_page:
                return None
            raise
        return collected

    def _resolve_validation_mode(self, override: ValidationMode | None) -> ValidationMode:
        """Resolve the effective validation mode from a per-call override and the config default."""
        mode = override if override is not None else self.config.on_validation_error
        if mode not in {"raise", "skip"}:
            msg = f"Invalid validation mode: {mode!r}"
            raise ValueError(msg)
        return mode

    def validate_input[InputT](self, value: InputT, annotation: Any) -> InputT:
        """Validate supplied values while allowing None for backwards compatibility."""
        if value is None or not self.config.validate_inputs:
            return value
        return cast("InputT", _input_adapter(annotation).validate_python(value, strict=True))

    @overload
    def validate_record[ModelT: BaseModel](
        self, record: JsonObject, model: type[ModelT], mode: Literal["raise"]
    ) -> ModelT: ...

    @overload
    def validate_record[ModelT: BaseModel](
        self, record: JsonObject, model: type[ModelT], mode: ValidationMode | None = None
    ) -> ModelT | None: ...

    def validate_record[ModelT: BaseModel](
        self, record: JsonObject, model: type[ModelT], mode: ValidationMode | None = None
    ) -> ModelT | None:
        """
        Validate one raw object, including nested fields, into a Pydantic model.

        `mode=None` uses the config's policy. "raise" propagates `ValidationError`,
        "skip" logs the error and returns None.
        """
        mode = self._resolve_validation_mode(mode)
        try:
            return model.model_validate(record)
        except ValidationError as exc:
            if mode == "raise":
                raise
            logger.warning("dropping_invalid_record", model=model.__name__, errors=exc.errors())
            return None

    def validate_records[ModelT: BaseModel](
        self,
        records: list[JsonObject],
        model: type[ModelT],
        mode: ValidationMode | None = None,
    ) -> list[ModelT]:
        """
        Validate raw records into `model` instances, validating the whole list at once.

        `mode=None` uses the config's policy. With mode "raise", an invalid record raises
        `pydantic.ValidationError` aggregating every bad row by index. With mode "skip",
        we first try the same path as for "raise" but on failure we rerun the validation
        this time with individual items and log the failing ones.
        """
        mode = self._resolve_validation_mode(mode)
        adapter = _list_adapter(model)
        if mode == "raise":
            return cast("list[ModelT]", adapter.validate_python(records))
        try:
            return cast("list[ModelT]", adapter.validate_python(records))
        except ValidationError:
            validated: list[ModelT] = []
            for record in records:
                parsed = self.validate_record(record, model, mode="skip")
                if parsed is not None:
                    validated.append(parsed)
            return validated


def _error_of(response: Response) -> HTTPError:
    """Preserve the response independently of its media type or error envelope."""
    return http_error_from_status(
        response.status_code,
        _message_of(response),
        body=response.content,
        headers=response.headers,
    )


def _message_of(response: Response) -> str:
    """Best-effort error message from an error response, without letting decoding fail the call."""
    try:
        body = cast("JsonValue", response.json())
    except Exception:  # noqa: BLE001 - an unparseable error body must not mask the HTTP error
        return response.text
    if isinstance(body, dict):
        for key in ("message", "Message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                return value
    return body if isinstance(body, str) else response.text
