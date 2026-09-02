$version: "2"

namespace weather.customizations

use openapi#GetCurrentWeather
use openapi#GetCurrentWeatherInput
use openapi#OpenapiService
use spitzeisen.api#excludeParameter
use spitzeisen.api#notFound
use spitzeisen.protocols#genericRestJson
use spitzeisen.python#operation
use spitzeisen.python#parameter

apply OpenapiService @genericRestJson

apply GetCurrentWeather @operation(module: "current_weather")
apply GetCurrentWeather @notFound(behavior: "absent")

apply GetCurrentWeatherInput$lat @parameter(name: "latitude")
apply GetCurrentWeatherInput$lon @parameter(name: "longitude")
apply GetCurrentWeatherInput$lang @parameter(name: "language")
apply GetCurrentWeatherInput$mode @excludeParameter
apply GetCurrentWeatherInput$units @default("standard")
