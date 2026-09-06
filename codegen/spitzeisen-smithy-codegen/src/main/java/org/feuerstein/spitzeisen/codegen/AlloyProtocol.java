package org.feuerstein.spitzeisen.codegen;

import java.util.Set;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.knowledge.OperationIndex;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.traits.TimestampFormatTrait;

/** The model capabilities implemented by Spitzeisen's Alloy JSON client. */
final class AlloyProtocol {
  static final String ID = "alloy#simpleRestJson";
  private static final Set<String> UNSUPPORTED_TRAITS =
      Set.of(
          "smithy.api#endpoint",
          "smithy.api#hostLabel",
          "smithy.api#streaming",
          "smithy.api#mediaType",
          "smithy.api#httpChecksumRequired",
          "smithy.api#requestCompression",
          "spitzeisen.api#queryEncoding");

  private AlloyProtocol() {}

  /**
   * Rejects unsupported wire semantics before any Python files are emitted.
   *
   * @param model assembled model
   * @param operation operation included in generation
   */
  static void validate(Model model, OperationShape operation) {
    var bindings = HttpBindingIndex.of(model);
    for (var shape : new Walker(model).walkShapes(operation)) {
      for (var trait : shape.getAllTraits().keySet()) {
        if (UNSUPPORTED_TRAITS.contains(trait.toString())
            || (trait.getNamespace().equals("alloy") && !trait.toString().equals(ID))) {
          throw unsupported(
              operation,
              shape,
              "trait "
                  + trait
                  + "; use a modeled String/Document and a Python adapter if appropriate");
        }
      }
      if (shape.isUnionShape() || shape.isBlobShape()) {
        throw unsupported(operation, shape, shape.getType() + " encoding");
      }
    }
    // Pydantic's date-time parsing implements our response timestamp subset. Other timestamp
    // representations need explicit codecs; accepting them here would silently change units.
    for (var shape :
        new Walker(model).walkShapes(OperationIndex.of(model).expectOutputShape(operation))) {
      if (shape.isMemberShape()) {
        var member = shape.asMemberShape().orElseThrow();
        // Enum members target the synthetic Unit shape, which service simplification can omit.
        if (model.getShape(member.getTarget()).filter(Shape::isTimestampShape).isPresent()
            && bindings.determineTimestampFormat(
                    member, HttpBinding.Location.DOCUMENT, TimestampFormatTrait.Format.DATE_TIME)
                != TimestampFormatTrait.Format.DATE_TIME) {
          throw unsupported(operation, member, "response timestamps other than date-time");
        }
      }
    }
  }

  private static IllegalArgumentException unsupported(
      OperationShape operation, Shape shape, String feature) {
    return new IllegalArgumentException(
        operation.getId()
            + ": Alloy client subset does not support "
            + feature
            + " on "
            + shape.getId());
  }
}
