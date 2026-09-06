package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.ServiceLoader;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;
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
      use alloy#simpleRestJson
      @simpleRestJson
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
      list HeaderValues { member: String }
      """;

  @Test
  void pluginIsDiscoveredAndGeneratesSourcesNotAnIr() throws Exception {
    assertTrue(
        ServiceLoader.load(SmithyBuildPlugin.class).stream()
            .anyMatch(provider -> provider.type() == SpitzeisenPythonClientCodegenPlugin.class));
    generate(MODEL, "{}");
    var source = Files.readString(output.resolve("sdk/_async/_generated/get.py"));
    assertTrue(source.contains("async def get_raw("));
    assertTrue(source.contains("key: str | None = None,"));
    assertTrue(source.contains("greedy=True"));
    assertTrue(source.contains("coerce_timestamp(since, \"date-time\""));
    assertTrue(source.contains("coerce_timestamp(modified, \"http-date\""));
    assertTrue(source.contains("return self._validate_response(raw, Item)"));
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(
        Set.of("files", "models", "model_names", "model_aliases", "dependencies", "warnings"),
        manifest.getStringMap().keySet());
    assertFalse(Files.exists(output.resolve("service-plan.json")));
    assertTrue(manifest.expectObjectMember("files").expectBooleanMember("__init__.py").getValue());
    assertFalse(manifest.expectObjectMember("files").expectBooleanMember("_exports.py").getValue());
    try (var paths = Files.walk(output.resolve("sdk"))) {
      assertEquals(
          manifest.expectObjectMember("files").getStringMap().keySet(),
          paths
              .filter(Files::isRegularFile)
              .map(path -> output.resolve("sdk").relativize(path).toString().replace('\\', '/'))
              .collect(Collectors.toSet()));
    }
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
    assertFalse(Files.exists(output.resolve("sdk")));
    assertFalse(Files.exists(output.resolve("model-schema.json")));
  }

  @Test
  void resourceOperationsSupportMixinsSharedIoAndRecursiveModels() throws Exception {
    generate(
        """
        $version: "2"
        namespace example
        use alloy#simpleRestJson
        @simpleRestJson
        service ExampleService {
            version: "1"
            resources: [Items]
            operations: [Other, Excluded]
        }
        resource Items { operations: [Get] }
        @readonly @http(method: "GET", uri: "/items")
        operation Get { input: SharedInput, output: SharedOutput }
        @readonly @http(method: "GET", uri: "/other")
        operation Other { input: SharedInput, output: SharedOutput }
        @spitzeisen.api#excludeOperation
        @http(method: "POST", uri: "/excluded")
        operation Excluded { input: SharedInput, output: SharedOutput }
        @mixin
        structure Query { @httpQuery("search") search: String }
        structure SharedInput with [Query] {}
        structure SharedOutput { @httpPayload item: Item }
        structure Item { @required id: String, child: Item }
        """,
        "{}");
    for (String surface : Set.of("_async", "_sync")) {
      var generated = output.resolve("sdk/" + surface + "/_generated");
      for (String operation : Set.of("get", "other")) {
        var source = Files.readString(generated.resolve(operation + ".py"));
        assertTrue(source.contains("search: str | None = None,"));
        assertTrue(source.contains("return self._validate_response(raw, Item)"));
        assertTrue(Files.exists(output.resolve("sdk/" + surface + "/" + operation + ".py")));
      }
      var client = Files.readString(generated.resolve("client.py"));
      assertTrue(client.contains("self.get_api = "));
      assertTrue(client.contains("self.other_api = "));
      assertFalse(client.contains("excluded"));
      assertFalse(Files.exists(generated.resolve("excluded.py")));
    }
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(1, manifest.expectArrayMember("models").size());
    var schema =
        Node.parse(Files.readString(output.resolve("model-schema.json"))).expectObjectNode();
    var definitions = schema.expectObjectMember("$defs");
    assertEquals(Set.of("Item"), definitions.getStringMap().keySet());
    assertTrue(Node.printJson(definitions).contains("#/$defs/Item"));
  }

  @Test
  void namingDoesNotDependOnServiceOperationOrder() throws Exception {
    var source =
        MODEL
                .replace("operations: [Get]", "operations: [Get, Other]")
                .replace(
                    "operation Get",
                    "@spitzeisen.python#operation(module: \"lookup\", method: \"lookup\", accessor: \"lookup\") operation Get")
            + """
            @readonly @http(method: "GET", uri: "/other")
            @spitzeisen.python#operation(module: "lookup", method: "lookup", accessor: "lookup")
            operation Other { output := { @httpPayload item: Item } }
            """;
    var first = output.resolve("first");
    var second = output.resolve("second");
    generate(source, "{}", first);
    generate(source.replace("operations: [Get, Other]", "operations: [Other, Get]"), "{}", second);
    assertEquals(artifacts(first), artifacts(second));
    assertTrue(
        Files.readString(first.resolve("sdk/_async/_generated/lookup.py"))
            .contains("/items/{key}"));
    assertTrue(
        Files.readString(first.resolve("sdk/_async/_generated/lookup_2.py")).contains("/other"));
  }

  @Test
  void renderingFailuresDoNotFlushPendingSdkFiles() {
    var source =
        MODEL.replace("operations: [Get]", "operations: [Get, Other]")
            + """
            @readonly @http(method: "GET", uri: "/other")
            operation Other {
                input := {
                    @httpQuery("search")
                    @spitzeisen.api#inputAdapter(id: "missing")
                    search: String
                }
                output := { @httpPayload item: Item }
            }
            """;
    var error = assertThrows(IllegalArgumentException.class, () -> generate(source, "{}"));
    assertTrue(error.getMessage().contains("input adapter is not registered: missing"));
    assertFalse(Files.exists(output.resolve("sdk")));
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @Test
  void sharedResponseModelsHaveOneScaffold() throws Exception {
    String second =
        "@readonly @http(method: \"GET\", uri: \"/other\") operation Other { output := { @httpPayload item: Item } }";
    generate(MODEL.replace("operations: [Get]", "operations: [Get, Other]") + second, "{}");
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(1, manifest.expectArrayMember("models").size());
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "{\"enabled_integrations\":[\"unused\"]}",
        "{\"require_api_required_arguments\":\"true\"}",
        "{\"strict_response_validation\":1}",
        "{\"external_models\":{\"example#Item\":{\"module\":\"bad-import\",\"symbol\":\"Record\"}}}",
        "{\"external_models\":{\"example#Absent\":{\"module\":\"external\",\"symbol\":\"Record\"}}}",
        "{\"input_adapters\":{\"bad\":{\"function\":{\"module\":\"m\",\"name\":\"f\"},\"public_type\":\"str\"}}}"
      })
  void rejectsInvalidSettings(String settings) {
    assertThrows(RuntimeException.class, () -> generate(MODEL, settings));
  }

  @Test
  void compatibilityOptInsAreExplicitAndDiagnosed() throws Exception {
    generate(MODEL, "{\"require_api_required_arguments\":true}");
    var source = Files.readString(output.resolve("sdk/_async/_generated/get.py"));
    assertTrue(source.contains("key: str,"));
    assertFalse(source.contains("key: str | None"));
    var manifest = Node.parse(Files.readString(output.resolve("manifest.json"))).expectObjectNode();
    assertEquals(1, manifest.expectArrayMember("warnings").size());
  }

  @Test
  void responseValidationIsConfiguredAtRuntime() {
    var error =
        assertThrows(
            IllegalArgumentException.class,
            () -> generate(MODEL, "{\"strict_response_validation\":false}"));
    assertTrue(error.getMessage().contains("runtime client configuration"));
  }

  @Test
  void clientOptionalCannotBeOverriddenByCustomDefaults() {
    var source =
        MODEL.replace(
            "@httpQuery(\"since\") since: Timestamp",
            "@clientOptional @spitzeisen.api#clientDefault(value: \"old\") @httpQuery(\"since\") since: String");
    var error = assertThrows(IllegalArgumentException.class, () -> generate(source, "{}"));
    assertTrue(error.getMessage().contains("clientDefault conflicts with @clientOptional"));
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
        RuntimeException.class, () -> generate(MODEL.replace("@simpleRestJson", ""), "{}"));
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "@spitzeisen.api#queryEncoding(style: \"form\", explode: false) @httpQuery(\"since\") since: String",
        "@httpQuery(\"since\") since: alloy#LocalDate",
        "@httpHeader(\"X-Values\") values: HeaderValues",
        "@httpPayload body: String"
      })
  void unsupportedInputSemanticsAreRejected(String input) {
    var error =
        assertThrows(
            RuntimeException.class,
            () -> generate(MODEL.replace("@httpQuery(\"since\") since: Timestamp", input), "{}"));
    assertTrue(error.getMessage().contains("support"));
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "structure Item { value: Choice }\nunion Choice { a: String, b: Integer }",
        "structure Item { value: Blob }",
        "structure Item { @timestampFormat(\"epoch-seconds\") value: Timestamp }",
        "structure Item { @alloy#nullable value: String }"
      })
  void unsupportedResponseSemanticsAreRejectedRecursively(String response) {
    var error =
        assertThrows(
            RuntimeException.class,
            () ->
                generate(MODEL.replace("structure Item { @required id: String }", response), "{}"));
    assertTrue(error.getMessage().contains("Alloy client subset"), error.getMessage());
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @Test
  void unsupportedProtocolCannotBeSelectedByPreference() {
    assertThrows(
        RuntimeException.class,
        () ->
            generate(
                MODEL
                    .replace("@simpleRestJson", "@other")
                    .replace(
                        "use alloy#simpleRestJson",
                        "@trait(selector: \"service\") @protocolDefinition structure other {}"),
                "{\"protocol_preference\":[\"example#other\"]}"));
    assertFalse(Files.exists(output.resolve("manifest.json")));
  }

  @Test
  void scalarResponsesCannotBypassTheSubsetWithAnExternalModel() {
    var error =
        assertThrows(
            RuntimeException.class,
            () ->
                generate(
                    MODEL.replace("structure Item { @required id: String }", "string Item"),
                    "{\"external_models\":{\"example#Item\":{\"module\":\"external\",\"symbol\":\"Item\"}}}"));
    assertTrue(error.getMessage().contains("response records must be JSON objects"));
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
    generate(source, pythonSettings, output);
  }

  private static Map<String, String> artifacts(Path root) throws Exception {
    var result = new TreeMap<String, String>();
    try (var paths = Files.walk(root)) {
      for (var path : paths.filter(Files::isRegularFile).toList()) {
        result.put(root.relativize(path).toString(), Files.readString(path));
      }
    }
    return result;
  }

  private static void generate(String source, String pythonSettings, Path output) {
    var model =
        Model.assembler()
            .discoverModels()
            .addUnparsedModel("model.smithy", source)
            .assemble()
            .unwrap();
    var settings =
        Node.parse(
                "{\"service\":\"example#ExampleService\",\"package\":\"test_sdk\",\"client_name\":\"Client\",\"python\":"
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
