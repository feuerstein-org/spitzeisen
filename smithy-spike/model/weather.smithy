$version: "2"

namespace test.spitzeisen.weather

use aws.protocols#restJson1

/// Describes the number of vendor rate-limit tokens consumed by an operation.
/// The stock Smithy Python generator deliberately ignores this custom trait.
@trait(selector: "operation")
structure rateLimitCost {
    @required
    cost: Float
}

/// A representative weather service covering the protocol features Spitzeisen needs.
@restJson1
@httpApiKeyAuth(name: "appid", in: "query")
service WeatherService {
    version: "2026-08-25"
    operations: [GetCurrentWeather, ListForecasts, SubmitObservation]
}

@readonly
@rateLimitCost(cost: 1)
@http(method: "GET", uri: "/data/2.5/weather", code: 200)
operation GetCurrentWeather {
    input := {
        @required
        @httpQuery("lat")
        latitude: Double

        @required
        @httpQuery("lon")
        longitude: Double

        @httpQuery("units")
        units: Units = "standard"

        @httpQuery("lang")
        language: String
    }

    output: CurrentWeather
    errors: [InvalidLocation, RateLimited]
}

/// A token-paginated collection. This checks whether the Python generator emits a paginator,
/// not just whether Smithy can describe pagination.
@readonly
@paginated(
    inputToken: "nextToken"
    outputToken: "nextToken"
    pageSize: "pageSize"
    items: "items"
)
@rateLimitCost(cost: 2)
@http(method: "GET", uri: "/data/2.5/forecast", code: 200)
operation ListForecasts {
    input := {
        @required
        @httpQuery("lat")
        latitude: Double

        @required
        @httpQuery("lon")
        longitude: Double

        @httpQuery("next_token")
        nextToken: String

        @range(min: 1, max: 100)
        @httpQuery("limit")
        pageSize: Integer
    }

    output := {
        nextToken: String

        @required
        items: ForecastList
    }

    errors: [InvalidLocation, RateLimited]
}

/// An illustrative JSON POST used to exercise request bodies and modeled errors.
@idempotent
@rateLimitCost(cost: 5)
@http(method: "POST", uri: "/data/2.5/observations", code: 201)
operation SubmitObservation {
    input := {
        @required
        stationId: String

        @required
        temperature: Double

        @required
        observedAt: Timestamp
    }

    output := {
        @required
        observationId: String
    }

    errors: [InvalidObservation, RateLimited]
}

@output
structure CurrentWeather {
    @required
    coord: Coordinates

    @required
    weather: WeatherConditions

    @required
    main: Measurements

    @required
    dt: Timestamp

    @required
    name: String
}

structure Coordinates {
    @required
    lon: Double

    @required
    lat: Double
}

structure WeatherCondition {
    @required
    id: Integer

    @required
    main: String

    description: String
}

list WeatherConditions {
    member: WeatherCondition
}

structure Measurements {
    @required
    temp: Double

    @range(min: 0, max: 100)
    humidity: Integer
}

structure Forecast {
    @required
    at: Timestamp

    @required
    measurements: Measurements
}

list ForecastList {
    member: Forecast
}

enum Units {
    STANDARD = "standard"
    METRIC = "metric"
    IMPERIAL = "imperial"
}

@error("client")
@httpError(400)
structure InvalidLocation {
    message: String
}

@error("client")
@httpError(422)
structure InvalidObservation {
    message: String
}

@error("client")
@retryable(throttling: true)
@httpError(429)
structure RateLimited {
    message: String
}
