"""
Scripted HTTP for tests.

Built on `httpx2.MockTransport`, so tests exercise the real client pipeline — query encoding,
header handling, the retry loop — while never opening a socket. That is one mechanism rather
than a fake for logic plus a separate real-stack layer, and it needs no HTTP-mocking library.

`FakeRouter` handles the scripting: responses are queued per path and served in order, so a
test can describe "two 429s then a page of records" declaratively.
"""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the router served, for assertions after the fact."""

    method: str
    url: str
    path: str
    params: dict[str, str | list[str]]
    headers: Mapping[str, str]


class FakeRouter:
    """
    Scripted responses keyed by path, plus a log of what was asked for.

    Responses queued for a path are served in order; the last one repeats once the queue is
    down to it, so a test only scripts what it actually cares about.
    """

    def __init__(self) -> None:
        """Start with no routes and an empty request log."""
        self._routes: dict[str, deque[httpx2.Response | Exception]] = {}
        self.requests: list[RecordedRequest] = []

    def add(
        self,
        path: str,
        *,
        json: Any = None,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        repeat: int = 1,
        error: Exception | None = None,
    ) -> FakeRouter:
        """
        Queue `repeat` responses (or raised errors) for `path`.

        Chain calls to script a sequence. Pass `error=httpx2.ConnectError(...)` to simulate a
        transport fault. Returns self.
        """
        queue = self._routes.setdefault(path, deque())
        for _ in range(repeat):
            queue.append(
                error if error is not None else httpx2.Response(status, json=json, headers=dict(headers or {})),
            )
        return self

    def add_pages(
        self,
        path: str,
        pages: list[list[dict[str, Any]]],
        *,
        results_key: str | None = None,
    ) -> FakeRouter:
        """
        Queue a sequence of pages for one path, served in request order.

        `pages` holds the records of each page, so tests describe records rather than bodies.
        An empty page is appended automatically: a walk ends on the first page carrying no
        records, and `handle` repeats the last scripted response forever, so a sequence ending
        in a full page would otherwise loop until the test times out.
        """
        for records in [*pages, []]:
            self.add(path, json=records if results_key is None else {results_key: records})
        return self

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request and return (or raise) the next scripted response for its path."""
        path = urlsplit(str(request.url)).path
        self.requests.append(
            RecordedRequest(
                request.method,
                str(request.url),
                path,
                _recorded_params(request.url.params.multi_items()),
                request.headers,
            ),
        )
        queue = self._routes.get(path)
        if not queue:
            msg = f"No response scripted for {path!r}. Scripted paths: {sorted(self._routes)}"
            raise AssertionError(msg)
        response = queue[0] if len(queue) == 1 else queue.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def requests_for(self, path: str) -> list[RecordedRequest]:
        """Every recorded request whose path matches."""
        return [request for request in self.requests if request.path == path]

    def mock_transport(self) -> httpx2.MockTransport:
        """An httpx2 transport serving this router's script, for either surface."""
        return httpx2.MockTransport(self.handle)


def _recorded_params(items: list[tuple[str, str]]) -> dict[str, str | list[str]]:
    """Preserve repeated query keys while keeping the common scalar assertions compact."""
    grouped: dict[str, list[str]] = {}
    for key, value in items:
        grouped.setdefault(key, []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}
