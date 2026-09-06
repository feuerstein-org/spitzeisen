package org.feuerstein.spitzeisen.codegen;

import java.util.List;
import java.util.logging.Logger;
import software.amazon.smithy.build.PluginContext;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.codegen.core.directed.CodegenDirector;
import software.amazon.smithy.model.node.Node;

/** Smithy Build entry point for the directed Python client generator. */
public final class SpitzeisenPythonClientCodegenPlugin implements SmithyBuildPlugin {
  public static final String NAME = "spitzeisen-python-client-codegen";

  @Override
  public String getName() {
    return NAME;
  }

  @Override
  public void execute(PluginContext context) {
    var settings = PythonSettings.from(context.getModel(), context.getSettings());
    settings.warnings().forEach(Logger.getLogger(getClass().getName())::warning);
    var director =
        new CodegenDirector<PythonWriter, PythonIntegration, GenerationContext, PythonSettings>();
    director.settings(settings);
    director.model(context.getModel());
    director.service(settings.service());
    director.fileManifest(context.getFileManifest());
    director.directedCodegen(new DirectedPythonCodegen());
    director.integrationClass(PythonIntegration.class);
    // Keep generation independent of incidental providers on the launcher's classpath.
    director.integrationFinder(List::of);
    director.integrationSettings(Node.objectNode());
    director.performDefaultCodegenTransforms();
    director.createDedicatedInputsAndOutputs();
    director.run();
  }
}
