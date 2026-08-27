# Vendor specification provenance

The linked OpenWeather source is HTML documentation rather than a machine-readable API
description. `vendor.json` is therefore a committed OpenAPI 3.1 transcription of the request
params, API-key security scheme, and JSON response fields documented on the official
[Current Weather page](https://openweathermap.org/api/current?collection=current_forecast).

The transcription records the example SDK's explicit requiredness and validation constraints in
the OpenAPI schema itself. Phenomenon-dependent fields remain optional. `weather.smithy` applies
SDK naming, JSON-only behaviour, and the `units` default as Smithy traits to the imported shapes.
