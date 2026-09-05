package org.feuerstein.spitzeisen.codegen;

import java.util.HashSet;
import java.util.Set;
import software.amazon.smithy.build.PluginContext;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.codegen.core.directed.CodegenDirector;
import software.amazon.smithy.jsonschema.JsonSchemaConfig;
import software.amazon.smithy.jsonschema.JsonSchemaConverter;
import software.amazon.smithy.jsonschema.JsonSchemaVersion;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.ShapeId;
import software.amazon.smithy.model.transform.ModelTransformer;

/** Smithy Build plugin that generates Python clients directly from Smithy's semantic model. */
public final class SpitzeisenPythonClientCodegenPlugin implements SmithyBuildPlugin {
  public static final String NAME = "spitzeisen-python-client-codegen";

  @Override
  public String getName() {
    return NAME;
  }

  @Override
  public void execute(PluginContext context) {
    var settings = PythonSettings.from(context.getModel(), context.getSettings());
    var transformer = ModelTransformer.create();
    var model =
        CodegenDirector.simplifyModelForServiceCodegen(
            context.getModel(), settings.service(), transformer);
    model = transformer.createDedicatedInputAndOutput(model, "Input", "Output");
    new PythonClientGenerator(model, settings, context.getFileManifest()).generate();
  }

  /**
   * Creates a response-only schema using Smithy's maintained JSON Schema converter.
   *
   * @return resolved value
   * @param model assembled semantic model
   * @param serviceId selected service
   * @param roots included response roots
   */
  static Node createModelSchema(Model model, ShapeId serviceId, Set<ShapeId> roots) {
    var included = new HashSet<ShapeId>();
    var walker = new Walker(model);
    roots.forEach(
        root ->
            walker
                .walkShapes(model.expectShape(root))
                .forEach(shape -> included.add(shape.getId())));
    var config = new JsonSchemaConfig();
    config.setService(serviceId);
    config.setJsonSchemaVersion(JsonSchemaVersion.DRAFT2020_12);
    config.setUseIntegerType(true);
    config.setUseJsonName(true);
    config.setAddReferenceDescriptions(true);
    return JsonSchemaConverter.builder()
        .model(model)
        .config(config)
        .shapePredicate(shape -> included.contains(shape.getId()))
        .build()
        .convert()
        .toNode();
  }
}
