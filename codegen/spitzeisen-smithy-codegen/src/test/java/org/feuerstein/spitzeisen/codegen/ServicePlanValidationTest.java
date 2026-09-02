package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Optional;
import org.junit.jupiter.api.Test;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.ShapeId;

final class ServicePlanValidationTest {
  @Test
  void settingsContainOnlyOptionalServiceSelection() {
    assertEquals(
        "example#ExampleService",
        ServicePlanSettings.fromNode(
                Node.parse("{\"service\":\"example#ExampleService\"}").expectObjectNode())
            .service()
            .orElseThrow()
            .toString());
    assertTrue(ServicePlanSettings.fromNode(Node.objectNode()).service().isEmpty());

    assertThrows(
        RuntimeException.class,
        () ->
            ServicePlanSettings.fromNode(
                Node.parse("{\"package\":\"python_package\"}").expectObjectNode()));
  }

  @Test
  void requiresServiceSelectionOnlyForAmbiguousModels() {
    var model =
        Model.assembler()
            .addUnparsedModel(
                "services.smithy",
                """
                $version: "2"
                namespace example
                service First { version: "1" }
                service Second { version: "1" }
                """)
            .assemble()
            .unwrap();

    var error =
        assertThrows(
            IllegalArgumentException.class,
            () -> ModelPreparation.prepare(model, ServicePlanSettings.fromNode(Node.objectNode())));
    assertTrue(error.getMessage().contains("service is required"));
  }

  @Test
  void preparationFlattensMixinsAndCreatesDedicatedInputsAndOutputs() {
    var model =
        Model.assembler()
            .addUnparsedModel(
                "model.smithy",
                """
                $version: "2"
                namespace example

                @mixin
                structure Named { name: String }

                service ExampleService { version: "1", operations: [Create] }
                operation Create {
                    input := with [Named] {}
                    output := {}
                }
                """)
            .assemble()
            .unwrap();
    var prepared = ModelPreparation.prepare(model, ServicePlanSettings.fromNode(Node.objectNode()));
    var input =
        prepared
            .model()
            .expectShape(
                software.amazon.smithy.model.shapes.ShapeId.from("example#CreateInput"),
                software.amazon.smithy.model.shapes.StructureShape.class);

    assertTrue(input.getMixins().isEmpty());
    assertTrue(input.getMember("name").isPresent());
    assertTrue(
        prepared
            .model()
            .getShape(software.amazon.smithy.model.shapes.ShapeId.from("example#CreateOutput"))
            .isPresent());
  }

  @Test
  void resultCardinalityIsOnlyUsedToResolveDocumentAmbiguity() {
    var nonDocument =
        compileError(
            """
            $version: "2"
            namespace example
            use spitzeisen.api#result
            service ExampleService { version: "1", operations: [Get] }
            @result(path: ["value"], cardinality: "single")
            operation Get { input := {}, output := { value: String } }
            """);
    assertTrue(nonDocument.getMessage().contains("may only be set for a Document target"));

    var ambiguousDocument =
        compileError(
            """
            $version: "2"
            namespace example
            use spitzeisen.api#result
            service ExampleService { version: "1", operations: [Get] }
            @result(path: ["value"])
            operation Get { input := {}, output := { value: Document } }
            """);
    assertTrue(ambiguousDocument.getMessage().contains("cardinality is required"));

    var model =
        Model.assembler()
            .discoverModels()
            .addUnparsedModel(
                "document-result.smithy",
                """
                $version: "2"
                namespace example
                use spitzeisen.api#result
                service ExampleService { version: "1", operations: [Get] }
                @result(path: ["value"], cardinality: "collection")
                operation Get { input := {}, output := { value: Document } }
                """)
            .assemble()
            .unwrap();
    var prepared =
        ModelPreparation.prepare(
            model, new ServicePlanSettings(Optional.of(ShapeId.from("example#ExampleService"))));
    var result =
        new ServicePlanCompiler(prepared.model(), prepared.serviceId())
            .compile()
            .node()
            .expectObjectMember("operations")
            .expectObjectMember("example#Get")
            .expectObjectMember("policies")
            .expectObjectMember("result");

    assertEquals("collection", result.expectStringMember("cardinality").getValue());
    assertFalse(result.containsMember("value_shape"));
  }

  @Test
  void portablePoliciesRejectUnresolvableMemberReferences() {
    var resultError =
        compileError(
            """
            $version: "2"
            namespace example
            use spitzeisen.api#result
            service ExampleService { version: "1", operations: [Get] }
            @result(path: ["missing"])
            operation Get { input := {}, output := { value: String } }
            """);
    assertTrue(resultError.getMessage().contains("references missing member"));

    var sortingError =
        compileError(
            """
            $version: "2"
            namespace example
            use spitzeisen.api#sorting
            service ExampleService { version: "1", operations: [Get] }
            @sorting(encoding: "separate", sortMember: "sort")
            operation Get { input := { sort: String }, output := {} }
            """);
    assertTrue(sortingError.getMessage().contains("orderMember is required"));
  }

  @Test
  void rejectsAmbiguousPaginationPolicies() {
    var error =
        compileError(
            """
            $version: "2"
            namespace example
            use smithy.api#paginated
            use spitzeisen.api#pageNumberPagination
            service ExampleService { version: "1", operations: [List] }
            @paginated(inputToken: "next", outputToken: "next", items: "items")
            @pageNumberPagination(pageMember: "page")
            operation List {
                input := { next: String, page: Integer }
                output := { next: String, items: ItemList }
            }
            list ItemList { member: String }
            """);

    assertTrue(error.getMessage().contains("cannot use both"));
  }

  private static IllegalArgumentException compileError(String source) {
    var model =
        Model.assembler()
            .discoverModels()
            .addUnparsedModel("model.smithy", source)
            .assemble()
            .unwrap();
    var prepared =
        ModelPreparation.prepare(
            model, new ServicePlanSettings(Optional.of(ShapeId.from("example#ExampleService"))));
    return assertThrows(
        IllegalArgumentException.class,
        () -> new ServicePlanCompiler(prepared.model(), prepared.serviceId()).compile());
  }
}
