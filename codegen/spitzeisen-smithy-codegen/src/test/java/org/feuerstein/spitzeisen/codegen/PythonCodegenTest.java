package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ServiceLoader;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import software.amazon.smithy.build.FileManifest;
import software.amazon.smithy.build.PluginContext;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;

final class PythonCodegenTest {
  @TempDir Path output;

  private static final String MODEL =
      """
      $version: "2"
      namespace example
      use spitzeisen.protocols#genericRestJson
      @genericRestJson
      service ExampleService { version: "1", operations: [Get] }
      @readonly
      @http(method: "GET", uri: "/items/{key+}", code: 200)
      operation Get {
          input := {
              @required @httpLabel key: String
              @httpQuery("since") since: Timestamp
              @httpHeader("If-Modified-Since") modified: Timestamp
          }
          output := { @httpPayload item: Item }
      }
      structure Item { @required id: String }
      """;

  @Test
  void pluginIsDiscoveredAndGeneratesSourcesNotAnIr() throws Exception {
    assertTrue(
        ServiceLoader.load(SmithyBuildPlugin.class).stream()
            .anyMatch(provider -> provider.type() == SpitzeisenPythonClientCodegenPlugin.class));
    generate(MODEL, "{}");
    var source = Files.readString(output.resolve("sdk/_async/_generated/get.py"));
    assertTrue(source.contains("async def get_raw("));
    assertTrue(source.contains("key: str,"));
    assertFalse(source.contains("key: str | None"));
    assertTrue(source.contains("greedy=True"));
    assertTrue(source.contains("coerce_timestamp(since, \"date-time\""));
    assertTrue(source.contains("coerce_timestamp(modified, \"http-date\""));
    assertTrue(source.contains("return Item.model_validate(raw)"));
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(
        Set.of("files", "models", "model_names", "model_aliases", "model_paths", "dependencies"),
        manifest.getStringMap().keySet());
    assertFalse(Files.exists(output.resolve("service-plan.json")));
    assertTrue(manifest.expectObjectMember("files").expectBooleanMember("__init__.py").getValue());
    assertFalse(manifest.expectObjectMember("files").expectBooleanMember("_exports.py").getValue());
  }

  @Test
  void modelNameMappingHonorsServiceRenames() throws Exception {
    generate(
        MODEL.replace(
            "operations: [Get]", "operations: [Get], rename: {\"example#Item\": \"RenamedItem\"}"),
        "{}");
    assertTrue(
        Files.readString(output.resolve("sdk/_sync/_generated/get.py"))
            .contains("renamed_item import RenamedItem"));
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(
        "RenamedItem",
        manifest
            .expectObjectMember("model_names")
            .expectStringMember("#/$defs/RenamedItem")
            .getValue());
    assertTrue(Files.exists(output.resolve("sdk/models/renamed_item.py")));
  }

  @Test
  void symbolsAndLiteralsAreSafeAndExact() {
    var writer = new PythonWriter(Set.of("Record", "str"));
    assertEquals("Record_2", writer.reference("one.models", "Record"));
    assertEquals("Record_3", writer.reference("two.models", "Record"));
    assertEquals("Record_2", writer.reference("one.models", "Record"));
    assertEquals("\"\\U0001f680\\n\\\"\\\\\\u0000\"", PythonWriter.quote("🚀\n\"\\\0"));
    assertEquals(
        "123456789012345678901234567890",
        PythonWriter.literal(Node.parse("123456789012345678901234567890")));
    assertEquals("[True, None]", PythonWriter.literal(Node.parse("[true,null]")));
    assertEquals("class_", PythonSymbols.snake("class"));
    assertThrows(
        IllegalArgumentException.class,
        () -> PythonSymbols.literals(Node.parse("[1.5]").expectArrayNode().getElements(), writer));
  }

  @Test
  void operationNamesHonorPresentationTraits() throws Exception {
    generate(
        MODEL.replace(
            "operation Get",
            "@spitzeisen.python#operation(module: \"lookup\", method: \"lookup\") operation Get"),
        "{}");
    assertTrue(
        Files.readString(output.resolve("sdk/_async/_generated/lookup.py"))
            .contains("async def lookup("));
  }

  @Test
  void nestedExternalModelsFailExplicitly() {
    String nested =
        MODEL.replace(
            "structure Item { @required id: String }",
            "structure Item { @required id: String, child: Child }\nstructure Child { name: String }");
    var error =
        assertThrows(
            IllegalArgumentException.class,
            () ->
                generate(
                    nested,
                    "{\"external_models\":{\"example#Child\":{\"module\":\"external\",\"symbol\":\"Child\"}}}"));
    assertTrue(error.getMessage().contains("inside generated models"));
  }

  @Test
  void sharedResponseModelsHaveOneScaffold() throws Exception {
    String second =
        "@readonly @http(method: \"GET\", uri: \"/other\") operation Other { output := { @httpPayload item: Item } }";
    generate(MODEL.replace("operations: [Get]", "operations: [Get, Other]") + second, "{}");
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(1, manifest.expectArrayMember("models").size());
    assertEquals(2, manifest.expectArrayMember("model_paths").size());
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "{\"enabled_integrations\":[\"unused\"]}",
        "{\"external_models\":{\"example#Item\":{\"module\":\"bad-import\",\"symbol\":\"Record\"}}}",
        "{\"external_models\":{\"example#Absent\":{\"module\":\"external\",\"symbol\":\"Record\"}}}",
        "{\"input_adapters\":{\"bad\":{\"function\":{\"module\":\"m\",\"name\":\"f\"},\"public_type\":\"str\"}}}"
      })
  void rejectsInvalidSettings(String settings) {
    assertThrows(RuntimeException.class, () -> generate(MODEL, settings));
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "@spitzeisen.api#result(path: [\"missing\"])",
        "@spitzeisen.api#pageNumberPagination(pageMember: \"since\")",
        "@spitzeisen.api#sorting(encoding: \"separate\", sortMember: \"since\")"
      })
  void rejectsInvalidPortablePolicies(String trait) {
    assertThrows(
        RuntimeException.class,
        () -> generate(MODEL.replace("operation Get", trait + "\noperation Get"), "{}"));
  }

  @Test
  void unsupportedMethodsAndProtocolsFailBeforeWritingSdkFiles() {
    assertThrows(
        RuntimeException.class,
        () -> generate(MODEL.replace("method: \"GET\"", "method: \"POST\""), "{}"));
    assertThrows(
        RuntimeException.class, () -> generate(MODEL.replace("@genericRestJson", ""), "{}"));
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @Test
  void excludesOnlyOptionalParameters() throws Exception {
    String optional =
        MODEL.replace(
            "@httpQuery(\"since\")", "@spitzeisen.api#excludeParameter @httpQuery(\"since\")");
    generate(optional, "{}");
    assertFalse(
        Files.readString(output.resolve("sdk/_async/_generated/get.py")).contains("since:"));
    assertThrows(
        RuntimeException.class,
        () ->
            generate(
                MODEL.replace("@httpLabel", "@spitzeisen.api#excludeParameter @httpLabel"), "{}"));
  }

  private void generate(String source, String pythonSettings) {
    var model =
        Model.assembler()
            .discoverModels()
            .addUnparsedModel("model.smithy", source)
            .assemble()
            .unwrap();
    var settings =
        Node.parse(
                "{\"package\":\"test_sdk\",\"client_name\":\"Client\",\"python\":"
                    + pythonSettings
                    + "}")
            .expectObjectNode();
    new SpitzeisenPythonClientCodegenPlugin()
        .execute(
            PluginContext.builder()
                .model(model)
                .settings(settings)
                .fileManifest(FileManifest.create(output))
                .build());
  }
}
