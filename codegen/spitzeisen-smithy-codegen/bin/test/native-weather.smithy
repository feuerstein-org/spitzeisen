$version: "2"

namespace native.weather

use smithy.api#http
use smithy.api#httpLabel
use smithy.api#httpPayload
use spitzeisen.api#sdkOperation

service WeatherService {
    version: "1.0"
    operations: [GetWeather]
}

/// Return the current weather for one city.
@readonly
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

structure Weather {
    @required
    temperature: Float
}
