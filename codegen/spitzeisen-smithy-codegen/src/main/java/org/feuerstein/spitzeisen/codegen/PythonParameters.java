package org.feuerstein.spitzeisen.codegen;

import static org.feuerstein.spitzeisen.codegen.PythonWriter.quote;

import java.util.List;
import java.util.stream.Collectors;
import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeType;
import software.amazon.smithy.model.traits.TimestampFormatTrait;

/** Maps modeled parameters to Python types, imports, and HTTP serialization expressions. */
record PythonParameters(
    Model model, PythonSettings settings, SymbolProvider symbols, OperationShape shape) {
  /**
   * Resolves a parameter annotation and serialization expression.
   *
   * @param parameter modeled argument
   * @param writer destination writer
   * @return Python type and coercion
   * @throws IllegalArgumentException if an input adapter has no registered implementation
   */
  Argument argument(Parameter parameter, PythonWriter writer) {
    var member = parameter.member();
    var target = model.expectShape(member.getTarget());
    var adapterPolicy = SdkPolicy.of(member, "inputAdapter");
    String type;
    String coercion = parameter.name();
    if (!adapterPolicy.isEmpty() && parameter.literals() == null) {
      String id = adapterPolicy.expectStringMember("id").getValue();
      var adapterNode = settings.inputAdapters().get(id);
      if (adapterNode == null) {
        throw new IllegalArgumentException(
            "input adapter is not registered: " + id + " on " + shape.getId());
      }
      var adapter = adapterNode.expectObjectNode();
      type = PythonSymbols.configuredType(adapter.expectObjectMember("public_type"), writer);
      var function = adapter.expectObjectMember("function");
      coercion =
          writer.reference(
                  PythonSymbols.symbol(
                          function.expectStringMember("module").getValue(),
                          function.expectStringMember("name").getValue())
                      .toBuilder()
                      .putProperty(
                          "alias",
                          function.getStringMemberOrDefault(
                              "alias", function.expectStringMember("name").getValue()))
                      .build())
              + "("
              + parameter.name()
              + ", param_name="
              + quote(parameter.name())
              + ")";
    } else {
      type =
          parameter.literals() == null
              ? type(target, writer)
              : PythonSymbols.literals(parameter.literals(), writer);
      Shape value = target;
      MemberShape subject = member;
      boolean multiple = target.isListShape() || target.getType() == ShapeType.SET;
      if (multiple) {
        subject = target.members().iterator().next();
        value = model.expectShape(subject.getTarget());
      }
      if (value.isTimestampShape()) {
        var defaultFormat =
            parameter.binding().getLocation() == HttpBinding.Location.HEADER
                ? TimestampFormatTrait.Format.HTTP_DATE
                : TimestampFormatTrait.Format.DATE_TIME;
        String format =
            HttpBindingIndex.of(model)
                .determineTimestampFormat(subject, parameter.binding().getLocation(), defaultFormat)
                .toString();
        coercion =
            writer.reference("spitzeisen", multiple ? "coerce_timestamps" : "coerce_timestamp")
                + "("
                + parameter.name()
                + ", "
                + quote(format)
                + ", "
                + quote(parameter.name())
                + ")";
      }
    }
    if (MemberPresence.inputNullable(model, member, settings)) {
      type += " | None";
    }
    if (!coercion.equals(parameter.name())) {
      coercion = "None if " + presenceValue(parameter, writer) + " is None else " + coercion;
    }
    return new Argument(parameter, type, coercion);
  }

  /**
   * Keeps the explicit None escape hatch available to strict input annotations.
   *
   * @param parameter modeled argument
   * @param writer destination writer
   * @return expression used to test presence
   */
  String presenceValue(Parameter parameter, PythonWriter writer) {
    // The opt-in annotation excludes None, but runtime omission is deliberately still supported.
    return MemberPresence.inputNullable(model, parameter.member(), settings)
        ? parameter.name()
        : writer.reference("typing", "cast") + "(object, " + parameter.name() + ")";
  }

  /**
   * Writes an explicit query binding or a map with explicit-name precedence.
   *
   * @param writer destination writer
   * @param argument resolved argument
   * @param value serialization expression
   */
  void queryValue(PythonWriter writer, Argument argument, String value) {
    if (argument.parameter().binding().getLocation() == HttpBinding.Location.QUERY_PARAMS) {
      var reserved =
          HttpBindingIndex.of(model).getRequestBindings(shape, HttpBinding.Location.QUERY).stream()
              .map(binding -> quote(binding.getLocationName()))
              .sorted()
              .collect(Collectors.joining(", "));
      writer.write(
          "*$L($L, reserved=[$L]),",
          writer.reference("spitzeisen", "serialize_query_map"),
          value,
          reserved);
      return;
    }
    writer.write(
        "*$L($L, name=$L),",
        writer.reference("spitzeisen", "serialize_query_param"),
        value,
        quote(argument.parameter().binding().getLocationName()));
  }

  /**
   * Requires a usable value for path labels at serialization.
   *
   * @param argument resolved argument
   * @param writer destination writer
   * @return expression for a header or path label
   */
  String required(Argument argument, PythonWriter writer) {
    return argument.parameter().binding().getLocation() != HttpBinding.Location.LABEL
        ? argument.coercion()
        : writer.reference("spitzeisen", "require_value")
            + "("
            + argument.coercion()
            + ", "
            + quote(argument.parameter().name())
            + ")";
  }

  /**
   * Identifies bindings that contribute query parameters.
   *
   * @param binding Smithy HTTP binding
   * @return whether the binding contributes query parameters
   */
  static boolean isQuery(HttpBinding binding) {
    return binding.getLocation() == HttpBinding.Location.QUERY
        || binding.getLocation() == HttpBinding.Location.QUERY_PARAMS;
  }

  /** A public parameter backed by its Smithy member and HTTP binding. */
  record Parameter(
      String name,
      MemberShape member,
      HttpBinding binding,
      Node defaultValue,
      List<? extends Node> literals) {}

  /** Python expressions resolved in a particular module writer. */
  record Argument(Parameter parameter, String type, String coercion) {}

  /**
   * Maps a supported HTTP input shape to a Python annotation.
   *
   * @return resolved Python source or identifier
   * @param shape modeled shape
   * @param writer destination source writer
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  private String type(Shape shape, PythonWriter writer) {
    if (settings.externalModels().containsKey(shape.getId().toString())) {
      return writer.reference(symbols.toSymbol(shape));
    }
    return switch (shape.getType()) {
      case BOOLEAN -> "bool";
      case BYTE, SHORT, INTEGER, LONG, BIG_INTEGER -> "int";
      case FLOAT, DOUBLE -> "float";
      case BIG_DECIMAL -> writer.reference("decimal", "Decimal");
      case STRING -> "str";
      case BLOB -> "bytes";
      case TIMESTAMP -> writer.reference("datetime", "datetime");
      case ENUM ->
          PythonSymbols.literals(
              shape.asEnumShape().orElseThrow().getEnumValues().values().stream()
                  .map(Node::from)
                  .toList(),
              writer);
      case INT_ENUM ->
          PythonSymbols.literals(
              shape.asIntEnumShape().orElseThrow().getEnumValues().values().stream()
                  .map(Node::from)
                  .toList(),
              writer);
      case LIST, SET ->
          (shape.isListShape() ? "list" : "set")
              + "["
              + memberType(shape.members().iterator().next(), writer)
              + "]";
      case MAP -> {
        var map = shape.asMapShape().orElseThrow();
        yield "dict["
            + type(model.expectShape(map.getKey().getTarget()), writer)
            + ", "
            + memberType(map.getValue(), writer)
            + "]";
      }
      default ->
          throw new IllegalArgumentException(
              "unsupported Python HTTP input shape " + shape.getId());
    };
  }

  private String memberType(
      software.amazon.smithy.model.shapes.MemberShape member, PythonWriter writer) {
    return type(model.expectShape(member.getTarget()), writer)
        + (MemberPresence.clientNullable(model, member) ? " | None" : "");
  }
}
