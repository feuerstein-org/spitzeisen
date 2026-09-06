package org.feuerstein.spitzeisen.codegen;

import software.amazon.smithy.codegen.core.SmithyIntegration;

/**
 * Integration type for the directed lifecycle; this target uses explicitly registered integrations.
 */
interface PythonIntegration
    extends SmithyIntegration<PythonSettings, PythonWriter, GenerationContext> {}
