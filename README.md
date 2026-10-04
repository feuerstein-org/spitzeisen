# Spitzeisen (Pickaxe in German)

A Python library for building REST API clients, with async and sync support. Spitzeisen handles HTTP requests, retries, rate limiting, and pagination. You write the endpoint methods and Pydantic models for your API. It's intended for simple data APIs e.g. financial APIs, product APIs etc.

This project is in Alpha, I've built this to simplify/unite the code from the various financial APIs I'm working on (e.g. [eodhd-py](https://github.com/feuerstein-org/eodhd-py) and [massive-api](https://github.com/feuerstein-org/massive-api)). Feel free to use it as a building block for your own SDKs, better documentation is currently being worked on. In the future I want to add the possibility to generate the entire SDK off of OpenAPI and Smithy models but that's just an idea for now.

## Installation

Requires Python 3.12 or newer.

```bash
pip install spitzeisen
```

The library is still in alpha, so the API may change.

## Usage

> Note: It is **strongly** recommended to review the example weather SDK in `examples`, the below is just a very simple showcase, it is advised to create endpoint groups within one SDK Baseclass.

Here's a client for an API that returns a single item from `/item/{item_id}`:

```python
import asyncio

from spitzeisen import (
    BearerHeader,
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenModel,
    SpitzeisenOperationSpec,
    serialize_path_param,
)

class Item(SpitzeisenModel):
    id: str
    name: str

_ITEM = SpitzeisenOperationSpec(path="/item/{item_id}")

class ItemsApi(SpitzeisenApi):
    async def get_item(self, item_id: str) -> Item:
        item = await self.get_object(_ITEM, item_id=serialize_path_param(item_id))
        return self.validate_record(item, Item, mode="raise")

async def main():
    config = SpitzeisenConfig(
        base_url="https://api.example.com",  # Replace with your API's URL
        auth=BearerHeader("YOUR_API_KEY"),
    )

    async with ItemsApi(config) as api:
        item = await api.get_item("123")
        print(item.id, item.name)


asyncio.run(main())
```

`serialize_path_param` URL-encodes the item ID before inserting it into the path. `get_object` fetches the JSON response. `validate_record` converts it into an `Item` model. With `mode="raise"`, a response that doesn't match the model raises a Pydantic validation error.

For a sync client, subclass `SyncSpitzeisenApi` and use `SyncSpitzeisenConfig`. Write regular methods with `with` blocks and no `await`. The [weather example](examples/weather_sdk) includes both versions. It's recommended to generate the sync counterparts as much as possible using unasync, the Weather example already does this.

## Config

Authentication, retries, timeouts, and rate limits are configured through `SpitzeisenConfig`.
For example:

```python
from spitzeisen import BearerHeader, SpitzeisenConfig, single_bucket

config = SpitzeisenConfig(
    base_url="https://api.example.com",
    auth=BearerHeader("YOUR_API_KEY"),
    limiter=single_bucket("example-account", 60, 60),  # 60 tokens, refilled over 60 seconds
    max_retries=3,
    request_timeout=30,
    on_validation_error="raise",
)
```

Rate limiting uses [steindamm](https://github.com/feuerstein-org/steindamm).
The bucket above starts full and refills gradually. Each request costs one token by default, including retries and page requests. Pass a Redis connection to `single_bucket` to share the limit across processes.
Without a limiter, requests are unlimited. Sync clients use `sync_single_bucket`.

For authentication, you can also use `HeaderKey` or `QueryParamAuth`. The default is `NoAuth`.

Those are by far not the only ways to authenticate which are supported. You can easily implement your own authentication strategy, same applies to for example pagination, to do that simply pass your custom strategy in the `SpitzeisenOperationSpec`, see [here](examples/inventory_sdk.py) for an example pagination strategy.

Retryable requests use exponential backoff for HTTP 429, server errors, timeouts, and connection errors. POST and PATCH requests are not retried unless you opt in with `retryable=True` on the operation.

Endpoint groups created with `self.api(GroupClass)` share the parent's config, HTTP connection, and rate limiter. Use context managers to close connections when you're done. A supplied HTTP client stays open unless you set `owns_http_client=True`.

## Testing

Install the testing helpers with `pip install "spitzeisen[testing]"`, then enable the pytest plugin in your root `conftest.py`:

```python
pytest_plugins = ["spitzeisen.testing.plugin"]
```

`api_factory` creates and closes your client. `httpx2_mock` supplies responses without calling the real API.
Using the `ItemsApi` class above:

```python
import pytest
from spitzeisen import SpitzeisenConfig

@pytest.mark.asyncio
async def test_get_item(api_factory, httpx2_mock):
    httpx2_mock.add_response(
        url="https://api.example.com/item/123",
        json={"id": "123", "name": "Example item"},
    )
    api = await api_factory.create(
        ItemsApi,
        config=SpitzeisenConfig(base_url="https://api.example.com"),
    )

    item = await api.get_item("123")
    assert item.name == "Example item"
```

For sync clients, use `sync_api_factory`. See the [example tests](examples/tests) for more.

## Examples

- [Weather SDK](examples/weather_sdk): endpoint groups, response models, and async and sync clients.
- [Inventory SDK](examples/inventory_sdk.py): custom pagination and record iteration.
- [Offline demo](examples/demo.py): runs both weather clients with saved responses.

To run the demo from the repository root:

```bash
mise run install
mise run demo-example
```

The demo needs no API key or network connection.

## Development

```bash
mise run install
mise run build-sync
mise run lint
mise run test
```

The sync code is generated from the async code with `unasync`.
After editing the async implementation, run `mise run build-sync` to update both the library and weather example.
`mise run check-sync` checks that the generated files are up to date.
