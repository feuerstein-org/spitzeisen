package org.feuerstein.spitzeisen.codegen;

import java.util.Set;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ShapeId;

/** Target-neutral service plan and response result roots for the temporary model-schema sidecar. */
record CompiledServicePlan(ObjectNode node, Set<ShapeId> resultRoots) {}
