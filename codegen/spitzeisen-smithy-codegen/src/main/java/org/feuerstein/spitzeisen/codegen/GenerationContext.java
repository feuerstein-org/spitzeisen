package org.feuerstein.spitzeisen.codegen;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import software.amazon.smithy.build.FileManifest;
import software.amazon.smithy.codegen.core.CodegenContext;
import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.codegen.core.directed.CreateContextDirective;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.ServiceIndex;
import software.amazon.smithy.model.knowledge.TopDownIndex;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.ShapeId;

/** Shared Smithy generation state with validated operations in stable naming order. */
record GenerationContext(
    Model model,
    PythonSettings settings,
    SymbolProvider symbolProvider,
    PythonNames names,
    FileManifest fileManifest,
    PythonWriterDelegator writerDelegator,
    List<PythonIntegration> integrations,
    Map<ShapeId, PythonOperation> operations)
    implements CodegenContext<PythonSettings, PythonWriter, PythonIntegration> {

  /**
   * Prepares the supported service and validates every included operation before writing files.
   *
   * @param directive Smithy's prepared model, symbols, and output context
   * @return context for operation and service callbacks
   * @throws IllegalArgumentException if the selected service cannot be generated
   */
  static GenerationContext from(
      CreateContextDirective<PythonSettings, PythonIntegration> directive) {
    var model = directive.model();
    var settings = directive.settings();
    var symbols = directive.symbolProvider();
    var names = new PythonNames();
    var service = model.expectShape(settings.service(), ServiceShape.class);
    var protocols = ServiceIndex.of(model).getProtocols(service).keySet();
    if (!protocols.contains(ShapeId.from(AlloyProtocol.ID))) {
      throw new IllegalArgumentException(
          "service protocols "
              + protocols
              + " have no Python handler; supported: "
              + AlloyProtocol.ID);
    }
    var closure = new Walker(model).walkShapes(service);
    for (String id : settings.externalModels().keySet()) {
      if (!closure.contains(model.expectShape(ShapeId.from(id)))) {
        throw new IllegalArgumentException("external model is outside the service closure: " + id);
      }
    }
    var operations = new TreeMap<ShapeId, PythonOperation>();
    TopDownIndex.of(model).getContainedOperations(service).stream()
        .sorted()
        .filter(operation -> !operation.hasTrait("spitzeisen.api#excludeOperation"))
        .forEach(
            operation ->
                operations.put(
                    operation.getId(),
                    new PythonOperation(model, settings, symbols, names, operation)));
    if (operations.isEmpty()) {
      throw new IllegalArgumentException("the selected service has no included operations");
    }
    return new GenerationContext(
        model,
        settings,
        symbols,
        names,
        directive.fileManifest(),
        new PythonWriterDelegator(directive.fileManifest(), symbols),
        directive.integrations(),
        Collections.unmodifiableMap(operations));
  }
}
