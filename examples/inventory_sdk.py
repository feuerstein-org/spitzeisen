"""A dummy inventory SDK yielding records through custom bookmark pagination."""

from collections.abc import AsyncIterator

from spitzeisen import (
    JsonObject,
    JsonValue,
    QueryParams,
    ResponseShapeError,
    SpitzeisenApi,
    SpitzeisenModel,
    SpitzeisenOperationSpec,
    extract_records,
    serialize_query_map,
)


class BookmarkPagination:
    """Read `items` and send each response's `bookmark` as the next request's `after`."""

    def first_params(self, params: QueryParams) -> QueryParams:
        """Start with the SDK method's query parameters."""
        return list(params)

    def records(self, page: JsonValue) -> list[JsonObject]:
        """Extract raw records without validating inventory model fields."""
        return extract_records(page, "items")

    def next_params(
        self,
        page: JsonValue,
        records: list[JsonObject],  # noqa: ARG002 - required by the pagination protocol
        params: QueryParams,
    ) -> QueryParams | None:
        """Stop without a bookmark, otherwise replace `after` and preserve other parameters."""
        if not isinstance(page, dict):
            msg = "expected an inventory response object"
            raise ResponseShapeError(msg)
        bookmark = page.get("bookmark")
        if bookmark is None:
            return None
        if not isinstance(bookmark, str) or not bookmark:
            msg = "expected a non-empty bookmark string"
            raise ResponseShapeError(msg)
        return [*(param for param in params if param.name != "after"), *serialize_query_map({"after": bookmark})]


class InventoryItem(SpitzeisenModel):
    """A model returned by the inventory endpoint's typed method."""

    id: str
    stock: int


_LIST_ITEMS = SpitzeisenOperationSpec(path="/items", pagination=BookmarkPagination())


class InventoryApi(SpitzeisenApi):
    """Iterate or collect inventory using a standard SpitzeisenConfig and bookmark pagination."""

    async def iter_items_raw(self) -> AsyncIterator[JsonObject]:
        """Yield raw items, fetching the next page as iteration advances."""
        async for record in self.iter_records(_LIST_ITEMS):
            yield record

    async def iter_items(self) -> AsyncIterator[InventoryItem]:
        """Validate and yield each item, following the config's validation policy."""
        async for record in self.iter_items_raw():
            item = self.validate_record(record, InventoryItem)
            if item is not None:
                yield item

    async def list_items(self) -> list[InventoryItem]:
        """Return all items in the inventory."""
        return [item async for item in self.iter_items()]
