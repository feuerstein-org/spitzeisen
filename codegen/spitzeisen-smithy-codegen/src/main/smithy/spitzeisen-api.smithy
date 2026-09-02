$version: "2"

namespace spitzeisen.api

use smithy.api#default
use smithy.api#length
use smithy.api#range
use smithy.api#trait

/// Selects the logical value returned by an operation from its modeled output.
@trait(selector: "operation")
structure result {
    /// Non-empty path of Smithy member names, starting at the operation output.
    @required
    path: ResultPath

    /// Required only when the terminal target is Document and its cardinality is ambiguous.
    cardinality: ResultCardinality
}

@length(min: 1)
list ResultPath {
    @length(min: 1)
    member: String
}

enum ResultCardinality {
    SINGLE = "single"
    COLLECTION = "collection"
}

/// Controls the target-neutral representation of an HTTP 404 response.
@trait(selector: "operation")
structure notFound {
    @default("raise")
    behavior: NotFoundBehavior
}

enum NotFoundBehavior {
    RAISE = "raise"
    ABSENT = "absent"
}

/// Amount acquired from a configured rate limiter for each request.
@trait(selector: "operation")
structure rateLimitCost {
    @required
    @range(min: 0.000000001)
    units: Double
}

/// Page-number pagination for services that cannot use Smithy's cursor pagination trait.
@trait(selector: "operation")
structure pageNumberPagination {
    /// Input member carrying the page number.
    @required
    @length(min: 1)
    pageMember: String

    /// Input member carrying the requested page size.
    @length(min: 1)
    pageSizeMember: String

    /// First page number.
    @default(1)
    start: Integer

    /// Amount added to the page number after each response.
    @default(1)
    @range(min: 1)
    step: Integer
}

/// Portable sorting controls synthesized into a target's public API.
@trait(selector: "operation")
structure sorting {
    @required
    encoding: SortingEncoding

    /// Input member carrying the sort field, or the combined suffix encoding.
    @required
    @length(min: 1)
    sortMember: String

    /// Input member carrying sort order when encoding is `separate`.
    @length(min: 1)
    orderMember: String

    /// Separator between field and order when encoding is `suffix`.
    @default(".")
    @length(min: 1)
    separator: String
}

enum SortingEncoding {
    SEPARATE = "separate"
    SUFFIX = "suffix"
}

/// Query collection serialization for generic HTTP protocols.
@trait(selector: "structure > member")
structure queryEncoding {
    @required
    style: QueryStyle

    @required
    explode: Boolean

    @default(false)
    allowReserved: Boolean
}

enum QueryStyle {
    FORM = "form"
    SPACE_DELIMITED = "spaceDelimited"
    PIPE_DELIMITED = "pipeDelimited"
    DEEP_OBJECT = "deepObject"
}

/// Target-neutral client-side default distinct from Smithy's wire/model default.
@trait(selector: "structure > member")
structure clientDefault {
    @required
    value: Document
}

/// Omits an operation from generated SDK surfaces.
@trait(selector: "operation")
structure excludeOperation {}

/// Omits an input member from generated SDK parameter surfaces.
@trait(selector: "structure > member")
structure excludeParameter {}

/// Selects a registered target-neutral input adapter by stable identifier.
@trait(selector: "structure > member")
structure inputAdapter {
    @required
    @length(min: 1)
    id: String
}
