# Vendor specification provenance

The linked OpenWeather source is HTML documentation rather than a machine-readable API
description. `vendor.json` is therefore a committed OpenAPI 3.1 transcription of the request
parameters, API-key security scheme, and JSON response fields documented on the official
[Current Weather page](https://openweathermap.org/api/current?collection=current_forecast).

The transcription deliberately does not invent `required` arrays because the HTML page cannot
express JSON Schema requiredness and notes that phenomenon-dependent fields may be absent.
`overlay.yaml` records the example SDK's explicit requiredness and validation constraints;
`manifest.yaml` contains SDK naming, JSON-only behaviour, and request cost. Keeping those layers
separate makes future documentation revisions reviewable as a clean `vendor.json` diff.
