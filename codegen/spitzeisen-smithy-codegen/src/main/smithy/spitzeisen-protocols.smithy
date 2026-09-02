$version: "2"

namespace spitzeisen.protocols

use smithy.api#protocolDefinition
use smithy.api#trait

/// Protocol for ordinary HTTP APIs whose document bodies use generic JSON semantics.
@protocolDefinition
@trait(selector: "service")
structure genericRestJson {}
