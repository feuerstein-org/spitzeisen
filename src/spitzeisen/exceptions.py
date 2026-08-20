"""
Exception hierarchy for operational errors raised at request time.

These are distinct from input-validation errors (which raise the built-in `ValueError`) and
from `pydantic.ValidationError` raised by validated list methods. Catch `SpitzeisenError` to
handle any operational failure a client raises.

HTTP error responses are wrapped in an `HTTPError` subclass so callers never have to reach for
the underlying transport library to branch on failures. The originating library exception, when
there was one, is preserved on `__cause__`.

A client library that wants its own vocabulary should alias rather than subclass, so that
`except MyApiError:` and `except SpitzeisenError:` catch exactly the same objects::

    from spitzeisen.exceptions import SpitzeisenError as MyApiError
"""

HTTP_TOO_MANY_REQUESTS = 429
HTTP_NOT_FOUND = 404
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR_MIN = 500


class SpitzeisenError(Exception):
    """Base class for all runtime errors raised by a spitzeisen-based client."""


class HTTPError(SpitzeisenError):
    """
    Raised for an HTTP error response. Carries the HTTP `status` and the server-supplied `message`.
    """

    def __init__(self, status: int, message: str = "") -> None:
        """Record the HTTP status and server-supplied message."""
        self.status = status
        self.message = message
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


def http_error_from_status(status: int, message: str = "") -> HTTPError:
    """Map an HTTP status onto the most specific `HTTPError` subclass."""
    if status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
        return AuthenticationError(status, message)
    if status == HTTP_NOT_FOUND:
        return NotFoundError(status, message)
    if status >= HTTP_SERVER_ERROR_MIN:
        return ServerError(status, message)
    return HTTPError(status, message)
