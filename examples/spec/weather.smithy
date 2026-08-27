$version: "2"

namespace weather.customizations

use openapi#GetCurrentWeather
use openapi#GetCurrentWeatherInput
use spitzeisen.api#hidden
use spitzeisen.api#pythonParameter
use spitzeisen.api#sdkOperation

apply GetCurrentWeather @sdkOperation(
    name: "current_weather"
    notFound: "empty"
)

apply GetCurrentWeatherInput$lat @pythonParameter(name: "latitude")
apply GetCurrentWeatherInput$lon @pythonParameter(name: "longitude")
apply GetCurrentWeatherInput$lang @pythonParameter(name: "language")
apply GetCurrentWeatherInput$mode @hidden
apply GetCurrentWeatherInput$units @default("standard")
