package org.feuerstein.spitzeisen.codegen;

import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.ShapeId;
import software.amazon.smithy.model.transform.ModelTransformer;

/** Applies the standard Smithy service-codegen model preparation sequence. */
final class ModelPreparation {
  private ModelPreparation() {}

  /**
   * Resolves the selected service and prepares a model specifically for client generation.
   *
   * <p>This mirrors the default {@code CodegenDirector} transforms used by Smithy language
   * generators: service errors are copied to operations, mixins are flattened, and every operation
   * receives dedicated input and output structures.
   *
   * @param model assembled and validated Smithy model
   * @param settings service selection
   * @return prepared model and selected service ID
   */
  static PreparedModel prepare(Model model, ServicePlanSettings settings) {
    var service = resolveService(model, settings);
    var transformer = ModelTransformer.create();
    var prepared = transformer.copyServiceErrorsToOperations(model, service);
    prepared = transformer.flattenAndRemoveMixins(prepared);
    prepared = transformer.createDedicatedInputAndOutput(prepared, "Input", "Output");
    return new PreparedModel(prepared, service.getId());
  }

  private static ServiceShape resolveService(Model model, ServicePlanSettings settings) {
    if (settings.service().isPresent()) {
      return model.expectShape(settings.service().orElseThrow(), ServiceShape.class);
    }
    if (model.getServiceShapes().size() != 1) {
      throw new IllegalArgumentException(
          "service is required when the model does not contain exactly one service");
    }
    return model.getServiceShapes().iterator().next();
  }

  /** A prepared semantic model together with its selected service. */
  record PreparedModel(Model model, ShapeId serviceId) {}
}
