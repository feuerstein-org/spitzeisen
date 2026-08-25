# Client usability demo

The runnable [`demo_client_usability.py`](demo_client_usability.py) uses the exact checked-in
Smithy output with a deterministic mock transport. This page isolates the public API choices a
consumer sees.

## Current Smithy-generated client

```python
config = Config(
    endpoint_uri="https://api.example.test",
    api_key="demo-key",
)

async with WeatherService(config) as client:
    weather = await client.get_current_weather(
        GetCurrentWeatherInput(
            latitude=52.52,
            longitude=13.405,
            units=Units.METRIC,
            language="en",
        )
    )

print(weather.name)
print(weather.main.temp)
```

This is serviceable and strongly typed, but it has three visible costs:

1. Every operation requires constructing a separate input object.
2. Only an asynchronous client is emitted.
3. The generated model is a dataclass, not a validating Pydantic model. Required input members are
   still typed as optional and constraints are not checked when constructing it.

The generated client also accepts a per-call list of configuration plugins, and the config accepts
interceptors. Those are useful low-level extension mechanisms, but they are not the same as a safe,
handwritten operation or model subclass.

## Pagination in the current output

Smithy's model contains a complete `@paginated` trait, but the Python generator emits only a
single-page method. An SDK consumer—or a wrapper maintained by the SDK author—currently needs this:

```python
items = []
next_token = None

while True:
    page = await client.list_forecasts(
        ListForecastsInput(
            latitude=52.52,
            longitude=13.405,
            next_token=next_token,
            page_size=100,
        )
    )
    items.extend(page.items)
    if page.next_token is None:
        break
    next_token = page.next_token
```

The runnable demo deliberately includes this helper so the usability cost is concrete.

## Spitzeisen's intended generated surface

Ignoring the deliberately corrupted example currently checked into the branch, Spitzeisen's
documented consumer contract is:

```python
config = AsyncSpitzeisenConfig(
    base_url="https://api.example.test",
    auth=QueryParamAuth("appid", "demo-key"),
)

async with AsyncWeatherApi(config) as client:
    weather = await client.current_weather_api.get_current_weather(
        latitude=52.52,
        longitude=13.405,
        units="metric",
        language="en",
    )
```

For a collection, pagination is hidden behind the operation:

```python
forecasts = await client.forecasts_api.list_forecasts(
    latitude=52.52,
    longitude=13.405,
    max_results=500,
)
```

The equivalent blocking API changes only the configuration/client class and removes `await`:

```python
with SyncWeatherApi(sync_config) as client:
    weather = client.current_weather_api.get_current_weather(
        latitude=52.52,
        longitude=13.405,
        units="metric",
        language="en",
    )
```

## Usability conclusion

For a boto3-like low-level client, the Smithy shape is recognizable and reasonable: one service
client, one input structure, one output structure, modeled exceptions, retry configuration, and
interceptors. It is closer to an async AWS SDK client than to boto3's most convenient resource
interfaces.

For the stated goal of a polished, Python-native SDK, Spitzeisen's intended call site is materially
better today: keyword arguments, automatic pagination, sync and async parity, Pydantic response
validation, and safe public subclasses. Recreating those features as a Smithy generator plugin and
runtime layer would preserve Smithy as the model while preserving Spitzeisen's usability—but that
is custom tooling, not a maintenance-free replacement.

If minimum maintenance wins over API design, use the stock generator and accept its call shape. If
client usability is non-negotiable, the current stock Smithy Python output is not sufficient by
itself.
