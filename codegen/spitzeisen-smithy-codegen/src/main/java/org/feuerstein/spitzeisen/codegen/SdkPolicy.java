package org.feuerstein.spitzeisen.codegen;

import java.util.ArrayList;
import java.util.List;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.knowledge.OperationIndex;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeType;

/** Portable Spitzeisen policy evaluated directly against the validated Smithy model. */
final class SdkPolicy {
  private SdkPolicy() {}

  /**
   * Reads a portable policy trait; absence is represented by an empty object.
   *
   * @return resolved Python source or identifier
   * @param shape modeled shape
   * @param name name to resolve
   */
  static ObjectNode of(Shape shape, String name) {
    return trait(shape, "spitzeisen.api#" + name);
  }

  /**
   * Reads one exact trait without reserializing the Smithy model.
   *
   * @return resolved Python source or identifier
   * @param shape modeled shape
   * @param id fully qualified trait ID
   */
  static ObjectNode trait(Shape shape, String id) {
    return shape
        .findTrait(id)
        .map(value -> value.toNode().expectObjectNode())
        .orElse(Node.objectNode());
  }

  /**
   * Resolves the logical result path and cardinality, retaining actual member identities.
   *
   * @return resolved value
   * @param model assembled semantic model
   * @param operation operation whose result is selected
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  static Result result(Model model, OperationShape operation) {
    var explicit = of(operation, "result");
    var members = new ArrayList<MemberShape>();
    Shape value = OperationIndex.of(model).expectOutputShape(operation);
    var path = explicit.getArrayMember("path");
    if (path.isPresent()) {
      for (Node segment : path.get().getElements()) {
        var structure =
            value
                .asStructureShape()
                .orElseThrow(
                    () ->
                        new IllegalArgumentException(
                            "result path must traverse structures on " + operation.getId()));
        String name = segment.expectStringNode().getValue();
        var member =
            structure
                .getMember(name)
                .orElseThrow(
                    () ->
                        new IllegalArgumentException(
                            "result path references missing member "
                                + structure.getId().withMember(name)));
        members.add(member);
        value = model.expectShape(member.getTarget());
      }
    } else {
      var payloads =
          HttpBindingIndex.of(model).getResponseBindings(operation, HttpBinding.Location.PAYLOAD);
      if (!payloads.isEmpty()) {
        var payload = payloads.get(0).getMember();
        members.add(payload);
        value = model.expectShape(payload.getTarget());
      }
    }
    boolean collection = value.isListShape() || value.getType() == ShapeType.SET;
    if (value.isDocumentShape()) {
      collection = "collection".equals(explicit.expectStringMember("cardinality").getValue());
    } else if (explicit.containsMember("cardinality")) {
      throw new IllegalArgumentException(
          "result cardinality may only be set for a Document target on " + operation.getId());
    }
    return new Result(List.copyOf(members), value, collection);
  }

  /** Logical result before a target chooses its public return representation. */
  record Result(List<MemberShape> path, Shape value, boolean collection) {}
}
