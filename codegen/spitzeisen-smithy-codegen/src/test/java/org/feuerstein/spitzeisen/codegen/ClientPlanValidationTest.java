package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;

final class ClientPlanValidationTest {
    @Test
    void rejectsNonGetOperations() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http

                service TestService { version: "1", operations: [CreateThing] }

                @http(method: "POST", uri: "/things", code: 200)
                operation CreateThing { input := {}, output := {} }
                """, "supports GET only");
    }

    @Test
    void rejectsGetRequestBodies() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http

                service TestService { version: "1", operations: [GetThing] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                operation GetThing {
                    input := { value: String }
                    output := {}
                }
                """, "has a request body");
    }

    @Test
    void rejectsCursorPaginationUntilTheRuntimeSupportsIt() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use smithy.api#httpQuery
                use smithy.api#paginated

                service TestService { version: "1", operations: [ListThings] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @paginated(inputToken: "nextToken", outputToken: "nextToken")
                operation ListThings {
                    input := {
                        @httpQuery("nextToken")
                        nextToken: String
                    }
                    output := { nextToken: String }
                }
                """, "cursor pagination");
    }

    @Test
    void rejectsPageNumberPaginationForSingleResponses() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use spitzeisen.api#pageNumberPagination
                use spitzeisen.api#sdkOperation

                service TestService { version: "1", operations: [GetThing] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "single")
                @pageNumberPagination(page: "page")
                operation GetThing { input := {}, output := {} }
                """, "cannot combine page-number pagination with shape='single'");
    }

    @Test
    void rejectsSortingThatNamesNoQueryMember() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use spitzeisen.api#sdkOperation
                use spitzeisen.api#sorting

                service TestService { version: "1", operations: [ListThings] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "collection")
                @sorting(style: "suffix", sort: "sort", sortDefault: "created", orderDefault: "asc")
                operation ListThings { input := {}, output := {} }
                """, "sorting param 'sort' is absent");
    }

    @Test
    void requiresAnExplicitOrSmithyPageSizeMaximum() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use smithy.api#httpQuery
                use spitzeisen.api#pageNumberPagination
                use spitzeisen.api#sdkOperation

                service TestService { version: "1", operations: [ListThings] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "collection")
                @pageNumberPagination(page: "page", pageSize: "limit")
                operation ListThings {
                    input := {
                        @httpQuery("page")
                        page: Integer
                        @httpQuery("limit")
                        limit: Integer
                    }
                    output := {}
                }
                """, "needs @pageNumberPagination maxPageSize or a Smithy @range maximum");
    }

    @Test
    void rejectsNestedResultPathsUntilTheRuntimeSupportsThem() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use spitzeisen.api#sdkOperation

                service TestService { version: "1", operations: [ListThings] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "collection", resultPath: "outer.items")
                operation ListThings { input := {}, output := {} }
                """, "nested resultPath/items paths are not supported");
    }

    @Test
    void rejectsPythonKeywordsAsPublicArguments() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use smithy.api#httpQuery
                use spitzeisen.api#sdkOperation

                service TestService { version: "1", operations: [GetThing] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "single")
                operation GetThing {
                    input := {
                        @httpQuery("class")
                        class: String
                    }
                    output := {}
                }
                """, "maps to invalid Python argument 'class'");
    }

    @Test
    void rejectsUnresolvedOpenApiConverterPlaceholders() {
        assertCompileFailure("""
                $version: "2"
                namespace validation
                use smithy.api#http
                use smithy.api#trait
                use spitzeisen.api#sdkOperation

                @trait
                structure errorMessage {}

                @errorMessage
                structure ConverterPlaceholder {}

                service TestService { version: "1", operations: [GetThing] }

                @readonly
                @http(method: "GET", uri: "/things", code: 200)
                @sdkOperation(responseModel: "Thing", shape: "single")
                operation GetThing { input := {}, output := {} }
                """, "OpenAPI conversion left unsupported Smithy placeholders");
    }

    private static void assertCompileFailure(String source, String expectedMessage) {
        var model = Model.assembler()
                .discoverModels()
                .addUnparsedModel("validation.smithy", source)
                .assemble()
                .getResult()
                .orElseThrow();
        var settings = SpitzeisenSettings.fromNode(Node.parse("""
                {
                    "service": "validation#TestService",
                    "package": "validation_sdk",
                    "clientName": "ValidationApi"
                }
                """).expectObjectNode());

        var failure = assertThrows(IllegalArgumentException.class, () -> new ClientPlanCompiler(model, settings).compile());
        assertTrue(failure.getMessage().contains(expectedMessage), failure::getMessage);
    }
}
