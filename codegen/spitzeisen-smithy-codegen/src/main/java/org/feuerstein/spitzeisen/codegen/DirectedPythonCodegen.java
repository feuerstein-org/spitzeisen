package org.feuerstein.spitzeisen.codegen;

import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.codegen.core.directed.CreateContextDirective;
import software.amazon.smithy.codegen.core.directed.CreateSymbolProviderDirective;
import software.amazon.smithy.codegen.core.directed.CustomizeDirective;
import software.amazon.smithy.codegen.core.directed.DirectedCodegen;
import software.amazon.smithy.codegen.core.directed.GenerateEnumDirective;
import software.amazon.smithy.codegen.core.directed.GenerateErrorDirective;
import software.amazon.smithy.codegen.core.directed.GenerateIntEnumDirective;
import software.amazon.smithy.codegen.core.directed.GenerateOperationDirective;
import software.amazon.smithy.codegen.core.directed.GenerateServiceDirective;
import software.amazon.smithy.codegen.core.directed.GenerateStructureDirective;
import software.amazon.smithy.codegen.core.directed.GenerateUnionDirective;

/** Smithy's generation lifecycle for Spitzeisen clients and the Pydantic model backend. */
final class DirectedPythonCodegen
    implements DirectedCodegen<GenerationContext, PythonSettings, PythonIntegration> {
  @Override
  public SymbolProvider createSymbolProvider(
      CreateSymbolProviderDirective<PythonSettings> directive) {
    return new PythonSymbols(directive.model(), directive.settings());
  }

  @Override
  public GenerationContext createContext(
      CreateContextDirective<PythonSettings, PythonIntegration> directive) {
    return GenerationContext.from(directive);
  }

  @Override
  public void generateOperation(
      GenerateOperationDirective<GenerationContext, PythonSettings> directive) {
    var operation = directive.context().operations().get(directive.shape().getId());
    if (operation != null) {
      new PythonOperationGenerator(directive.context(), operation).generate();
    }
  }

  @Override
  public void generateService(
      GenerateServiceDirective<GenerationContext, PythonSettings> directive) {
    var context = directive.context();
    new PythonClientGenerator(
            context.settings(), context.operations().values(), context.writerDelegator())
        .generate();
  }

  @Override
  public void customizeAfterIntegrations(
      CustomizeDirective<GenerationContext, PythonSettings> directive) {
    new PythonModelGenerator(directive.context()).generate();
  }

  @Override
  public void generateStructure(
      GenerateStructureDirective<GenerationContext, PythonSettings> directive) {
    // The response-only schema is generated once for Pydantic after all operations.
  }

  @Override
  public void generateEnumShape(
      GenerateEnumDirective<GenerationContext, PythonSettings> directive) {
    // Enum metadata is preserved in the Pydantic schema, including runtime validation policy.
  }

  @Override
  public void generateIntEnumShape(
      GenerateIntEnumDirective<GenerationContext, PythonSettings> directive) {
    // Integer enums use the same schema backend as string enums.
  }

  @Override
  public void generateUnion(GenerateUnionDirective<GenerationContext, PythonSettings> directive) {
    // Alloy validation rejects unions reachable from included operations before generation.
  }

  @Override
  public void generateError(GenerateErrorDirective<GenerationContext, PythonSettings> directive) {
    // The current runtime exposes HTTP errors without a generated exception hierarchy.
  }
}
