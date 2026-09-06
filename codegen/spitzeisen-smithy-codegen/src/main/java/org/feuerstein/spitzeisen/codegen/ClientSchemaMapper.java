package org.feuerstein.spitzeisen.codegen;

import java.util.ArrayList;
import java.util.List;
import software.amazon.smithy.jsonschema.JsonSchemaMapper;
import software.amazon.smithy.jsonschema.JsonSchemaMapperContext;
import software.amazon.smithy.jsonschema.Schema;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.ShapeType;

/** Applies non-authoritative client semantics to Smithy's JSON Schema projection. */
final class ClientSchemaMapper implements JsonSchemaMapper {
  @Override
  public byte getOrder() {
    return 127;
  }

  @Override
  public Schema.Builder updateSchema(JsonSchemaMapperContext context, Schema.Builder builder) {
    var shape = context.getShape();
    var model = context.getModel();
    // Preserve constraints for optional runtime checks in the generated package.
    // Referenced members bypass the converter's member hook; rewrite properties on the container.
    if (shape.isStructureShape()) {
      var properties = builder.build().getProperties();
      var required = new ArrayList<String>();
      for (var member : shape.members()) {
        String name = PythonOperation.jsonName(member);
        var property = properties.get(name);
        if (property == null) {
          continue;
        }
        Node value = MemberPresence.modeledDefault(model, member);
        if (MemberPresence.clientNullable(model, member)) {
          property = nullable(property);
          if (value == null) {
            value = Node.nullNode();
          }
        } else if (value == null) {
          required.add(name);
        }
        builder.putProperty(name, property.toBuilder().defaultValue(value).build());
      }
      builder.required(required);
    } else if (shape.isListShape() || shape.getType() == ShapeType.SET) {
      var member = shape.members().iterator().next();
      if (MemberPresence.clientNullable(model, member)) {
        builder.items(nullable(builder.build().getItems().orElseThrow()));
      }
    } else if (shape.isMapShape()) {
      var member = shape.asMapShape().orElseThrow().getValue();
      if (MemberPresence.clientNullable(model, member)) {
        var schema = builder.build();
        schema
            .getAdditionalProperties()
            .ifPresent(value -> builder.additionalProperties(nullable(value)));
        schema
            .getPatternProperties()
            .forEach((key, value) -> builder.putPatternProperty(key, nullable(value)));
      }
    }
    return builder;
  }

  private static Schema nullable(Schema schema) {
    return Schema.builder()
        .description(schema.getDescription().orElse(null))
        .anyOf(List.of(schema, Schema.builder().type("null").build()))
        .build();
  }
}
