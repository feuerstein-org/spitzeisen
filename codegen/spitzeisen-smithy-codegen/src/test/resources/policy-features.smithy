$version: "2"

namespace policy.example

use smithy.api#default
use smithy.api#externalDocumentation
use smithy.api#http
use smithy.api#httpHeader
use smithy.api#httpLabel
use smithy.api#httpPayload
use smithy.api#httpQuery
use spitzeisen.api#clientDefault
use spitzeisen.api#excludeOperation
use spitzeisen.api#excludeParameter
use spitzeisen.api#inputAdapter
use spitzeisen.api#notFound
use spitzeisen.api#pageNumberPagination
use spitzeisen.api#queryEncoding
use spitzeisen.api#rateLimitCost
use spitzeisen.api#result
use spitzeisen.api#sorting
use spitzeisen.python#modelField
use spitzeisen.python#operation
use spitzeisen.python#parameter
use spitzeisen.protocols#genericRestJson

@genericRestJson
service ExampleService {
    version: "1.0"
    operations: [ListRecords, InternalOperation]
}

/// Return records selected by the caller.
@http(method: "GET", uri: "/accounts/{account_id}/records", code: 200)
@readonly
@externalDocumentation(operation: "https://docs.example.test/records")
@result(path: ["page", "records"])
@notFound(behavior: "absent")
@rateLimitCost(units: 2.5)
@operation(module: "records", accessor: "records", method: "list_records")
@pageNumberPagination(
    pageMember: "page"
    pageSizeMember: "limit"
    start: 0
    step: 2
)
@sorting(encoding: "suffix", sortMember: "sort")
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
        @parameter(name: "filters")
        @inputAdapter(id: "comma-list")
        filter: StringList

        @httpQuery("since")
        @inputAdapter(id: "date")
        since: String

        @httpQuery("state")
        state: State

        @httpQuery("custom")
        @inputAdapter(id: "custom")
        custom: String

        @httpQuery("language")
        @queryEncoding(style: "pipeDelimited", explode: false)
        @clientDefault(value: "en")
        language: String

        @excludeParameter
        @httpQuery("internal")
        internal: String

        @required
        @httpHeader("X-Workspace")
        workspace: String
    }
    output := {
        @required
        @httpPayload
        page: RecordsPage
    }
}

@excludeOperation
@http(method: "GET", uri: "/internal", code: 200)
@readonly
operation InternalOperation {
    input := {}
    output := {}
}

structure RecordsPage {
    @required
    records: RecordList
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

    @modelField(name: "display_name")
    @jsonName("displayName")
    displayName: String
}
