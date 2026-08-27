$version: "2"

namespace native.weather

use smithy.api#http
use smithy.api#httpLabel
use smithy.api#httpPayload
use spitzeisen.api#sdkOperation

/// A small service used to exercise Spitzeisen's Smithy-first pipeline.
service WeatherService {
    version: "1.0"
    operations: [GetWeather]
}

/// Return the current weather for one city.
@http(method: "GET", uri: "/weather/{city}", code: 200)
@sdkOperation(notFound: "empty")
operation GetWeather {
    input := {
        /// City whose current weather should be returned.
        @required
        @httpLabel
        city: String
    }
    output := {
        @required
        @httpPayload
        weather: Weather
    }
}

/// A current weather observation.
structure Weather {
    /// Air temperature in degrees Celsius.
    @required
    @range(min: -100, max: 100)
    temperature: Float

    /// Broad current condition.
    @required
    status: WeatherStatus

    details: WeatherDetails
}

enum WeatherStatus {
    CLEAR = "clear"
    RAIN = "rain"
}

structure WeatherDetails {
    /// Human-readable conditions.
    @length(min: 1, max: 200)
    summary: String
}
