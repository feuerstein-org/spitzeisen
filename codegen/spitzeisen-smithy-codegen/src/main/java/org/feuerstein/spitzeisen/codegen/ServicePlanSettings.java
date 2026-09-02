package org.feuerstein.spitzeisen.codegen;

import java.util.Optional;
import java.util.Set;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ShapeId;

/** Target-neutral settings supplied by a Smithy Build projection. */
record ServicePlanSettings(Optional<ShapeId> service) {
  /**
   * Parses the Smithy Build settings object.
   *
   * @param node projection settings
   * @return validated service-plan settings
   */
  static ServicePlanSettings fromNode(ObjectNode node) {
    node.expectNoAdditionalProperties(Set.of("service"));
    return new ServicePlanSettings(
        node.getStringMember("service").map(value -> ShapeId.from(value.getValue())));
  }
}
