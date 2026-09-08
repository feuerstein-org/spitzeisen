"""
The request core.

The HTTP client, retry semantics, pagination and validation live here. Everything
vendor-specific arrives as a `SpitzeisenOperationSpec` or a strategy object.

Documentation written neutrally in regards to async/sync since unasync generates
the sync counterpart.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, Self, cast, overload
from urllib.parse import quote, urlencode

import structlog
from httpx2 import URL, AsyncClient, NetworkError, Response, TimeoutException
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
    from spitzeisen._async.config import AsyncSpitzeisenConfig, ValidationMode
    from spitzeisen.operations import SpitzeisenOperationSpec
    from spitzeisen.pagination import JsonObject, JsonValue

logger = structlog.get_logger(__name__)

RETRYABLE = (TimeoutException, NetworkError)


@functools.cache
def _list_adapter(model: type[BaseModel]) -> TypeAdapter[list[Any]]:
    """Build (and cache) a TypeAdapter that validates a whole list of `model` instances."""
    return TypeAdapter(list[model])  # type: ignore[valid-type]


@functools.cache
def _input_adapter(annotation: Any) -> TypeAdapter[Any]:
    """Build and cache the adapter used by opt-in strict input validation."""
    return TypeAdapter(annotation)


def _is_retryable(status: int) -> bool:
    """Whether an HTTP status is worth another attempt: rate limiting or a transient fault."""
    return status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR_MIN


class AsyncSpitzeisenApi:
    """
    Shared transport and typed response helpers for handwritten API clients.

    Subclasses describe *what* to call with a `SpitzeisenOperationSpec` and call the
    public request and collection helpers; they never touch the HTTP client or the limiter directly.
    """

    def __init__(self, config: AsyncSpitzeisenConfig) -> None:
        """Bind the shared configuration."""
        self.config = config
        self._apis: dict[type[AsyncSpitzeisenApi], AsyncSpitzeisenApi] = {}

    def api[ApiT: AsyncSpitzeisenApi](self, api_class: type[ApiT]) -> ApiT:
        """
        Return a cached API group sharing this client's config, connection, and limiter.

        Groups need only inherit this class and accept its config in their constructor.
        The root context owns the connection; accessing a group does not acquire another
        holder. A group may also be used as an independent context manager.
        """
        if api_class not in self._apis:
            self._apis[api_class] = api_class(self.config)
        return cast("ApiT", self._apis[api_class])

    async def __aenter__(self) -> Self:
        """Register as a holder of the shared HTTP client; nothing is opened until first use."""
        self.config.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Close the HTTP client, unless another holder is still using it or the caller owns it."""
        if self.config.release():
            client = self.config.http_client
            try:
                await self._http_client.aclose()
            finally:
                if self.config.http_client is client:
                    self.config.http_client = None
                    self.config.owns_http_client = False

    @property
    def _http_client(self) -> AsyncClient:
        """
        The shared HTTP client, built on first use.

        What spitzeisen builds, spitzeisen closes, a client supplied on the config is left open,
        because the caller may be sharing it with the rest of their process.
        """
        if self.config.http_client is None:
            self.config.http_client = AsyncClient(timeout=self.config.request_timeout)
            self.config.owns_http_client = True
        return self.config.http_client

    async def get_json(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> JsonValue:
        """Fetch decoded JSON through the shared transport used by handwritten operations."""
        return await self.request(
            operation_spec,
            decoder=_decode_json,
            params=params,
            headers=headers,
            path_params=path_params,
        )

    async def request[ResultT](
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        decoder: Callable[[Response], ResultT],
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        path_params: Mapping[str, object] | None = None,
    ) -> ResultT:
        """
        Execute a handwritten operation with shared authentication, rate limiting, and retries.

        The synchronous decoder receives the buffered successful HTTP response, including its
        bytes, headers, and status. It may parse CSV, return bytes, or construct a Pydantic model.
        Decoder failures propagate unchanged and never trigger another HTTP request. HTTP errors
        are raised before decoding and retain their response body and headers.

        Supply an already serialized bytes body and its Content-Type header for requests with a
        payload. Only replayable bytes are accepted. Retries follow the operation's method and
        retryable setting; POST and PATCH are not retried by default. The client config owns the
        usual request timeout and connection lifecycle.

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
                async with self.config.limiter(operation_spec.cost):
                    client = self._http_client
                    request = client.build_request(
                        operation_spec.method,
                        url,
                        params=httpx_params,
                        headers=request_headers,
                        timeout=self.config.request_timeout,
                        content=content,
                    )
                    # Encode after HTTP client defaults are merged, using %20 for spaces.
                    query = urlencode(request.url.params.multi_items(), quote_via=quote).encode("ascii")
                    request.url = request.url.copy_with(query=query or None)
                    response = await client.send(request)
            except RETRYABLE as exc:
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
                await asyncio.sleep(self.config.backoff(attempt))
                continue

            status = response.status_code
            if not _is_retryable(status):
                if status >= 400:  # noqa: PLR2004 - the HTTP error boundary
                    raise _error_of(response)
                return decoder(response)

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
            await asyncio.sleep(self.config.backoff(attempt))

        msg = "Unexpected end of retry loop"
        raise RuntimeError(msg)

    async def get_json_optional(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> JsonValue:
        """Like `get_json`, but return None when the resource does not exist (HTTP 404)."""
        try:
            return await self.get_json(operation_spec, params=params, headers=headers, **path_params)
        except NotFoundError:
            return None

    async def iter_records(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> AsyncIterator[JsonObject]:
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
        seen: set[tuple[SerializedQueryParam, ...]] = set()
        while True:
            _check_page_progress(current, seen)
            page = await self.get_json(operation_spec, params=current, headers=headers, **path_params)
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

    async def get_records(
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
            async for record in self.iter_records(
                operation_spec,
                params=params,
                max_results=max_results,
                headers=headers,
                **path_params,
            )
        ]

    async def get_records_optional(
        self,
        operation_spec: SpitzeisenOperationSpec,
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> list[JsonObject] | None:
        """Collect pages like ``get_records``, mapping any HTTP 404 to None."""
        try:
            return await self.get_records(
                operation_spec, params=params, max_results=max_results, headers=headers, **path_params
            )
        except NotFoundError:
            return None

    async def get_models[ModelT: BaseModel](
        self,
        operation_spec: SpitzeisenOperationSpec,
        model: type[ModelT],
        *,
        params: QueryParams | None = None,
        max_results: int | None = None,
        on_validation_error: ValidationMode | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> list[ModelT]:
        """
        Collect a typed list, applying the record cap before raise/skip validation.

        The cap counts raw records, so skipping invalid rows can return fewer models.
        HTTP, decoding, and envelope errors always propagate; only Pydantic record
        validation is governed by ``on_validation_error``.
        """
        mode = self._resolve_validation_mode(on_validation_error)
        records = await self.get_records(
            operation_spec, params=params, max_results=max_results, headers=headers, **path_params
        )
        return self.validate_records(records, model, mode)

    @overload
    async def get_model[ModelT: BaseModel](
        self,
        operation_spec: SpitzeisenOperationSpec,
        model: type[ModelT],
        *,
        result_key: str | None = None,
        not_found_ok: Literal[False] = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> ModelT: ...

    @overload
    async def get_model[ModelT: BaseModel](
        self,
        operation_spec: SpitzeisenOperationSpec,
        model: type[ModelT],
        *,
        result_key: str | None = None,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> ModelT | None: ...

    async def get_model[ModelT: BaseModel](
        self,
        operation_spec: SpitzeisenOperationSpec,
        model: type[ModelT],
        *,
        result_key: str | None = None,
        not_found_ok: bool = False,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        **path_params: object,
    ) -> ModelT | None:
        """
        Parse one response object, optionally unwrapping a vendor envelope.

        Only an HTTP 404 becomes None, and only with ``not_found_ok=True``. Missing or
        null envelopes are shape errors; model validation failures always propagate.
        Use a model's Pydantic aliases/validators for nested vendor-specific layouts.
        """
        try:
            value = await self.get_json(operation_spec, params=params, headers=headers, **path_params)
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
        return self.validate_response(value, model)

    def _resolve_validation_mode(self, override: ValidationMode | None) -> ValidationMode:
        """Resolve the effective validation mode from a per-call override and the config default."""
        mode = override if override is not None else self.config.on_validation_error
        if mode not in {"raise", "skip"}:
            msg = f"Invalid validation mode: {mode!r}"
            raise ValueError(msg)
        return mode

    def validate_input[InputT](self, value: InputT, annotation: Any) -> InputT:
        """Validate supplied values while preserving None as the wire-omission escape hatch."""
        if value is None or not self.config.strict_inputs:
            return value
        return cast("InputT", _input_adapter(annotation).validate_python(value, strict=True))

    def validate_response[ModelT: BaseModel](self, value: JsonObject, model: type[ModelT]) -> ModelT:
        """Parse one response using the client's optional constraint checks."""
        return model.model_validate(value, context=self._response_validation_context())

    def _response_validation_context(self) -> dict[str, bool]:
        """Snapshot the policy for one validation, including every nested value."""
        return {"strict_response_validation": self.config.strict_response_validation}

    def validate_records[ModelT: BaseModel](
        self,
        records: list[JsonObject],
        model: type[ModelT],
        mode: ValidationMode | None = None,
    ) -> list[ModelT]:
        """
        Validate raw records into `model` instances, validating the whole list at once.

        With mode "raise", an invalid record raises `pydantic.ValidationError` aggregating
        every bad row by index. With mode "skip", we first try the same path as for "raise" but
        on failure we rerun the validation this time with individual items and log the failing ones.
        """
        mode = self._resolve_validation_mode(mode)
        adapter = _list_adapter(model)
        context = self._response_validation_context()
        if mode == "raise":
            return cast("list[ModelT]", adapter.validate_python(records, context=context))
        try:
            return cast("list[ModelT]", adapter.validate_python(records, context=context))
        except ValidationError:
            validated: list[ModelT] = []
            for record in records:
                try:
                    validated.append(model.model_validate(record, context=context))
                except ValidationError as exc:
                    logger.warning("dropping_invalid_record", model=model.__name__, errors=exc.errors())
            return validated


def _check_page_progress(params: QueryParams, seen: set[tuple[SerializedQueryParam, ...]]) -> None:
    """Stop a broken pagination cycle before issuing a duplicate paid request."""
    key = tuple(sorted(params, key=lambda item: item.name))
    if key in seen:
        msg = "pagination repeated a previous request"
        raise ResponseShapeError(msg)
    seen.add(key)


def _decode_json(response: Response) -> JsonValue:
    """Decode the JSON body after transport and HTTP errors have been handled."""
    return cast("JsonValue", response.json())


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
