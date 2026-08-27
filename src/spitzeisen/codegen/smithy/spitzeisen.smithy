$version: "2"

namespace spitzeisen.api

/// How a generated operation is presented by the Python SDK.
@trait(selector: "operation")
structure sdkOperation {
    /// Stable module and aggregate-client accessor name. Defaults to the operation name.
    name: String

    /// Public Python method name. Defaults to the snake-cased operation name.
    methodName: String

    /// Public response-model name when it cannot be inferred from the output payload.
    responseModel: String

    /// Operation-specific documentation URL.
    documentationUrl: String

    /// Generate the Pydantic response model instead of requiring a handwritten public model.
    generateModel: Boolean = true

    /// Explicitly select collection or single-object rendering when the payload is ambiguous.
    shape: OperationShape

    /// How HTTP 404 is represented by a single-object operation.
    notFound: NotFoundBehavior = "raise"

    /// Scalar amount acquired from the configured rate limiter for each request.
    @range(min: 0.000000001)
    cost: Double = 1

    /// Top-level response member containing the returned object or collection.
    resultPath: String
}

enum OperationShape {
    COLLECTION = "collection"
    SINGLE = "single"
}

enum NotFoundBehavior {
    RAISE = "raise"
    EMPTY = "empty"
}

/// Page-number pagination for non-Smithy vendor protocols.
@trait(selector: "operation")
structure pageNumberPagination {
    /// Query key carrying the page number.
    page: String = "page"

    /// Query member carrying the requested page size.
    pageSize: String

    /// Response member carrying the page's records.
    items: String

    /// Explicit page-size ceiling when the member has no @range maximum.
    @range(min: 1)
    maxPageSize: Integer

    /// First page number.
    start: Integer = 1

    /// Amount added to the page number after each response.
    @range(min: 1)
    step: Integer = 1
}

/// Sorting controls synthesized into the public `sort` and `order` arguments.
@trait(selector: "operation")
structure sorting {
    @required
    style: SortingStyle

    @required
    sort: String

    order: String
    sortLiteral: String
    orderLiteral: String
    sortDefault: Document
    orderDefault: Document
}

enum SortingStyle {
    SUFFIX = "suffix"
    PARAM = "param"
}

/// Python-facing behavior for one Smithy input member.
@trait(selector: "structure > member")
structure pythonParameter {
    name: String
    style: QueryStyle
    explode: Boolean
    coercion: CoercionStyle = "plain"
    function: String
    literal: String
    annotation: String
    clientDefault: Document
}

enum QueryStyle {
    FORM = "form"
    SPACE_DELIMITED = "spaceDelimited"
    PIPE_DELIMITED = "pipeDelimited"
}

enum CoercionStyle {
    PLAIN = "plain"
    DATE = "date"
    COMMA_LIST = "comma_list"
    COMMA_CHOICE_LIST = "comma_choice_list"
    CHOICE = "choice"
}

/// Hide an imported operation or input member from the generated SDK.
@trait
structure hidden {}

/// Pydantic-backend customization for a response structure member.
@trait(selector: "structure > member")
structure modelProperty {
    name: String
    type: String
}
