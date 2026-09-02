package org.feuerstein.spitzeisen.codegen;

import java.util.HashSet;
import java.util.Set;
import software.amazon.smithy.build.PluginContext;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.jsonschema.JsonSchemaConfig;
import software.amazon.smithy.jsonschema.JsonSchemaConverter;
import software.amazon.smithy.jsonschema.JsonSchemaVersion;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.ShapeId;

/** Smithy semantic frontend that emits Spitzeisen's runtime-neutral service plan. */
public final class SpitzeisenServicePlanPlugin implements SmithyBuildPlugin {
  public static final String NAME = "spitzeisen-service-plan";

  @Override
  public String getName() {
    return NAME;
  }

  @Override
  public void execute(PluginContext context) {
    var settings = ServicePlanSettings.fromNode(context.getSettings());
    var prepared = ModelPreparation.prepare(context.getModel(), settings);
    var compiled = new ServicePlanCompiler(prepared.model(), prepared.serviceId()).compile();
    context.getFileManifest().writeJson("service-plan.json", compiled.node());
    context
        .getFileManifest()
        .writeJson(
            "model-schema.json",
            createModelSchema(prepared.model(), prepared.serviceId(), compiled.resultRoots()));
  }

  /**
   * Creates the temporary JSON Schema sidecar for generated operation result shapes.
   *
   * @param model prepared semantic model
   * @param serviceId selected service
   * @param resultRoots non-excluded operation result roots
   * @return JSON Schema document
   */
  static Node createModelSchema(Model model, ShapeId serviceId, Set<ShapeId> resultRoots) {
    var included = new HashSet<ShapeId>();
    var walker = new Walker(model);
    for (var root : resultRoots) {
      walker.walkShapes(model.expectShape(root)).forEach(shape -> included.add(shape.getId()));
    }

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
