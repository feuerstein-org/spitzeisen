package org.feuerstein.spitzeisen.codegen;

import java.util.Set;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ShapeId;

/** Renderer-ready client plan together with the response closure roots used for model generation. */
record CompiledClientPlan(ObjectNode node, Set<ShapeId> responseShapes) {}
