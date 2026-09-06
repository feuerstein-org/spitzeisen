package org.feuerstein.spitzeisen.codegen;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import software.amazon.smithy.codegen.core.Symbol;
import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.utils.CaseUtils;

/** Python names and types reference Smithy's model directly, including service-context renames. */
final class PythonSymbols implements SymbolProvider {
  static final Set<String> RESERVED =
      Set.of(
          "False",
          "None",
          "True",
          "and",
          "as",
          "assert",
          "async",
          "await",
          "break",
          "class",
          "continue",
          "def",
          "del",
          "elif",
          "else",
          "except",
          "finally",
          "for",
          "from",
          "global",
          "if",
          "import",
          "in",
          "is",
          "lambda",
          "nonlocal",
          "not",
          "or",
          "pass",
          "raise",
          "return",
          "try",
          "while",
          "with",
          "yield",
          "match",
          "case",
          "type",
          "self",
          "cls");
  static final Set<String> BUILTINS =
      Set.of("int", "str", "float", "bool", "bytes", "dict", "list", "set", "object");
  private final PythonSettings settings;
  private final ServiceShape service;
  private final PythonNames names = new PythonNames();

  /**
   * Creates a symbol provider for a prepared service.
   *
   * @param model assembled semantic model
   * @param settings validated Python target settings
   */
  PythonSymbols(Model model, PythonSettings settings) {
    this.settings = settings;
    service = model.expectShape(settings.service(), ServiceShape.class);
    var closure = new Walker(model).walkShapes(service);
    names.reserve(
        "models", Set.of("Async" + settings.clientName(), "Sync" + settings.clientName()));
    // Allocate model names in shape order, independent of operation traversal.
    closure.stream().filter(Shape::isStructureShape).sorted().forEach(this::toSymbol);
  }

  @Override
  public Symbol toSymbol(Shape shape) {
    Node external = settings.externalModels().get(shape.getId().toString());
    if (external != null) {
      var entry = external.expectObjectNode();
      return symbol(
          entry.expectStringMember("module").getValue(),
          entry.expectStringMember("symbol").getValue());
    }
    String name =
        names.allocate("models", shape.getId().toString(), pascal(shape.getId().getName(service)));
    return symbol(settings.packageName() + ".models." + snake(name), name).toBuilder()
        .definitionFile("models/" + snake(name) + ".py")
        .build();
  }

  @Override
  public String toMemberName(MemberShape member) {
    return snake(member.getMemberName());
  }

  /**
   * Decodes the bounded public-type vocabulary for configured input adapters.
   *
   * @return resolved Python source or identifier
   * @param node structured Smithy value
   * @param writer destination source writer
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  static String configuredType(ObjectNode node, PythonWriter writer) {
    String kind = node.expectStringMember("kind").getValue();
    if ("symbol".equals(kind)) {
      node.expectNoAdditionalProperties(Set.of("kind", "module", "name"));
      return writer.reference(
          PythonSettings.module(node.expectStringMember("module").getValue()),
          PythonSettings.identifier(node.expectStringMember("name").getValue()));
    }
    if ("literal".equals(kind)) {
      node.expectNoAdditionalProperties(Set.of("kind", "values"));
      return literals(node.expectArrayMember("values").getElements(), writer);
    }
    if (Set.of("list", "set", "dict", "union").contains(kind)) {
      node.expectNoAdditionalProperties(Set.of("kind", "members"));
      var members = node.expectArrayMember("members").getElements();
      int count = "dict".equals(kind) || "union".equals(kind) ? 2 : 1;
      if (("union".equals(kind) && members.size() < count)
          || (!"union".equals(kind) && members.size() != count)) {
        throw new IllegalArgumentException("invalid number of members in Python " + kind + " type");
      }
      var types =
          members.stream().map(item -> configuredType(item.expectObjectNode(), writer)).toList();
      return "union".equals(kind)
          ? String.join(" | ", types)
          : kind + "[" + String.join(", ", types) + "]";
    }
    node.expectNoAdditionalProperties(Set.of("kind"));
    return switch (kind) {
      case "bool", "bytes", "float", "int", "str" -> kind;
      case "datetime" -> writer.reference("datetime", "datetime");
      case "decimal" -> writer.reference("decimal", "Decimal");
      case "date_input" ->
          "str | "
              + writer.reference("datetime", "date")
              + " | "
              + writer.reference("datetime", "datetime");
      case "document" -> "object";
      default -> throw new IllegalArgumentException("unsupported Python public type " + kind);
    };
  }

  /**
   * Renders enum choices using Python's Literal with exact integer/string values.
   *
   * @return resolved Python source or identifier
   * @param values string or integer enum values
   * @param writer destination source writer
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  static String literals(List<? extends Node> values, PythonWriter writer) {
    if (values.isEmpty()
        || values.stream()
            .anyMatch(
                value ->
                    !(value.isStringNode()
                        || (value.isNumberNode()
                            && value
                                .expectNumberNode()
                                .getValue()
                                .toString()
                                .matches("-?\\d+"))))) {
      throw new IllegalArgumentException(
          "Python enum literals must contain string or integer values");
    }
    return writer.reference("typing", "Literal")
        + values.stream().map(PythonWriter::literal).collect(Collectors.joining(", ", "[", "]"));
  }

  /**
   * Returns a Python symbol for an exact module export.
   *
   * @return resolved Python source or identifier
   * @param module absolute import module
   * @param name name to resolve
   */
  static Symbol symbol(String module, String name) {
    return Symbol.builder().namespace(module, ".").name(name).build();
  }

  /**
   * Normalizes a modeled name using Smithy's case conversion.
   *
   * @return resolved Python source or identifier
   * @param value modeled identifier
   */
  static String snake(String value) {
    String name = CaseUtils.toSnakeCase(value.replaceAll("[^A-Za-z0-9_]", "_"));
    if (name.isEmpty()) {
      name = "value";
    }
    if (Character.isDigit(name.charAt(0))) {
      name = "_" + name;
    }
    return RESERVED.contains(name) || BUILTINS.contains(name) ? name + "_" : name;
  }

  /**
   * Normalizes a modeled class name using Smithy's case conversion.
   *
   * @return resolved Python source or identifier
   * @param value modeled identifier
   */
  static String pascal(String value) {
    return CaseUtils.toPascalCase(snake(value));
  }
}
