/*
 * Portions derived from smithy-python's PythonWriter and ImportDeclarations.
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 * Modified for Spitzeisen: small absolute-import writer with eager alias allocation,
 * explicit local-name reservation, and Python literals without runtime dependencies.
 */
package org.feuerstein.spitzeisen.codegen;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;
import software.amazon.smithy.codegen.core.ImportContainer;
import software.amazon.smithy.codegen.core.Symbol;
import software.amazon.smithy.codegen.core.SymbolWriter;
import software.amazon.smithy.model.node.Node;

/** Python source writer using Smithy's indentation, templates, and symbol infrastructure. */
final class PythonWriter extends SymbolWriter<PythonWriter, PythonWriter.Imports> {
  /**
   * Creates a writer after reserving all names defined by the generated module and methods.
   *
   * @param reserved names already bound in this module
   */
  PythonWriter(Set<String> reserved) {
    super(new Imports(reserved));
    trimBlankLines();
    trimTrailingSpaces();
    putFormatter('T', (value, indent) -> reference((Symbol) value));
    putFormatter('P', (value, indent) -> literal((Node) value));
  }

  /**
   * References a symbol and records any required import, allocating an alias on collision.
   *
   * @return resolved Python source or identifier
   * @param symbol referenced Python symbol
   */
  String reference(Symbol symbol) {
    return getImportContainer().reference(symbol);
  }

  /**
   * References a Python module export.
   *
   * @return resolved Python source or identifier
   * @param module absolute import module
   * @param name name to resolve
   */
  String reference(String module, String name) {
    return reference(Symbol.builder().namespace(module, ".").name(name).build());
  }

  /**
   * Writes documentation as a Python string literal, keeping modeled text out of source syntax.
   *
   * @param text modeled text
   */
  void docs(String text) {
    String body =
        java.util.Arrays.stream(text.split("\n", -1))
            .map(
                line -> {
                  String encoded = quote(line);
                  return encoded.substring(1, encoded.length() - 1);
                })
            .collect(Collectors.joining("\n"));
    write(
        "$L",
        text.contains("\n")
            ? "\"\"\"\n" + body.stripTrailing() + "\n\n\"\"\""
            : "\"\"\"" + body + "\"\"\"");
  }

  /**
   * Encodes text as a Python string literal, including control characters and Unicode.
   *
   * @return resolved Python source or identifier
   * @param text modeled text
   */
  static String quote(String text) {
    var result = new StringBuilder("\"");
    for (int index = 0; index < text.length(); index++) {
      char value = text.charAt(index);
      if (Character.isHighSurrogate(value)
          && index + 1 < text.length()
          && Character.isLowSurrogate(text.charAt(index + 1))) {
        result.append(String.format("\\U%08x", Character.toCodePoint(value, text.charAt(++index))));
        continue;
      }
      switch (value) {
        case '"' -> result.append("\\\"");
        case '\\' -> result.append("\\\\");
        case '\n' -> result.append("\\n");
        case '\r' -> result.append("\\r");
        case '\t' -> result.append("\\t");
        default -> {
          if (value < 32 || value == 127 || Character.isSurrogate(value)) {
            result.append(String.format("\\u%04x", (int) value));
          } else {
            result.append(value);
          }
        }
      }
    }
    return result.append('"').toString();
  }

  /**
   * Encodes Smithy defaults as Python values without narrowing integer or decimal precision.
   *
   * @return resolved Python source or identifier
   * @param node structured Smithy value
   * @throws IllegalArgumentException if the model or setting cannot be represented by this target
   */
  static String literal(Node node) {
    if (node.isNullNode()) {
      return "None";
    }
    if (node.isStringNode()) {
      return quote(node.expectStringNode().getValue());
    }
    if (node.isBooleanNode()) {
      return node.expectBooleanNode().getValue() ? "True" : "False";
    }
    if (node.isNumberNode()) {
      var number = node.expectNumberNode();
      if (number.isNaN() || number.isInfinite()) {
        throw new IllegalArgumentException("Python defaults must be finite JSON numbers");
      }
      return number.getValue().toString();
    }
    if (node.isArrayNode()) {
      return node.expectArrayNode().getElements().stream()
          .map(PythonWriter::literal)
          .collect(Collectors.joining(", ", "[", "]"));
    }
    return node.expectObjectNode().getStringMap().entrySet().stream()
        .map(entry -> quote(entry.getKey()) + ": " + literal(entry.getValue()))
        .collect(Collectors.joining(", ", "{", "}"));
  }

  @Override
  public String toString() {
    return "# Generated by spitzeisen-gen.\nfrom __future__ import annotations\n\n"
        + getImportContainer()
        + "\n"
        + super.toString();
  }

  /** Deterministic absolute imports; Ruff owns their final grouping and wrapping. */
  static final class Imports implements ImportContainer {
    private final Set<String> used;
    private final Map<String, String> names = new HashMap<>();
    private final Map<String, String> lines = new TreeMap<>();

    private Imports(Set<String> reserved) {
      used = new HashSet<>(reserved);
    }

    private String reference(Symbol symbol) {
      if (symbol.getNamespace().isEmpty()) {
        return symbol.getName();
      }
      String key = symbol.getNamespace() + "." + symbol.getName();
      return names.computeIfAbsent(
          key,
          ignored -> {
            String name = symbol.getName();
            String preferred = symbol.getProperty("alias", String.class).orElse(name);
            String candidate = preferred;
            for (int suffix = 2; used.contains(candidate); suffix++) {
              candidate = preferred + "_" + suffix;
            }
            used.add(candidate);
            lines.put(
                key,
                "from "
                    + symbol.getNamespace()
                    + " import "
                    + name
                    + (candidate.equals(name) ? "" : " as " + candidate)
                    + (symbol.getProperty("runtimeAnnotation", Boolean.class).orElse(false)
                        ? "  # noqa: TC001 - retain runtime annotation introspection"
                        : "")
                    + "\n");
            return candidate;
          });
    }

    @Override
    public void importSymbol(Symbol symbol, String alias) {
      reference(symbol);
    }

    @Override
    public String toString() {
      return String.join("", lines.values());
    }
  }
}
