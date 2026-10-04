"""
Exception hierarchy for operational errors raised at request time.

HTTP error responses are wrapped in an `HTTPError` subclass which itself subclasses
SpitzeisenError The originating library exception, when there was one, is preserved on `__cause__`.

A client library that wants its own vocabulary should alias rather than subclass, so that
`except MyApiError:` and `except SpitzeisenError:` catch exactly the same objects::

    from spitzeisen.exceptions import SpitzeisenError as MyApiError
"""

from collections.abc import Mapping

HTTP_TOO_MANY_REQUESTS = 429
HTTP_NOT_FOUND = 404
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR_MIN = 500


class SpitzeisenError(Exception):
    """Base class for all runtime errors raised by a spitzeisen-based client."""


class HTTPError(SpitzeisenError):
    """
    Raised for an HTTP error response, preserving `status`, `message`, raw `body` bytes, and `headers`.
    """

    def __init__(
        self,
        status: int,
        message: str = "",
        *,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Preserve status, message, and original response data, including non-JSON failures."""
        self.status = status
        self.message = message
        self.body = body
        self.headers = dict(headers or {})
        detail = f": {message}" if message else ""
        super().__init__(f"Request failed with status {status}{detail}")


class AuthenticationError(HTTPError):
    """Raised on HTTP 401/403 responses (missing/invalid credentials or insufficient entitlement)."""


class NotFoundError(HTTPError):
    """Raised on HTTP 404 responses (the requested resource does not exist)."""


class ServerError(HTTPError):
    """Raised on HTTP 5xx responses (a fault on the API side)."""


class ResponseShapeError(SpitzeisenError):
    """Raised when a response body is not that we expected."""


class TransportError(SpitzeisenError):
    """Raised when a request keeps failing at the transport level (timeout, connection error)."""

    def __init__(self, cause: BaseException | str) -> None:
        """Record a description of the underlying transport failure."""
        detail = str(cause) or type(cause).__name__
        super().__init__(f"Request failed at the transport level: {detail}")


class MaxRetriesExceededError(SpitzeisenError):
    """
    Raised when a request keeps returning a retryable status past `max_retries`.

    The originating `HTTPError` is preserved on `__cause__`.
    """

    def __init__(self, retries: int, status: int = HTTP_TOO_MANY_REQUESTS) -> None:
        """Record the retry count and HTTP status that led to the failure."""
        self.retries = retries
        self.status = status
        super().__init__(f"Maximum retries ({retries}) exceeded after repeated {status} responses")


def http_error_from_status(
    status: int,
    message: str = "",
    *,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
) -> HTTPError:
    """Map an HTTP status onto the most specific `HTTPError` subclass."""
    if status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
        return AuthenticationError(status, message, body=body, headers=headers)
    if status == HTTP_NOT_FOUND:
        return NotFoundError(status, message, body=body, headers=headers)
    if status >= HTTP_SERVER_ERROR_MIN:
        return ServerError(status, message, body=body, headers=headers)
    return HTTPError(status, message, body=body, headers=headers)
