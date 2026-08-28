package org.feuerstein.spitzeisen.codegen;

import java.util.Optional;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ShapeId;

/** Target-language settings supplied by smithy-build. */
record SpitzeisenSettings(
        Optional<ShapeId> service,
        String packageName,
        String clientName,
        Optional<String> vendor
) {
    static SpitzeisenSettings fromNode(ObjectNode node) {
        node.expectNoAdditionalProperties(java.util.Set.of("service", "package", "clientName", "vendor"));
        var service = node.getStringMember("service").map(value -> ShapeId.from(value.getValue()));
        var packageName = node.expectStringMember("package").getValue();
        var clientName = node.expectStringMember("clientName").getValue();
        var vendor = node.getStringMember("vendor").map(value -> value.getValue());
        if (packageName.isBlank()) {
            throw new IllegalArgumentException("package must not be blank");
        }
        if (clientName.isBlank()) {
            throw new IllegalArgumentException("clientName must not be blank");
        }
        return new SpitzeisenSettings(service, packageName, clientName, vendor);
    }
}
