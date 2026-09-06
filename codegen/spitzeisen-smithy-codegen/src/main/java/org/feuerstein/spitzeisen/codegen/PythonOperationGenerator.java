package org.feuerstein.spitzeisen.codegen;

import static org.feuerstein.spitzeisen.codegen.PythonWriter.literal;
import static org.feuerstein.spitzeisen.codegen.PythonWriter.quote;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.feuerstein.spitzeisen.codegen.PythonParameters.Argument;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.node.Node;

/** Writes asynchronous and blocking methods for one resolved operation. */
record PythonOperationGenerator(GenerationContext context, PythonOperation operation) {
  /** Emits the generated operation and its public extension for both Python surfaces. */
  void generate() {
    var writers = context.writerDelegator();
    for (boolean async : List.of(true, false)) {
      String folder = async ? "_async" : "_sync";
      String prefix = async ? "Async" : "Sync";
      writers.useSdkWriter(
          folder + "/_generated/" + operation.key + ".py", false, writer -> render(writer, async));
      writers.extension(
          folder + "/" + operation.key + ".py",
          context.settings().packageName() + "." + folder + "._generated." + operation.key,
          prefix + operation.className + "Base",
          prefix + operation.className);
    }
  }

  private void render(PythonWriter writer, boolean async) {
    String prefix = async ? "Async" : "Sync";
    String aw = async ? "await " : "";
    var names = new HashSet<>(PythonOperation.LOCAL_NAMES);
    names.addAll(PythonSymbols.BUILTINS);
    names.add(prefix + operation.className + "Base");
    operation.parameters.forEach(parameter -> names.add(parameter.name()));
    writer.reserve(names);
    var parameterGenerator =
        new PythonParameters(
            context.model(), context.settings(), context.symbolProvider(), operation.shape);
    String modelName = writer.reference(operation.responseSymbol);
    String base = writer.reference("spitzeisen", prefix + "SpitzeisenApi");
    var arguments = new ArrayList<Argument>();
    operation.parameters.forEach(
        parameter -> arguments.add(parameterGenerator.argument(parameter, writer)));
    operation.sorting.forEach(
        parameter -> arguments.add(parameterGenerator.argument(parameter, writer)));
    String cost =
        SdkPolicy.of(operation.shape, "rateLimitCost")
            .getNumberMemberOrDefault("units", 1)
            .toString();
    String paginationType =
        writer.reference("spitzeisen", operation.pageName == null ? "NoPagination" : "PageNumber");
    String pageArgs =
        operation.pageName == null
            ? ""
            : "page_param="
                + quote(operation.pageName)
                + ", start="
                + operation.pagination.getNumberMemberOrDefault("start", 1)
                + ", step="
                + operation.pagination.getNumberMemberOrDefault("step", 1)
                + ", ";
    String uri = operation.http.getUri().toString().replace("+}", "}");
    writer.write(
        "_OPERATION = $T(path=$L, cost=$L, pagination=$L($Lresults_key=$L))\n",
        PythonSymbols.symbol("spitzeisen", "SpitzeisenOperationSpec"),
        quote(uri),
        cost,
        paginationType,
        pageArgs,
        operation.resultsKey);
    writer.write("class $L($L):", prefix + operation.className + "Base", base).indent();
    writer.docs(
        "Generated request layer. Customize the public "
            + prefix
            + operation.className
            + " subclass.");
    String rawType =
        (operation.collection ? "list[dict[str, " : "dict[str, ")
            + writer.reference("typing", "Any")
            + (operation.collection ? "]]" : "]")
            + (operation.absent ? " | None" : "");
    signature(writer, arguments, async, true, rawType);
    writer.docs(
        "Fetch raw JSON without model validation. See "
            + operation.method
            + " for parameter documentation.");
    for (Argument argument : arguments) {
      Node defaultValue = argument.parameter().defaultValue();
      if (defaultValue != null && (defaultValue.isArrayNode() || defaultValue.isObjectNode())) {
        writer.write(
            "$L = $T($L)",
            argument.parameter().name(),
            PythonSymbols.symbol("copy", "deepcopy"),
            argument.parameter().name());
      }
      writer.write(
          "$L = self._validate_input($L, $L)",
          argument.parameter().name(),
          argument.parameter().name(),
          argument.type());
    }
    if (operation.collection) {
      writer.write("max_results = self._validate_input(max_results, int | None)");
    }
    writer
        .write(
            "params: $T = [",
            PythonSymbols.symbol("spitzeisen.params", "QueryParams").toBuilder()
                .putProperty("runtimeAnnotation", true)
                .build())
        .indent();
    for (Argument argument : arguments) {
      if (!operation.sorting.contains(argument.parameter())
          && PythonParameters.isQuery(argument.parameter().binding())) {
        parameterGenerator.queryValue(writer, argument, argument.coercion());
      }
    }
    if (operation.sortSeparator != null) {
      var field = arguments.get(arguments.size() - 2);
      var order = arguments.get(arguments.size() - 1);
      parameterGenerator.queryValue(
          writer,
          field,
          "None if "
              + parameterGenerator.presenceValue(field.parameter(), writer)
              + " is None and "
              + parameterGenerator.presenceValue(order.parameter(), writer)
              + " is None else "
              + writer.reference("spitzeisen", "coerce_sort")
              + "("
              + field.coercion()
              + ", "
              + order.coercion()
              + ", separator="
              + quote(operation.sortSeparator)
              + ")");
    } else {
      for (Argument argument : arguments) {
        if (operation.sorting.contains(argument.parameter())) {
          parameterGenerator.queryValue(writer, argument, argument.coercion());
        }
      }
    }
    if (operation.pageSizeName != null) {
      writer.write(
          "*$L(min(max_results, $L) if max_results is not None else $L, name=$L),",
          writer.reference("spitzeisen", "serialize_query_param"),
          operation.pageSizeMax,
          operation.pageSizeMax,
          quote(operation.pageSizeName));
    }
    writer.dedent().write("]");
    writer.write("headers = $L({", writer.reference("spitzeisen", "build_header_params")).indent();
    for (Argument argument : arguments) {
      if (argument.parameter().binding().getLocation() == HttpBinding.Location.HEADER) {
        writer.write(
            "$L: $L,",
            quote(argument.parameter().binding().getLocationName()),
            parameterGenerator.required(argument, writer));
      }
    }
    writer.dedent().write("})");
    writer
        .write(
            "$L$Lself.$L$L(_OPERATION, params=params, headers=headers,",
            operation.collection ? "return " : "data = ",
            aw,
            operation.collection ? "_get_all_pages" : "_request",
            operation.absent ? "_optional" : "")
        .indent();
    if (operation.collection) {
      writer.write("max_results=max_results,");
    }
    for (Argument argument : arguments) {
      var binding = argument.parameter().binding();
      if (binding.getLocation() == HttpBinding.Location.LABEL) {
        String label = binding.getLocationName();
        if (Set.of("params", "headers", "max_results", "spec", "self").contains(label)) {
          throw new IllegalArgumentException(
              "path label conflicts with a runtime request argument: "
                  + label
                  + " on "
                  + operation.shape.getId());
        }
        boolean greedy = operation.http.getUri().getLabel(label).orElseThrow().isGreedyLabel();
        String expression =
            writer.reference("spitzeisen", "serialize_path_param")
                + "("
                + parameterGenerator.required(argument, writer)
                + ", greedy="
                + (greedy ? "True" : "False")
                + ")";
        writer.write(
            "$L",
            PythonSymbols.RESERVED.contains(label)
                ? "**{" + quote(label) + ": " + expression + "},"
                : label + "=" + expression + ",");
      }
    }
    writer.dedent().write(")");
    if (!operation.collection) {
      if (operation.absent) {
        writer.write("if data is None:").indent().write("return None").dedent();
      }
      writer.write("if not isinstance(data, dict):").indent();
      writer.write("message = $L", quote("expected a response object"));
      writer.write("raise $T(message)", PythonSymbols.symbol("spitzeisen", "ResponseShapeError"));
      writer.dedent();
      if (!"None".equals(operation.resultsKey)) {
        writer.write("result = data.get($L)", operation.resultsKey);
        writer
            .write("if result is None:")
            .indent()
            .write("return $L", operation.absent ? "None" : "{}")
            .dedent();
        writer.write("if not isinstance(result, dict):").indent();
        writer.write("message = $L", quote("expected a response result object"));
        writer.write("raise $T(message)", PythonSymbols.symbol("spitzeisen", "ResponseShapeError"));
        writer.dedent().write("return result");
      } else {
        writer.write("return data");
      }
    }
    writer.dedent().write("");
    signature(
        writer,
        arguments,
        async,
        false,
        (operation.collection ? "list[" + modelName + "]" : modelName)
            + (operation.absent ? " | None" : ""));
    writer.docs(
        new PythonOperationDocumentation(context.settings(), operation).render(arguments, async));
    writer.write("raw = $Lself.$L_raw(", aw, operation.method).indent();
    for (Argument argument : arguments) {
      writer.write("$L=$L,", argument.parameter().name(), argument.parameter().name());
    }
    if (operation.collection) {
      writer.write("max_results=max_results,");
    }
    writer.dedent().write(")");
    if (operation.absent) {
      writer.write("if raw is None:").indent().write("return None").dedent();
    }
    writer.write(
        operation.collection
            ? "return self._validate_records(raw, $L, self._resolve_validation_mode(on_validation_error))"
            : "return self._validate_response(raw, $L)",
        modelName);
    writer.dedent().dedent();
  }

  private void signature(
      PythonWriter writer, List<Argument> arguments, boolean async, boolean raw, String returns) {
    writer
        .write("$Ldef $L$L(self,", async ? "async " : "", operation.method, raw ? "_raw" : "")
        .indent();
    if (operation.collection || !arguments.isEmpty()) {
      writer.write("*,");
    }
    for (Argument argument : arguments) {
      Node value = argument.parameter().defaultValue();
      String defaultSource = value == null ? "" : literal(value);
      if (value != null
          && value.isNumberNode()
          && context
              .model()
              .expectShape(argument.parameter().member().getTarget())
              .isBigDecimalShape()) {
        defaultSource = writer.reference("decimal", "Decimal") + "(" + quote(defaultSource) + ")";
      }
      writer.write(
          "$L: $L$L,",
          argument.parameter().name(),
          argument.type(),
          value == null ? "" : " = " + defaultSource);
    }
    if (operation.collection) {
      writer.write("max_results: int | None = None,");
      if (!raw) {
        writer.write(
            "on_validation_error: $T | None = None,",
            PythonSymbols.symbol("spitzeisen", "ValidationMode"));
      }
    }
    writer.dedent().write(") -> $L:", returns).indent();
  }
}
