$version: "2"

namespace spitzeisen.python

use smithy.api#length

/// Optional Python presentation overrides for an operation.
@trait(selector: "operation")
structure operation {
    @length(min: 1)
    module: String

    @length(min: 1)
    accessor: String

    @length(min: 1)
    method: String
}

/// Optional Python argument-name override for an input member.
@trait(selector: "structure > member")
structure parameter {
    @length(min: 1)
    name: String
}

/// Optional Python/Pydantic field-name override for a modeled member.
@trait(selector: "structure > member")
structure modelField {
    @length(min: 1)
    name: String
}
