package org.feuerstein.spitzeisen.codegen;

import java.util.List;
import java.util.Map;
import java.util.Set;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.ShapeId;

/** Python generator configuration, decoded once by Smithy's Node API. */
record PythonSettings(
    ShapeId service,
    String packageName,
    String clientName,
    String vendor,
    Map<String, Node> externalModels,
    Map<String, Node> inputAdapters,
    List<String> protocols) {

  /** Reads artifact identity and optional Python-only settings, rejecting unknown fields. */
  static PythonSettings from(Model model, ObjectNode node) {
    node.expectNoAdditionalProperties(
        Set.of("service", "package", "client_name", "vendor", "python"));
    ShapeId service =
        node.getStringMember("service")
            .map(value -> ShapeId.from(value.getValue()))
            .orElseGet(
                () -> {
                  if (model.getServiceShapes().size() != 1) {
                    throw new IllegalArgumentException(
                        "service is required when the model has multiple services");
                  }
                  return model.getServiceShapes().iterator().next().getId();
                });
    model.expectShape(service, ServiceShape.class);
    String packageName = module(node.expectStringMember("package").getValue());
    String clientName = identifier(node.expectStringMember("client_name").getValue());
    var target = node.getObjectMember("python").orElse(Node.objectNode());
    target.expectNoAdditionalProperties(
        Set.of("external_models", "input_adapters", "protocol_preference"));
    var external =
        target.getObjectMember("external_models").orElse(Node.objectNode()).getStringMap();
    external.forEach(
        (id, value) -> {
          ShapeId.from(id);
          var entry = value.expectObjectNode();
          entry.expectNoAdditionalProperties(Set.of("module", "symbol", "dependencies"));
          module(entry.expectStringMember("module").getValue());
          identifier(entry.expectStringMember("symbol").getValue());
          dependencies(entry);
        });
    var adapters =
        target.getObjectMember("input_adapters").orElse(Node.objectNode()).getStringMap();
    adapters.forEach(
        (id, value) -> {
          if (id.isBlank()) {
            throw new IllegalArgumentException("input adapter IDs cannot be blank");
          }
          var entry = value.expectObjectNode();
          entry.expectNoAdditionalProperties(Set.of("function", "public_type", "dependencies"));
          var function = entry.expectObjectMember("function");
          function.expectNoAdditionalProperties(Set.of("module", "name", "alias"));
          module(function.expectStringMember("module").getValue());
          identifier(function.expectStringMember("name").getValue());
          function.getStringMember("alias").ifPresent(alias -> identifier(alias.getValue()));
          PythonSymbols.configuredType(
              entry.expectObjectMember("public_type"), new PythonWriter(Set.of()));
          dependencies(entry);
        });
    var protocols =
        target.getArrayMember("protocol_preference").orElse(Node.arrayNode()).getElements().stream()
            .map(value -> ShapeId.from(value.expectStringNode().getValue()).toString())
            .toList();
    return new PythonSettings(
        service,
        packageName,
        clientName,
        node.getStringMemberOrDefault("vendor", service.getName()),
        external,
        adapters,
        protocols);
  }

  /** Validates an exact Python identifier used in configuration. */
  static String identifier(String value) {
    if (!value.matches("[A-Za-z_][A-Za-z0-9_]*") || PythonSymbols.RESERVED.contains(value)) {
      throw new IllegalArgumentException("invalid Python identifier: " + value);
    }
    return value;
  }

  /** Validates an importable absolute Python module. */
  static String module(String value) {
    for (String segment : value.split("\\.", -1)) {
      identifier(segment);
    }
    return value;
  }

  /** Returns explicit runtime requirements for the CLI's dependency report. */
  static List<String> dependencies(ObjectNode value) {
    return value.getArrayMember("dependencies").orElse(Node.arrayNode()).getElements().stream()
        .map(
            item -> {
              String requirement = item.expectStringNode().getValue();
              if (requirement.isBlank()
                  || requirement.contains("\n")
                  || requirement.contains("\r")) {
                throw new IllegalArgumentException(
                    "dependencies must be non-empty single-line requirements");
              }
              return requirement;
            })
        .toList();
  }
}
