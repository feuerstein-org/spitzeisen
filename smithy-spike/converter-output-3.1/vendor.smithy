$version: "2.0"

namespace vendor

use error#All
use error#Base
use error#Cod
use error#CoordinatesLat
use error#CoordinatesLon
use error#Country
use error#CurrentWeatherResponseId
use error#Deg
use error#Description
use error#Dt
use error#FeelsLike
use error#GetCurrentWeatherInputLat
use error#GetCurrentWeatherInputLon
use error#GrndLevel
use error#Gust
use error#Humidity
use error#Icon
use error#Lang
use error#Main
use error#Message
use error#Mode
use error#n1h
use error#Name
use error#Pressure
use error#SeaLevel
use error#Speed
use error#Sunrise
use error#Sunset
use error#SystemMetadataId
use error#Temp
use error#TempMax
use error#TempMin
use error#Timezone
use error#Type
use error#Units
use error#Visibility
use error#Weather
use error#WeatherConditionId
use smithytranslate#contentType

@externalDocumentation(
    "Official Current Weather documentation": "https://openweathermap.org/api/current?collection=current_forecast"
)
@httpApiKeyAuth(
    name: "appid"
    in: "query"
)
service VendorService {
    operations: [
        GetCurrentWeather
    ]
}

/// Returns the current weather observation for one latitude and longitude.
@auth([
    httpApiKeyAuth
])
@http(
    method: "GET"
    uri: "/data/2.5/weather"
    code: 200
)
operation GetCurrentWeather {
    input: GetCurrentWeatherInput
    output: GetCurrentWeather200
    errors: [
        GetCurrentWeather401
    ]
}

structure Clouds {
    @required
    all: All
}

structure Coordinates {
    @required
    lon: CoordinatesLon
    @required
    lat: CoordinatesLat
}

structure CurrentWeatherResponse {
    @required
    coord: Coordinates
    @required
    weather: Weather
    base: Base
    @required
    main: WeatherMeasurements
    visibility: Visibility
    wind: Wind
    rain: PrecipitationVolume
    snow: PrecipitationVolume
    clouds: Clouds
    @required
    dt: Dt
    sys: SystemMetadata
    @required
    timezone: Timezone
    @required
    id: CurrentWeatherResponseId
    @required
    name: Name
    @required
    cod: Cod
}

structure GetCurrentWeather200 {
    @httpPayload
    @required
    @contentType("application/json")
    body: CurrentWeatherResponse
}

@error("client")
@httpError(401)
structure GetCurrentWeather401 {}

structure GetCurrentWeatherInput {
    /// Latitude of the location.
    @httpQuery("lat")
    @required
    lat: GetCurrentWeatherInputLat
    /// Longitude of the location.
    @httpQuery("lon")
    @required
    lon: GetCurrentWeatherInputLon
    /// Optional non-JSON response format. JSON is returned when omitted.
    @httpQuery("mode")
    mode: Mode
    /// Units of measurement. Standard units are used by default.
    @httpQuery("units")
    units: Units
    /// Language code used for the city name and weather description.
    @httpQuery("lang")
    lang: Lang
}

structure PrecipitationVolume {
    @jsonName("1h")
    @required
    n1h: n1h
}

structure SystemMetadata {
    type: Type
    id: SystemMetadataId
    message: Message
    @required
    country: Country
    @required
    sunrise: Sunrise
    @required
    sunset: Sunset
}

structure WeatherCondition {
    @required
    id: WeatherConditionId
    @required
    main: Main
    @required
    description: Description
    @required
    icon: Icon
}

structure WeatherMeasurements {
    @required
    temp: Temp
    @required
    feels_like: FeelsLike
    temp_min: TempMin
    temp_max: TempMax
    @required
    pressure: Pressure
    @required
    humidity: Humidity
    sea_level: SeaLevel
    grnd_level: GrndLevel
}

structure Wind {
    @required
    speed: Speed
    @required
    deg: Deg
    gust: Gust
}
