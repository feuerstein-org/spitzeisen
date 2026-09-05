package org.feuerstein.spitzeisen.codegen;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import software.amazon.smithy.build.FileManifest;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.ServiceIndex;
import software.amazon.smithy.model.knowledge.TopDownIndex;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeId;
import software.amazon.smithy.model.traits.HttpTrait;

/** Emits a Python SDK and only the metadata needed by the existing Pydantic model backend. */
final class PythonClientGenerator {
  private static final Set<String> PROTOCOLS =
      Set.of("spitzeisen.protocols#genericRestJson", "aws.protocols#restJson1");
  private final Model model;
  private final PythonSettings settings;
  private final FileManifest manifest;
  private final PythonSymbols symbols;
  private final List<PythonOperation> operations;
  private final ObjectNode.Builder files = ObjectNode.builder();

  /**
   * Creates a generator for a prepared model.
   *
   * @param model assembled semantic model
   * @param settings validated Python target settings
   * @param manifest Smithy build output destination
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  PythonClientGenerator(Model model, PythonSettings settings, FileManifest manifest) {
    this.model = model;
    this.settings = settings;
    this.manifest = manifest;
    var service = model.expectShape(settings.service(), ServiceShape.class);
    var declared =
        ServiceIndex.of(model).getProtocols(service).keySet().stream()
            .map(ShapeId::toString)
            .sorted()
            .toList();
    Stream.concat(settings.protocols().stream(), declared.stream())
        .filter(declared::contains)
        .filter(PROTOCOLS::contains)
        .findFirst()
        .orElseThrow(
            () ->
                new IllegalArgumentException(
                    "service protocols "
                        + declared
                        + " have no Python handler; supported: "
                        + PROTOCOLS));
    var closure = new Walker(model).walkShapes(service);
    for (String id : settings.externalModels().keySet()) {
      if (!closure.contains(model.expectShape(ShapeId.from(id)))) {
        throw new IllegalArgumentException("external model is outside the service closure: " + id);
      }
    }
    symbols = new PythonSymbols(model, settings);
    symbols.reserve("modules", Set.of("client", "__init__", "_generated"));
    symbols.reserve(
        "methods",
        Set.of(
            "__init__",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__",
            "_request",
            "_request_optional",
            "_http_client",
            "_paginate",
            "_get_all_pages",
            "_get_all_pages_optional",
            "_validate_input",
            "_validate_records",
            "_resolve_validation_mode"));
    symbols.reserve(
        "accessors",
        Set.of(
            "config",
            "_operation_apis",
            "close",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__"));
    symbols.reserve(
        "models", Set.of("Async" + settings.clientName(), "Sync" + settings.clientName()));
    // Allocate model names in shape order, independent of operation traversal.
    closure.stream().filter(Shape::isStructureShape).sorted().forEach(symbols::toSymbol);
    operations =
        TopDownIndex.of(model).getContainedOperations(service).stream()
            .sorted()
            .filter(operation -> !operation.hasTrait("spitzeisen.api#excludeOperation"))
            .map(operation -> new PythonOperation(model, settings, symbols, operation))
            .toList();
    if (operations.isEmpty()) {
      throw new IllegalArgumentException("the selected service has no included operations");
    }
  }

  /**
   * Writes source files, a build manifest, and the response-only JSON Schema sidecar.
   *
   * @throws IllegalArgumentException if an external model is nested in a generated model
   */
  void generate() {
    var models = new LinkedHashMap<ShapeId, PythonOperation>();
    operations.forEach(operation -> models.putIfAbsent(operation.response.getId(), operation));
    for (boolean async : List.of(true, false)) {
      String folder = async ? "_async" : "_sync";
      String prefix = async ? "Async" : "Sync";
      emit(folder + "/__init__.py", "\"\"\"Public " + prefix + " API surface.\"\"\"\n", true);
      emit(
          folder + "/_generated/__init__.py",
          "\"\"\"Generated implementation. Do not edit.\"\"\"\n",
          false);
      for (var operation : operations) {
        emit(folder + "/_generated/" + operation.key + ".py", operation.render(async), false);
        emit(
            folder + "/" + operation.key + ".py",
            extension(
                settings.packageName() + "." + folder + "._generated." + operation.key,
                prefix + operation.className + "Base",
                prefix + operation.className),
            true);
      }
      emit(folder + "/_generated/client.py", client(async), false);
      emit(
          folder + "/client.py",
          extension(
              settings.packageName() + "." + folder + "._generated.client",
              prefix + settings.clientName() + "Base",
              prefix + settings.clientName()),
          true);
    }
    var publicModels =
        models.values().stream().filter(operation -> operation.generatedModel).toList();
    for (var operation : publicModels) {
      emit(
          operation.responseSymbol.getDefinitionFile(),
          extension(
              settings.packageName() + ".models._generated",
              operation.responseSymbol.getName(),
              operation.responseSymbol.getName()),
          true);
    }
    emit(
        "models/__init__.py",
        "\"\"\"Public models. Safe to customize.\"\"\"\nfrom "
            + settings.packageName()
            + ".models._exports import *  # noqa: F403\n",
        true);
    var facade = new PythonWriter(Set.of());
    var exports = new TreeSet<String>();
    for (String prefix : List.of("Async", "Sync")) {
      String name = prefix + settings.clientName();
      exports.add(
          facade.reference(
              settings.packageName()
                  + "."
                  + ("Async".equals(prefix) ? "_async" : "_sync")
                  + ".client",
              name));
    }
    publicModels.forEach(
        operation ->
            exports.add(
                facade.reference(
                    settings.packageName() + ".models", operation.responseSymbol.getName())));
    facade.write("__all__ = $L", PythonWriter.literal(strings(exports)));
    emit("_exports.py", facade.toString(), false);
    emit(
        "__init__.py",
        "\"\"\"Public SDK. Safe to customize.\"\"\"\nfrom "
            + settings.packageName()
            + "._exports import *  # noqa: F403\n",
        true);

    var roots =
        publicModels.stream()
            .map(operation -> operation.response.getId())
            .collect(java.util.stream.Collectors.toSet());
    manifest.writeJson(
        "model-schema.json",
        SpitzeisenPythonClientCodegenPlugin.createModelSchema(model, settings.service(), roots));
    var included = new HashSet<Shape>();
    var walker = new Walker(model);
    roots.forEach(root -> included.addAll(walker.walkShapes(model.expectShape(root))));
    for (var shape : included) {
      if (settings.externalModels().containsKey(shape.getId().toString())) {
        throw new IllegalArgumentException(
            "external model substitution inside generated models is not supported: "
                + shape.getId());
      }
    }
    var names = ObjectNode.builder();
    var aliases = ObjectNode.builder();
    var service = model.expectShape(settings.service(), ServiceShape.class);
    for (var shape : included.stream().filter(Shape::isStructureShape).sorted().toList()) {
      String name = symbols.toSymbol(shape).getName();
      names.withMember("#/$defs/" + shape.getId().getName(service), name);
      names.withMember("#/components/schemas/" + shape.getId().getName(), name);
      // Inline OpenAPI response schemas have generated class names rather than component refs.
      names.withMember(shape.getId().getName(), name);
      String scope = "fields:" + shape.getId();
      symbols.reserve(
          scope, Set.of("model_config", "model_fields", "model_validate", "model_dump"));
      for (var member : shape.members()) {
        String preferred =
            SdkPolicy.trait(member, "spitzeisen.python#modelField")
                .getStringMemberOrDefault("name", member.getMemberName());
        aliases.withMember(
            name + "." + PythonOperation.jsonName(member),
            symbols.allocate(scope, member.getId().toString(), PythonSymbols.snake(preferred)));
      }
    }
    var dependencies =
        Stream.concat(
                settings.externalModels().values().stream(),
                settings.inputAdapters().values().stream())
            .flatMap(value -> PythonSettings.dependencies(value.expectObjectNode()).stream())
            .distinct()
            .sorted()
            .toList();
    var responseModels =
        models.values().stream()
            .map(
                operation ->
                    ObjectNode.builder()
                        .withMember("name", operation.responseSymbol.getName())
                        .withMember("module", operation.responseSymbol.getNamespace())
                        .withMember("generated", operation.generatedModel)
                        .build())
            .toList();
    manifest.writeJson(
        "manifest.json",
        ObjectNode.builder()
            .withMember("files", files.build())
            .withMember("models", Node.fromNodes(responseModels))
            .withMember("model_names", names.build())
            .withMember("model_aliases", aliases.build())
            .withMember("dependencies", strings(dependencies))
            .withMember(
                "model_paths",
                strings(
                    operations.stream()
                        .filter(operation -> operation.generatedModel)
                        .map(
                            operation ->
                                operation.shape.expectTrait(HttpTrait.class).getUri().toString())
                        .distinct()
                        .sorted()
                        .toList()))
            .build());
  }

  private String client(boolean async) {
    String prefix = async ? "Async" : "Sync";
    String folder = async ? "_async" : "_sync";
    String name = prefix + settings.clientName() + "Base";
    var writer = new PythonWriter(Set.of(name, "config", "self", "args", "operation_api"));
    writer.write("class $L:", name).indent();
    writer.docs(
        "Client for " + settings.vendor() + ". Shares one configuration across operation APIs.");
    writer
        .write(
            "def __init__(self, config: $T) -> None:",
            PythonSymbols.symbol("spitzeisen", prefix + "SpitzeisenConfig").toBuilder()
                .putProperty("runtimeAnnotation", true)
                .build())
        .indent();
    writer.docs("Build all operation APIs around the supplied configuration.");
    writer.write("self.config = config");
    for (var operation : operations) {
      writer.write(
          "self.$L = $T(config)",
          operation.accessor,
          PythonSymbols.symbol(
              settings.packageName() + "." + folder + "." + operation.key,
              prefix + operation.className));
    }
    writer.write("self._operation_apis = (").indent();
    operations.forEach(operation -> writer.write("self.$L,", operation.accessor));
    writer.dedent().write(")").dedent();
    String a = async ? "a" : "";
    writer
        .write(
            "\n$Ldef __$Lenter__(self) -> $T:",
            async ? "async " : "",
            a,
            PythonSymbols.symbol("typing", "Self"))
        .indent();
    writer.docs("Enter each API while sharing one lazily-created HTTP client.");
    writer.write("for operation_api in self._operation_apis:").indent();
    writer.write("$Loperation_api.__$Lenter__()", async ? "await " : "", a).dedent();
    writer.write("return self").dedent();
    writer
        .write("\n$Ldef __$Lexit__(self, *args: object) -> None:", async ? "async " : "", a)
        .indent();
    writer.docs("Release each API and close the shared owned client after its last holder.");
    writer.write("for operation_api in reversed(self._operation_apis):").indent();
    writer
        .write("$Loperation_api.__$Lexit__(*args)", async ? "await " : "", a)
        .dedent()
        .dedent()
        .dedent();
    return writer.toString();
  }

  private String extension(String module, String base, String name) {
    var writer = new PythonWriter(Set.of(name));
    writer
        .write(
            "# Created once. Safe to customize.\nclass $L($T):",
            name,
            PythonSymbols.symbol(module, base))
        .indent();
    writer.docs("Public " + name + " extension point.");
    writer.dedent();
    return writer.toString();
  }

  private void emit(String path, String source, boolean createOnce) {
    if (files.build().containsMember(path)) {
      throw new IllegalArgumentException("duplicate generated path: " + path);
    }
    files.withMember(path, createOnce);
    manifest.writeFile("sdk/" + path, source);
  }

  private static Node strings(java.util.Collection<String> values) {
    return Node.fromNodes(values.stream().map(Node::from).toList());
  }
}
