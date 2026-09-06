package org.feuerstein.spitzeisen.codegen;

import java.util.Collection;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import software.amazon.smithy.jsonschema.JsonSchemaConfig;
import software.amazon.smithy.jsonschema.JsonSchemaConverter;
import software.amazon.smithy.jsonschema.JsonSchemaVersion;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeId;

/** Owns the response projection and build artifacts consumed by the Pydantic backend. */
record PythonModelGenerator(GenerationContext context) {
  /** Writes public model facades, the client schema, and the build manifest. */
  void generate() {
    var model = context.model();
    var settings = context.settings();
    var symbols = context.symbolProvider();
    var naming = context.names();
    var writers = context.writerDelegator();
    var manifest = context.fileManifest();
    var models = new LinkedHashMap<ShapeId, PythonOperation>();
    context
        .operations()
        .values()
        .forEach(operation -> models.putIfAbsent(operation.response.getId(), operation));
    var publicModels =
        models.values().stream().filter(operation -> operation.generatedModel).toList();
    for (var operation : publicModels) {
      writers.extension(
          operation.responseSymbol.getDefinitionFile(),
          settings.packageName() + ".models._generated",
          operation.responseSymbol.getName(),
          operation.responseSymbol.getName());
    }
    writers.packageMarker(
        "models/__init__.py",
        "\"\"\"Public models. Safe to customize.\"\"\"\nfrom "
            + settings.packageName()
            + ".models._exports import *  # noqa: F403\n",
        true);
    writers.useSdkWriter(
        "_exports.py",
        false,
        facade -> {
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
        });
    writers.packageMarker(
        "__init__.py",
        "\"\"\"Public SDK. Safe to customize.\"\"\"\nfrom "
            + settings.packageName()
            + "._exports import *  # noqa: F403\n",
        true);

    var roots =
        publicModels.stream()
            .map(operation -> operation.response.getId())
            .collect(java.util.stream.Collectors.toSet());
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
      naming.reserve(scope, Set.of("model_config", "model_fields", "model_validate", "model_dump"));
      for (var member : shape.members()) {
        String preferred =
            SdkPolicy.trait(member, "spitzeisen.python#modelField")
                .getStringMemberOrDefault("name", member.getMemberName());
        aliases.withMember(
            name + "." + PythonOperation.jsonName(member),
            naming.allocate(scope, member.getId().toString(), PythonSymbols.snake(preferred)));
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
    manifest.writeJson("model-schema.json", createModelSchema(model, settings, included));
    manifest.writeJson(
        "manifest.json",
        ObjectNode.builder()
            .withMember("files", writers.files())
            .withMember("models", Node.fromNodes(responseModels))
            .withMember("model_names", names.build())
            .withMember("model_aliases", aliases.build())
            .withMember("dependencies", strings(dependencies))
            .withMember("warnings", strings(settings.warnings()))
            .build());
  }

  private static Node strings(Collection<String> values) {
    return Node.fromNodes(values.stream().map(Node::from).toList());
  }

  /**
   * Creates a response-only schema using Smithy's maintained JSON Schema converter.
   *
   * @return resolved value
   * @param model assembled semantic model
   * @param settings selected service and client policies
   * @param included response roots and their reachable shapes
   */
  private static Node createModelSchema(Model model, PythonSettings settings, Set<Shape> included) {
    var config = new JsonSchemaConfig();
    config.setService(settings.service());
    config.setJsonSchemaVersion(JsonSchemaVersion.DRAFT2020_12);
    config.setUseIntegerType(true);
    config.setUseJsonName(true);
    config.setAddReferenceDescriptions(true);
    config.setDisableDefaultValues(true);
    return JsonSchemaConverter.builder()
        .model(model)
        .config(config)
        .addMapper(new ClientSchemaMapper())
        .shapePredicate(included::contains)
        .build()
        .convert()
        .toNode();
  }
}
