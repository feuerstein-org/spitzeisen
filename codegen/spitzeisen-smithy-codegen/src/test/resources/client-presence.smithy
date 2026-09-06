$version: "2"

namespace client.presence

use alloy#simpleRestJson

@simpleRestJson
service ExampleService { version: "1", operations: [Search, ListResults] }

@readonly
@http(method: "GET", uri: "/search/{key}", code: 200)
operation Search {
    input := {
        @required @httpLabel key: String
        @required @httpQuery("query") query: String
        @required @httpHeader("X-Workspace") workspace: String
        @required @clientOptional @httpQuery("legacy") legacy: String
        @clientOptional @httpQuery("ignored") ignored: String = "old"
        @httpQuery("limit") limit: Integer = 10
        @required @httpQuery("count") count: Integer
        @required @httpQuery("enabled") enabled: Boolean
        @httpQuery("state") state: State
    }
    output := { @required @httpPayload result: Result }
}

@readonly
@http(method: "GET", uri: "/results", code: 200)
operation ListResults {
    output := { @httpPayload results: Results }
}

list Results { member: Result }

structure Result {
    @required id: String
    @required @clientOptional legacy: String
    @clientOptional ignored: String = "old"
    @clientOptional implicit: PrimitiveInteger = 0
    @required @default("fallback") defaulted: String
    count: Integer = 7
    @required @range(min: 0, max: 10) score: Integer
    @required @length(min: 2, max: 4) @pattern("^[a-z]+$") name: String
    @required state: State
    @required level: Level
    optionalChild: Child
    @required @clientOptional @jsonName("oldChild") oldChild: Child
    @required child: Child
    sparseList: SparseList
    denseList: DenseList
    sparseMap: SparseMap
    denseMap: DenseMap
    @length(min: 1, max: 2) names: DenseList
    scores: Scores
    states: States
    levels: Levels
    codes: Codes
}

structure Child {
    @required @length(min: 1) value: String
    next: Child
}

enum State {
    OPEN = "open"
    CLOSED = "closed"
}
intEnum Level {
    NEGATIVE = -1
    LOW = 1
    HIGH = 2
}
@sparse
list SparseList { member: Child }
list DenseList { member: Child }
@sparse
map SparseMap { key: String, value: Child }
map DenseMap { key: String, value: Child }

@range(min: 0, max: 10)
integer Score
list Scores { member: Score }
list States { member: State }
@sparse
list Levels { member: Level }
@pattern("^[a-z]+$")
string Code
map Codes { key: String, value: Code }
