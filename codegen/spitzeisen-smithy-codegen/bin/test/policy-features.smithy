$version: "2"

namespace policy.example

use smithy.api#default
use smithy.api#http
use smithy.api#httpHeader
use smithy.api#httpLabel
use smithy.api#httpPayload
use smithy.api#httpQuery
use spitzeisen.api#hidden
use spitzeisen.api#modelProperty
use spitzeisen.api#pageNumberPagination
use spitzeisen.api#pythonParameter
use spitzeisen.api#sdkOperation
use spitzeisen.api#sorting

service ExampleService {
    version: "1.0"
    operations: [ListRecords]
}

/// Return records selected by the caller.
@http(method: "GET", uri: "/accounts/{account_id}/records", code: 200)
@readonly
@sdkOperation(
    name: "records"
    methodName: "list_records"
    documentationUrl: "https://docs.example.test/records"
    cost: 2.5
)
@pageNumberPagination(
    page: "page"
    pageSize: "limit"
    items: "results"
    start: 0
    step: 2
)
@sorting(style: "suffix", sort: "sort")
operation ListRecords {
    input := {
        /// Account whose records are returned.
        @required
        @httpLabel
        account_id: String

        @httpQuery("page")
        page: Integer

        @range(max: 500)
        @httpQuery("limit")
        limit: Integer

        @default("created.desc")
        @httpQuery("sort")
        sort: SortValue

        /// Filters applied to the records.
        @httpQuery("filter")
        @pythonParameter(name: "filters", coercion: "comma_list")
        filter: StringList

        @httpQuery("since")
        @pythonParameter(coercion: "date")
        since: String

        @httpQuery("state")
        @pythonParameter(coercion: "choice", literal: "StateAlias")
        state: State

        @httpQuery("custom")
        @pythonParameter(function: "coerce_custom", annotation: "str")
        custom: String

        @httpQuery("language")
        @pythonParameter(clientDefault: "en", style: "pipeDelimited", explode: false)
        language: String

        @hidden
        @httpQuery("internal")
        internal: String

        @required
        @httpHeader("X-Workspace")
        workspace: String
    }
    output := {
        @required
        @httpPayload
        records: RecordList
    }
}

enum SortValue {
    CREATED_ASC = "created.asc"
    CREATED_DESC = "created.desc"
    UPDATED_ASC = "updated.asc"
    UPDATED_DESC = "updated.desc"
}

enum State {
    OPEN = "open"
    CLOSED = "closed"
}

list StringList {
    member: String
}

list RecordList {
    member: Record
}

structure Record {
    @required
    id: String

    @modelProperty(name: "display_name", type: "str")
    @jsonName("displayName")
    displayName: String
}
