"""
Pagination strategies.

A strategy answers three questions about a decoded page: which records it carries (`records`), how
to get the first page (`first_params`) and how to get the following pages (`next_params`).

A few common strategies are provided for endpoint definitions.
You can create your own by implementing the PaginationStrategy Protocol.
"""

import re
from collections.abc import Collection
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from spitzeisen.exceptions import ResponseShapeError
from spitzeisen.params import QueryParams, SerializedQueryParam

# How many keys to log on failure
_KEYS_IN_ERROR = 10

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


def _describe(value: JsonValue) -> str:
    """Name what arrived, by shape rather than by content."""
    if isinstance(value, dict):
        keys = sorted(value)
        shown = ", ".join(repr(key) for key in keys[:_KEYS_IN_ERROR])
        more = f", ... ({len(keys)} keys)" if len(keys) > _KEYS_IN_ERROR else ""
        return f"an object with keys [{shown}{more}]" if keys else "an empty object"
    return f"a {type(value).__name__}"


def _ensure_record_objects(records: list[JsonValue], location: str) -> list[JsonObject]:
    """Require each record to be a JSON object."""
    validated: list[JsonObject] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            msg = f"expected record {index} {location} to be an object, got {_describe(record)}"
            raise ResponseShapeError(msg)
        validated.append(record)
    return validated


def extract_records(page: JsonValue, results_key: str | None) -> list[JsonObject]:
    """
    Extract records from an envelope when `results_key` is provided.

    Otherwise `page` is a assumed to be a list of records.
    A null envelope member means no records, other invalid shapes raise `ResponseShapeError`.
    """
    if results_key is None:
        if not isinstance(page, list):
            msg = f"expected the response body to be a list, got {_describe(page)}"
            raise ResponseShapeError(msg)
        return _ensure_record_objects(page, "in the response body")
    if not isinstance(page, dict):
        msg = f"expected an object carrying {results_key!r}, got {_describe(page)}"
        raise ResponseShapeError(msg)
    if results_key not in page:
        msg = f"expected {results_key!r} in the response envelope, got {_describe(page)}"
        raise ResponseShapeError(msg)
    records = page[results_key]
    if records is None:
        return []
    if not isinstance(records, list):
        msg = f"expected a list under {results_key!r}, got {_describe(records)}"
        raise ResponseShapeError(msg)
    return _ensure_record_objects(records, f"under {results_key!r}")


@runtime_checkable
class PaginationStrategy(Protocol):
    """
    Walks an API's paginated collection.

    Core will call `first_params` to get the first page (could be empty), afterwards `next_params`
    is called to get the next page params, if None is returned it means all pages were downloaded.

    `records` is called after every page to extract the actual data from the JSON.
    """

    def first_params(self, params: QueryParams) -> QueryParams:
        """
        Return the query params for the first request.

        Add whatever the vendor needs to serve page one. A strategy that needs nothing returns
        the caller's params unchanged.
        """
        ...

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Return the records carried by one decoded page, empty when it carries none."""
        ...

    def next_params(self, page: JsonValue, records: list[JsonObject], params: QueryParams) -> QueryParams | None:
        """
        Return the next request's query params, or None to stop.

        `records` are just the actual records extracted from page.
        """
        ...


class NoPagination:
    """Read one page of records."""

    def __init__(self, results_key: str | None = None) -> None:
        """Store the envelope key holding the records, or None when the body *is* the list."""
        self.results_key = results_key

    def first_params(self, params: QueryParams) -> QueryParams:
        """Copy the caller's query params unchanged."""
        return list(params)

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Read the records out of the envelope."""
        return extract_records(page, self.results_key)

    def next_params(self, page: JsonValue, records: list[JsonObject], params: QueryParams) -> QueryParams | None:
        """Always stop after the first page."""
        return None


class PageNumber:
    """
    Increment `page_param` from `start` by `step`, stopping on the first empty page.

    For example, `start=100, step=100` requests `?page=100`, `?page=200`, and so on.
    """

    def __init__(
        self,
        page_param: str = "page",
        start: int = 1,
        step: int = 1,
        results_key: str | None = None,
    ) -> None:
        """Store the param name, start, stride, and envelope key."""
        if step < 1:
            # A step of zero would re-request the same page for as long as it kept returning
            msg = f"step must be >= 1, got {step}"
            raise ValueError(msg)
        self.page_param = page_param
        self.start = start
        self.step = step
        self.results_key = results_key

    def first_params(self, params: QueryParams) -> QueryParams:
        """Ask for the starting page explicitly."""
        return _replace_query_param(params, self.page_param, str(self.start))

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Read the records out of the envelope."""
        return extract_records(page, self.results_key)

    def next_params(self, page: JsonValue, records: list[JsonObject], params: QueryParams) -> QueryParams | None:
        """Advance the counter by `step` for as long as records keep arriving."""
        if not records:
            return None
        current = next((item.value for item in reversed(params) if item.name == self.page_param), None)
        if current is None:
            msg = f"pagination param {self.page_param!r} is missing"
            raise ValueError(msg)
        return _replace_query_param(params, self.page_param, str(int(current) + self.step))


class CursorPagination:
    """
    Fetch successive pages using a cursor supplied by the API.

    The first request uses the caller's parameters. The `cursor_param` defines
    the param we use in the 2nd, 3rd etc. request to pass the "cursor value".
    `next_key` is the key we check in the JSON returned from the API to retrieve the
    cursor. `results_key` is to parse out the results. Some APIs return the cursor
    as an already encoded URL, in this case we parse that url (stored in `next_key`)
    retrieve the `cursor_param` and construct a new request. This can be disabled with
    `cursor_from_url = False`.

    `retain_params` is a set of params you want to _always_ send with every request,
    e.g. something like `type=brown`, some APIs encode those params in the `cursor_param`
    already and it's not required.

    A missing or null next value stops pagination. An empty page with a cursor continues.
    Invalid cursors or a cursor matching the current request raise ``ResponseShapeError``.
    """

    def __init__(
        self,
        cursor_param: str = "cursor",
        next_key: str = "next_url",
        results_key: str = "results",
        *,
        cursor_from_url: bool = True,
        retain_params: Collection[str] = (),
    ) -> None:
        """Configure the envelope, cursor encoding, and parameters carried between pages."""
        if not cursor_param or not next_key or not results_key:
            msg = "cursor_param, next_key, and results_key must be non-empty"
            raise ValueError(msg)
        self.cursor_param = cursor_param
        self.next_key = next_key
        self.results_key = results_key
        self.cursor_from_url = cursor_from_url
        self.retain_params = frozenset(retain_params)

    def first_params(self, params: QueryParams) -> QueryParams:
        """Preserve the initial params."""
        return list(params)

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Read records from the configured envelope member."""
        return extract_records(page, self.results_key)

    def next_params(self, page: JsonValue, records: list[JsonObject], params: QueryParams) -> QueryParams | None:
        """Return a cursor request, or finish when the continuation is absent or null."""
        if not isinstance(page, dict):
            msg = f"expected an object carrying {self.next_key!r}, got {_describe(page)}"
            raise ResponseShapeError(msg)
        continuation = page.get(self.next_key)
        if continuation is None:
            return None
        if not isinstance(continuation, str) or not continuation:
            msg = f"expected a non-empty string under {self.next_key!r}"
            raise ResponseShapeError(msg)
        cursor = self._cursor_from_url(continuation) if self.cursor_from_url else continuation
        if any(item.name == self.cursor_param and item.value == cursor for item in params):
            msg = f"pagination repeated the current {self.cursor_param!r} cursor"
            raise ResponseShapeError(msg)
        return [
            *(item for item in params if item.name in self.retain_params and item.name != self.cursor_param),
            SerializedQueryParam(self.cursor_param, cursor),
        ]

    def _cursor_from_url(self, continuation: str) -> str:
        """Decode one non-empty cursor while ignoring the URL's destination and other keys."""
        try:
            query = urlsplit(continuation).query
        except ValueError as exc:
            msg = f"malformed pagination URL under {self.next_key!r}"
            raise ResponseShapeError(msg) from exc
        if re.search(r"%(?![0-9A-Fa-f]{2})", query):
            msg = f"invalid percent encoding in pagination URL under {self.next_key!r}"
            raise ResponseShapeError(msg)
        try:
            tokens = parse_qs(query, keep_blank_values=True, errors="strict").get(self.cursor_param, [])
        except UnicodeError as exc:
            msg = f"malformed pagination URL under {self.next_key!r}"
            raise ResponseShapeError(msg) from exc
        if len(tokens) != 1 or not tokens[0]:
            msg = f"expected exactly one non-empty {self.cursor_param!r} parameter in {self.next_key!r}"
            raise ResponseShapeError(msg)
        return tokens[0]


def _replace_query_param(params: QueryParams, name: str, value: str) -> QueryParams:
    """Replace the pagination key without touching the caller-supplied params."""
    return [*(item for item in params if item.name != name), SerializedQueryParam(name, value)]
