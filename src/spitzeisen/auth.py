"""
Authentication strategies.

A few default strategies which are most common are provided and can be configured via the manifest.
You can create your own AuthStrategy by implementing the AuthStrategy Protocol.
"""

from typing import Protocol, runtime_checkable

_REDACTED = "***"


@runtime_checkable
class AuthStrategy(Protocol):
    """Applies credentials to an outgoing request."""

    def apply(self, headers: dict[str, str], params: dict[str, str]) -> None:
        """Mutate `headers` and/or `params` to carry this request's credentials."""
        ...


class NoAuth:
    """No credentials. The default, and the whole strategy for open APIs."""

    def apply(self, headers: dict[str, str], params: dict[str, str]) -> None:
        """Leave the request untouched."""

    def __repr__(self) -> str:
        return "NoAuth()"


class BearerHeader:
    """`Authorization: Bearer <token>`."""

    def __init__(self, token: str) -> None:
        """Store the bearer token."""
        self._token = token

    def apply(self, headers: dict[str, str], params: dict[str, str]) -> None:
        """Set the Authorization header."""
        headers["Authorization"] = f"Bearer {self._token}"

    def __repr__(self) -> str:
        return f"BearerHeader(token={_REDACTED})"


class HeaderKey:
    """An API key carried in an arbitrary header, e.g. `X-API-Key: <key>`."""

    def __init__(self, name: str, key: str) -> None:
        """Store the header name and key."""
        self._name = name
        self._key = key

    def apply(self, headers: dict[str, str], params: dict[str, str]) -> None:
        """Set the configured header."""
        headers[self._name] = self._key

    def __repr__(self) -> str:
        return f"HeaderKey(name={self._name!r}, key={_REDACTED})"


class QueryParamAuth:
    """An API key carried as a query parameter, e.g. `?api_token=<key>`."""

    def __init__(self, name: str, key: str) -> None:
        """Store the parameter name and key."""
        self._name = name
        self._key = key

    def apply(self, headers: dict[str, str], params: dict[str, str]) -> None:
        """Set the configured query parameter."""
        params[self._name] = self._key

    def __repr__(self) -> str:
        return f"QueryParamAuth(name={self._name!r}, key={_REDACTED})"
