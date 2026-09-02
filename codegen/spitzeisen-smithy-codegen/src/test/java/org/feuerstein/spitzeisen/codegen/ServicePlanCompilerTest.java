package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.ServiceLoader;
import org.junit.jupiter.api.Test;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ShapeId;

final class ServicePlanCompilerTest {
  @Test
  void compilesLosslessRuntimeNeutralServicePlan() {
    var result = compile(comprehensiveModel());
    var document = result.node();
    var service = document.expectObjectMember("service");
    var operations = document.expectObjectMember("operations");
    var shapes = document.expectObjectMember("shapes");

    assertEquals("review#ExampleService", text(service, "id"));
    assertEquals("2026-08-30", text(service, "version"));
    assertEquals("Example API", text(service, "title"));
    assertEquals(
        "https://docs.example.test/",
        text(service.expectObjectMember("external_documentation"), "homepage"));
    assertEquals(List.of("review#wireProtocol"), strings(service.expectArrayMember("protocols")));
    assertEquals(List.of("smithy.api#noAuth"), strings(service.expectArrayMember("auth_schemes")));
    assertEquals(
        List.of("review#CreateThing", "review#ListThings"),
        strings(service.expectArrayMember("operations")));

    var create = operations.expectObjectMember("review#CreateThing");
    assertEquals("review#CreateThingInput", text(create, "input"));
    assertEquals("review#CreateThingOutput", text(create, "output"));
    assertEquals(List.of("review#CommonError"), strings(create.expectArrayMember("errors")));
    assertEquals(
        "create_thing",
        text(
            create
                .expectObjectMember("extensions")
                .expectObjectMember("spitzeisen.python#operation"),
            "method"));
    assertEquals(
        "https://docs.example.test/create",
        text(create.expectObjectMember("external_documentation"), "operation"));

    var policies = create.expectObjectMember("policies");
    var resultPolicy = policies.expectObjectMember("result");
    assertEquals(List.of("envelope", "thing"), strings(resultPolicy.expectArrayMember("path")));
    assertFalse(resultPolicy.containsMember("cardinality"));
    assertFalse(resultPolicy.containsMember("value_shape"));
    assertEquals("absent", text(policies.expectObjectMember("not_found"), "behavior"));
    assertEquals(
        2.5,
        policies
            .expectObjectMember("rate_limit_cost")
            .expectNumberMember("units")
            .getValue()
            .doubleValue());

    var inferredResult =
        operations
            .expectObjectMember("review#ListThings")
            .expectObjectMember("policies")
            .expectObjectMember("result");
    assertEquals(List.of("things"), strings(inferredResult.expectArrayMember("path")));
    assertFalse(inferredResult.containsMember("cardinality"));
    assertFalse(inferredResult.containsMember("value_shape"));

    var http = create.expectObjectMember("http");
    assertEquals("POST", text(http, "method"));
    assertEquals("/things/{account+}", text(http, "uri"));
    assertEquals(201, http.expectNumberMember("code").getValue().intValue());
    assertBinding(http.expectArrayMember("request_bindings"), 0, "account", "label");
    assertTrue(
        http.expectArrayMember("request_bindings")
            .getElements()
            .get(0)
            .expectObjectNode()
            .expectBooleanMember("greedy")
            .getValue());
    assertBinding(http.expectArrayMember("request_bindings"), 1, "trace", "header");
    assertBinding(http.expectArrayMember("request_bindings"), 2, "thing", "document");
    assertBinding(http.expectArrayMember("response_bindings"), 0, "envelope", "payload");
    var error = http.expectObjectMember("error_bindings").expectObjectMember("review#CommonError");
    assertEquals(409, error.expectNumberMember("code").getValue().intValue());
    assertBinding(error.expectArrayMember("bindings"), 0, "message", "document");

    var thing = shapes.expectObjectMember("review#Thing");
    assertEquals("structure", text(thing, "kind"));
    assertEquals(
        List.of(
            "id",
            "status",
            "children",
            "metadata",
            "priority",
            "tiny",
            "large",
            "precise",
            "createdAt",
            "payload"),
        thing.expectArrayMember("members").getElements().stream()
            .map(Node::expectObjectNode)
            .map(member -> text(member, "name"))
            .toList());
    assertTrue(thing.expectBooleanMember("recursive").getValue());

    var id = member(thing, "id");
    assertTrue(id.expectBooleanMember("required").getValue());
    assertFalse(id.expectBooleanMember("client_nullable").getValue());
    assertFalse(id.expectObjectMember("default").expectBooleanMember("present").getValue());
    assertEquals(
        1,
        id.expectObjectMember("constraints")
            .expectObjectMember("smithy.api#length")
            .expectNumberMember("min")
            .getValue()
            .intValue());

    var status = member(thing, "status");
    assertTrue(status.expectObjectMember("default").expectBooleanMember("present").getValue());
    assertEquals("open", text(status.expectObjectMember("default"), "value"));
    assertEquals(
        "state",
        text(
            status
                .expectObjectMember("extensions")
                .expectObjectMember("spitzeisen.python#modelField"),
            "name"));

    var statusShape = shapes.expectObjectMember("review#Status");
    assertEquals("enum", text(statusShape, "kind"));
    var enumValues = statusShape.expectArrayMember("enum_values");
    assertEquals("OPEN", text(enumValues.getElements().get(0).expectObjectNode(), "name"));
    assertEquals("open", text(enumValues.getElements().get(0).expectObjectNode(), "value"));
    assertEquals(
        "Open things", text(enumValues.getElements().get(0).expectObjectNode(), "documentation"));

    assertEquals("list", text(shapes.expectObjectMember("review#ThingList"), "kind"));
    assertEquals("map", text(shapes.expectObjectMember("review#Metadata"), "kind"));
    assertEquals("intEnum", text(shapes.expectObjectMember("review#Priority"), "kind"));
    assertEquals("byte", text(shapes.expectObjectMember("review#TinyValue"), "kind"));
    assertEquals("long", text(shapes.expectObjectMember("review#LargeValue"), "kind"));
    assertEquals("bigDecimal", text(shapes.expectObjectMember("review#PreciseValue"), "kind"));
    assertEquals("timestamp", text(shapes.expectObjectMember("review#CreatedAt"), "kind"));
    assertEquals("blob", text(shapes.expectObjectMember("review#Payload"), "kind"));
    assertTrue(document.expectObjectMember("extensions").isEmpty());
    assertTrue(result.resultRoots().contains(ShapeId.from("review#Thing")));
  }

  @Test
  void resolvesSmithyPaginationAndEventStreamsThroughKnowledgeIndexes() {
    var document = compile(paginationAndStreamingModel()).node();
    var operations = document.expectObjectMember("operations");
    var list = operations.expectObjectMember("review.more#ListThings");
    var pagination = list.expectObjectMember("policies").expectObjectMember("smithy_pagination");

    var inferredRoot = list.expectObjectMember("policies").expectObjectMember("result");
    assertTrue(inferredRoot.expectArrayMember("path").isEmpty());
    assertFalse(inferredRoot.containsMember("cardinality"));
    assertFalse(inferredRoot.containsMember("value_shape"));

    assertEquals("review.more#ListThingsInput$nextToken", text(pagination, "input_token"));
    assertEquals(
        List.of("review.more#ListThingsOutput$nextToken"),
        strings(pagination.expectArrayMember("output_token")));
    assertEquals("review.more#ListThingsInput$limit", text(pagination, "page_size"));
    assertEquals(
        List.of("review.more#ListThingsOutput$items"),
        strings(pagination.expectArrayMember("items")));

    var stream = operations.expectObjectMember("review.more#WatchThings");
    assertEquals(
        "review.more#WatchThingsOutput$events",
        text(stream.expectObjectMember("event_streams"), "output"));

    var pageOnly =
        operations
            .expectObjectMember("review.more#WalkThings")
            .expectObjectMember("policies")
            .expectObjectMember("page_number_pagination");
    assertEquals("review.more#WalkThingsInput$page", text(pageOnly, "page_member"));
    assertFalse(pageOnly.containsMember("page_size_member"));
  }

  @Test
  void recognizesLegacySetShapesAsCollectionResults() {
    var result =
        compile(
                """
                $version: "1.0"
                namespace review

                use spitzeisen.api#result

                service ExampleService {
                    version: "1"
                    operations: [ListThings]
                }

                @result(path: ["things"])
                operation ListThings {
                    input: ListThingsRequest
                    output: ListThingsResponse
                }

                structure ListThingsRequest {}

                structure ListThingsResponse {
                    things: Things
                }

                set Things {
                    member: Thing
                }

                structure Thing {
                    id: String
                }
                """)
            .node()
            .expectObjectMember("operations")
            .expectObjectMember("review#ListThings")
            .expectObjectMember("policies")
            .expectObjectMember("result");

    assertFalse(result.containsMember("cardinality"));
    assertFalse(result.containsMember("value_shape"));
  }

  @Test
  void pluginIsDiscoveredThroughJavaSpi() {
    var plugins = ServiceLoader.load(SmithyBuildPlugin.class);
    var provider =
        plugins.stream()
            .filter(value -> value.type().equals(SpitzeisenServicePlanPlugin.class))
            .findFirst()
            .orElseThrow();

    assertEquals("spitzeisen-service-plan", provider.get().getName());
  }

  @Test
  void compilesTheSplitPortableAndPythonFixtureTraits() {
    var model =
        Model.assembler()
            .discoverModels()
            .addImport(resource("/policy-features.smithy"))
            .assemble()
            .unwrap();
    var settings =
        ServicePlanSettings.fromNode(
            Node.parse("{\"service\":\"policy.example#ExampleService\"}").expectObjectNode());
    var prepared = ModelPreparation.prepare(model, settings);
    var compiled = new ServicePlanCompiler(prepared.model(), prepared.serviceId()).compile();
    var document = compiled.node();
    var service = document.expectObjectMember("service");
    var operations = document.expectObjectMember("operations");
    var list = operations.expectObjectMember("policy.example#ListRecords");
    var policies = list.expectObjectMember("policies");
    var input =
        document.expectObjectMember("shapes").expectObjectMember("policy.example#ListRecordsInput");

    assertEquals(
        List.of("spitzeisen.protocols#genericRestJson"),
        strings(service.expectArrayMember("protocols")));
    assertTrue(
        service
            .expectObjectMember("traits")
            .containsMember("spitzeisen.protocols#genericRestJson"));
    assertFalse(
        service
            .expectObjectMember("extensions")
            .containsMember("spitzeisen.protocols#genericRestJson"));
    assertEquals(
        "https://docs.example.test/records",
        text(list.expectObjectMember("external_documentation"), "operation"));

    var resultPolicy = policies.expectObjectMember("result");
    assertEquals(List.of("page", "records"), strings(resultPolicy.expectArrayMember("path")));
    assertFalse(resultPolicy.containsMember("cardinality"));
    assertFalse(resultPolicy.containsMember("value_shape"));
    assertEquals("absent", text(policies.expectObjectMember("not_found"), "behavior"));
    assertEquals(
        2.5,
        policies
            .expectObjectMember("rate_limit_cost")
            .expectNumberMember("units")
            .getValue()
            .doubleValue());

    var page = policies.expectObjectMember("page_number_pagination");
    assertEquals("policy.example#ListRecordsInput$page", text(page, "page_member"));
    assertEquals("policy.example#ListRecordsInput$limit", text(page, "page_size_member"));
    assertEquals(0, page.expectNumberMember("start").getValue().intValue());
    assertEquals(2, page.expectNumberMember("step").getValue().intValue());

    var sorting = policies.expectObjectMember("sorting");
    assertEquals("suffix", text(sorting, "encoding"));
    assertEquals("policy.example#ListRecordsInput$sort", text(sorting, "sort_member"));
    assertEquals(".", text(sorting, "separator"));

    assertTrue(
        member(input, "filter")
            .expectObjectMember("extensions")
            .containsMember("spitzeisen.python#parameter"));
    assertEquals(
        "comma-list",
        text(
            member(input, "filter")
                .expectObjectMember("policies")
                .expectObjectMember("input_adapter"),
            "id"));
    assertEquals(
        "pipeDelimited",
        text(
            member(input, "language")
                .expectObjectMember("policies")
                .expectObjectMember("query_encoding"),
            "style"));
    assertFalse(
        member(input, "language")
            .expectObjectMember("policies")
            .expectObjectMember("query_encoding")
            .expectBooleanMember("allow_reserved")
            .getValue());
    assertEquals(
        "en",
        text(
            member(input, "language")
                .expectObjectMember("policies")
                .expectObjectMember("client_default"),
            "value"));
    assertTrue(
        member(input, "internal")
            .expectObjectMember("policies")
            .expectBooleanMember("exclude_parameter")
            .getValue());
    assertTrue(
        operations
            .expectObjectMember("policy.example#InternalOperation")
            .expectObjectMember("policies")
            .expectBooleanMember("exclude_operation")
            .getValue());
    assertTrue(compiled.resultRoots().contains(ShapeId.from("policy.example#RecordList")));
    assertFalse(
        compiled.resultRoots().contains(ShapeId.from("policy.example#InternalOperationOutput")));
    var definitions =
        SpitzeisenServicePlanPlugin.createModelSchema(
                prepared.model(), prepared.serviceId(), compiled.resultRoots())
            .expectObjectNode()
            .expectObjectMember("$defs");
    assertTrue(definitions.containsMember("Record"));
    assertFalse(definitions.containsMember("InternalOperationOutput"));
  }

  private static CompiledServicePlan compile(String source) {
    var model =
        Model.assembler()
            .discoverModels()
            .addUnparsedModel("model.smithy", source)
            .assemble()
            .unwrap();
    var settings =
        ServicePlanSettings.fromNode(
            Node.parse("{\"service\": \"" + serviceId(source) + "\"}").expectObjectNode());
    var prepared = ModelPreparation.prepare(model, settings);
    return new ServicePlanCompiler(prepared.model(), prepared.serviceId()).compile();
  }

  private static String serviceId(String source) {
    return source.contains("namespace review.more")
        ? "review.more#ExampleService"
        : "review#ExampleService";
  }

  private static java.net.URL resource(String name) {
    var resource = ServicePlanCompilerTest.class.getResource(name);
    if (resource == null) {
      throw new IllegalArgumentException("missing test resource " + name);
    }
    return resource;
  }

  private static ObjectNode member(ObjectNode shape, String name) {
    return shape.expectArrayMember("members").getElements().stream()
        .map(Node::expectObjectNode)
        .filter(value -> text(value, "name").equals(name))
        .findFirst()
        .orElseThrow();
  }

  private static String text(ObjectNode node, String member) {
    return node.expectStringMember(member).getValue();
  }

  private static List<String> strings(software.amazon.smithy.model.node.ArrayNode node) {
    return node.getElements().stream()
        .map(Node::expectStringNode)
        .map(value -> value.getValue())
        .toList();
  }

  private static void assertBinding(
      software.amazon.smithy.model.node.ArrayNode bindings,
      int index,
      String memberName,
      String location) {
    var binding = bindings.getElements().get(index).expectObjectNode();
    assertTrue(text(binding, "member_id").endsWith("$" + memberName));
    assertEquals(location, text(binding, "location"));
  }

  private static String comprehensiveModel() {
    return """
        $version: "2"
        namespace review

        use smithy.api#authDefinition
        use smithy.api#default
        use smithy.api#error
        use smithy.api#externalDocumentation
        use smithy.api#http
        use smithy.api#httpError
        use smithy.api#httpHeader
        use smithy.api#httpLabel
        use smithy.api#httpPayload
        use smithy.api#length
        use smithy.api#protocolDefinition
        use smithy.api#required
        use smithy.api#title
        use smithy.api#trait
        use spitzeisen.api#notFound
        use spitzeisen.api#rateLimitCost
        use spitzeisen.api#result
        use spitzeisen.python#modelField
        use spitzeisen.python#operation

        @protocolDefinition
        @trait(selector: "service")
        structure wireProtocol {}

        @wireProtocol
        @title("Example API")
        @externalDocumentation(homepage: "https://docs.example.test/")
        service ExampleService {
            version: "2026-08-30"
            operations: [CreateThing, ListThings]
            errors: [CommonError]
        }

        /// Create a thing with a request body.
        @externalDocumentation(operation: "https://docs.example.test/create")
        @http(method: "POST", uri: "/things/{account+}", code: 201)
        @result(path: ["envelope", "thing"])
        @notFound(behavior: "absent")
        @rateLimitCost(units: 2.5)
        @operation(module: "things", accessor: "things", method: "create_thing")
        operation CreateThing {
            input := {
                @required
                @httpLabel
                account: String

                @httpHeader("X-Trace")
                trace: String

                @required
                thing: Thing
            }
            output := {
                @required
                @httpPayload
                envelope: CreateThingEnvelope
            }
        }

        @http(method: "GET", uri: "/things", code: 200)
        operation ListThings {
            input := {}
            output := {
                @required
                @httpPayload
                things: ThingList
            }
        }

        structure CreateThingEnvelope {
            @required
            thing: Thing
        }

        structure Thing {
            @required
            @length(min: 1, max: 80)
            id: String

            @default("open")
            @modelField(name: "state")
            status: Status

            children: ThingList
            metadata: Metadata
            priority: Priority
            tiny: TinyValue
            large: LargeValue
            precise: PreciseValue
            createdAt: CreatedAt
            payload: Payload
        }

        list ThingList { member: Thing }
        map Metadata { key: String, value: Document }
        byte TinyValue
        long LargeValue
        bigDecimal PreciseValue
        timestamp CreatedAt
        blob Payload

        enum Status {
            /// Open things
            OPEN = "open"
            CLOSED = "closed"
        }

        intEnum Priority {
            LOW = 1
            HIGH = 10
        }

        @error("client")
        @httpError(409)
        structure CommonError {
            @required
            message: String
        }
        """;
  }

  private static String paginationAndStreamingModel() {
    return """
        $version: "2"
        namespace review.more

        use smithy.api#paginated
        use smithy.api#streaming
        use spitzeisen.api#pageNumberPagination

        service ExampleService {
            version: "1"
            operations: [ListThings, WatchThings, WalkThings]
        }

        @paginated(
            inputToken: "nextToken"
            outputToken: "nextToken"
            pageSize: "limit"
            items: "items"
        )
        operation ListThings {
            input := {
                nextToken: String
                limit: Integer
            }
            output := {
                nextToken: String
                items: ThingList
            }
        }

        operation WatchThings {
            input := {}
            output := {
                @required
                events: Events
            }
        }

        @pageNumberPagination(pageMember: "page")
        operation WalkThings {
            input := { page: Integer }
            output := { items: ThingList }
        }

        @streaming
        union Events {
            changed: ThingChanged
        }

        structure ThingChanged { id: String }
        structure Thing { id: String }
        list ThingList { member: Thing }
        """;
  }
}
