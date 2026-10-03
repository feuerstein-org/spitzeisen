"""Test an SDK iterator with custom pagination."""

from inventory_sdk import InventoryApi, InventoryItem
from pytest_httpx2 import HTTPXMock

from spitzeisen import SpitzeisenConfig
from spitzeisen.testing import ApiFactory


async def test_inventory_iterator_fetches_pages_as_needed(api_factory: ApiFactory, httpx2_mock: HTTPXMock) -> None:
    """Yield typed items lazily across bookmarks, skipping invalid items by default."""
    inventory = await api_factory.create(
        InventoryApi,
        config=SpitzeisenConfig(base_url="https://inventory.test"),
    )
    httpx2_mock.add_response(
        method="GET",
        url="https://inventory.test/items",
        json={"items": [{"id": "a", "stock": "4"}], "bookmark": "page-2"},
    )
    httpx2_mock.add_response(
        method="GET",
        url="https://inventory.test/items",
        match_params={"after": "page-2"},
        json={"items": [{"id": "invalid", "stock": "unknown"}, {"id": "b", "stock": "7"}], "bookmark": None},
    )

    items = inventory.iter_items()
    assert not httpx2_mock.get_requests()
    assert await anext(items) == InventoryItem(id="a", stock=4)
    assert len(httpx2_mock.get_requests()) == 1

    assert [item async for item in items] == [InventoryItem(id="b", stock=7)]
    assert len(httpx2_mock.get_requests()) == 2
