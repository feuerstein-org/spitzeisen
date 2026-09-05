package org.feuerstein.spitzeisen.codegen;

import static org.feuerstein.spitzeisen.codegen.PythonWriter.literal;
import static org.feuerstein.spitzeisen.codegen.PythonWriter.quote;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import software.amazon.smithy.codegen.core.Symbol;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.EventStreamIndex;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.knowledge.NullableIndex;
import software.amazon.smithy.model.knowledge.OperationIndex;
import software.amazon.smithy.model.knowledge.PaginatedIndex;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeType;
import software.amazon.smithy.model.shapes.StructureShape;
import software.amazon.smithy.model.traits.DefaultTrait;
import software.amazon.smithy.model.traits.DocumentationTrait;
import software.amazon.smithy.model.traits.ExternalDocumentationTrait;
import software.amazon.smithy.model.traits.HttpTrait;
import software.amazon.smithy.model.traits.JsonNameTrait;
import software.amazon.smithy.model.traits.RangeTrait;
import software.amazon.smithy.model.traits.RequiredTrait;
import software.amazon.smithy.model.traits.TimestampFormatTrait;

/** Resolves and emits a Python operation directly from Smithy shapes and indexes. */
final class PythonOperation {
  private static final Set<HttpBinding.Location> REQUEST_LOCATIONS =
      Set.of(
          HttpBinding.Location.LABEL,
          HttpBinding.Location.QUERY,
          HttpBinding.Location.QUERY_PARAMS,
          HttpBinding.Location.HEADER);
  private static final Set<String> LOCAL_NAMES =
      Set.of(
          "self",
          "params",
          "headers",
          "data",
          "result",
          "message",
          "records",
          "raw",
          "max_results",
          "on_validation_error",
          "sort",
          "order");
  final OperationShape shape;
  final String key;
  final String method;
  final String className;
  final String accessor;
  final Shape response;
  final Symbol responseSymbol;
  final boolean generatedModel;
  private final Model model;
  private final PythonSettings settings;
  private final PythonSymbols symbols;
  private final HttpBindingIndex bindings;
  private final HttpTrait http;
  private final boolean collection;
  private final boolean absent;
  private final String resultsKey;
  private final List<Parameter> parameters = new ArrayList<>();
  private final List<Parameter> sorting = new ArrayList<>();
  private final ObjectNode pagination;
  private final String pageName;
  private final String pageSizeName;
  private final int pageSizeMax;
  private final String sortSeparator;

  /**
   * Resolves the target's supported surface without creating an intermediate wire model.
   *
   * @param model assembled semantic model
   * @param settings validated Python target settings
   * @param symbols selected symbols
   * @param shape modeled shape
   */
  PythonOperation(
      Model model, PythonSettings settings, PythonSymbols symbols, OperationShape shape) {
    this.model = model;
    this.settings = settings;
    this.symbols = symbols;
    this.shape = shape;
    bindings = HttpBindingIndex.of(model);
    http = shape.getTrait(HttpTrait.class).orElseThrow(() -> unsupported("HTTP binding required"));
    if (!"GET".equals(http.getMethod())) {
      throw unsupported("Python runtime supports GET only");
    }
    if (EventStreamIndex.of(model).getInputInfo(shape).isPresent()
        || EventStreamIndex.of(model).getOutputInfo(shape).isPresent()) {
      throw unsupported("event streams are not supported");
    }
    if (PaginatedIndex.of(model).getPaginationInfo(settings.service(), shape).isPresent()) {
      throw unsupported("standard Smithy pagination is not supported yet");
    }
    var python = SdkPolicy.trait(shape, "spitzeisen.python#operation");
    String contextualName =
        shape.getId().getName(model.expectShape(settings.service(), ServiceShape.class));
    key =
        symbols.allocate(
            "modules",
            shape.getId().toString(),
            PythonSymbols.snake(python.getStringMemberOrDefault("module", contextualName)));
    method =
        symbols.allocate(
            "methods",
            shape.getId().toString(),
            PythonSymbols.snake(python.getStringMemberOrDefault("method", contextualName)));
    className =
        symbols.allocate("classes", shape.getId().toString(), PythonSymbols.pascal(key + "_api"));
    accessor =
        symbols.allocate(
            "accessors",
            shape.getId().toString(),
            PythonSymbols.snake(python.getStringMemberOrDefault("accessor", key + "_api")));
    var result = SdkPolicy.result(model, shape);
    collection = result.collection();
    var path = new ArrayList<>(result.path());
    var payload = bindings.getResponseBindings(shape, HttpBinding.Location.PAYLOAD);
    if (!payload.isEmpty()) {
      if (path.isEmpty() || !path.remove(0).equals(payload.get(0).getMember())) {
        throw unsupported("result path must start at the HTTP payload");
      }
    }
    if (path.size() > 1) {
      throw unsupported("nested response result paths are not supported");
    }
    resultsKey = path.isEmpty() ? "None" : quote(jsonName(path.get(0)));
    response =
        result.value().isListShape() || result.value().getType() == ShapeType.SET
            ? model.expectShape(result.value().members().iterator().next().getTarget())
            : result.value();
    generatedModel = !settings.externalModels().containsKey(response.getId().toString());
    if (generatedModel && !response.isStructureShape()) {
      throw unsupported(
          "response must be a structure or have a configured external model: " + response.getId());
    }
    responseSymbol = symbols.toSymbol(response);
    absent =
        "absent"
            .equals(SdkPolicy.of(shape, "notFound").getStringMemberOrDefault("behavior", "raise"));
    var input = OperationIndex.of(model).expectInputShape(shape);
    var request = bindings.getRequestBindings(shape);
    for (var binding : request.values()) {
      if (!REQUEST_LOCATIONS.contains(binding.getLocation())) {
        throw unsupported("unsupported request binding " + binding.getLocation());
      }
    }
    for (var binding : bindings.getResponseBindings(shape).values()) {
      if (binding.getLocation() != HttpBinding.Location.PAYLOAD
          && binding.getLocation() != HttpBinding.Location.DOCUMENT) {
        throw unsupported("unsupported response binding " + binding.getLocation());
      }
    }
    pagination = SdkPolicy.of(shape, "pageNumberPagination");
    var sort = SdkPolicy.of(shape, "sorting");
    var structural = new HashSet<String>();
    for (String field : List.of("pageMember", "pageSizeMember")) {
      pagination.getStringMember(field).ifPresent(value -> structural.add(value.getValue()));
    }
    for (String field : List.of("sortMember", "orderMember")) {
      sort.getStringMember(field).ifPresent(value -> structural.add(value.getValue()));
    }
    String scope = "parameters:" + shape.getId();
    symbols.reserve(scope, LOCAL_NAMES);
    for (MemberShape member : input.members()) {
      if (structural.contains(member.getMemberName())) {
        continue;
      }
      var binding = request.get(member.getMemberName());
      if (binding == null) {
        continue;
      }
      if (member.hasTrait("spitzeisen.api#excludeParameter")) {
        if (binding.getLocation() == HttpBinding.Location.LABEL
            || member.hasTrait(RequiredTrait.class)
            || !NullableIndex.of(model).isMemberNullable(member)) {
          throw unsupported("cannot exclude a required input: " + member.getId());
        }
        continue;
      }
      String preferred =
          SdkPolicy.trait(member, "spitzeisen.python#parameter")
              .getStringMemberOrDefault("name", member.getMemberName());
      parameters.add(
          new Parameter(
              symbols.allocate(scope, member.getId().toString(), PythonSymbols.snake(preferred)),
              member,
              binding,
              defaultValue(member),
              null));
    }
    if (!pagination.isEmpty() && !collection) {
      throw unsupported("page-number pagination requires a collection result");
    }
    pageName =
        pagination.isEmpty()
            ? null
            : query(input, request, pagination.expectStringMember("pageMember").getValue())
                .getLocationName();
    if (pagination.containsMember("pageSizeMember")) {
      var binding =
          query(input, request, pagination.expectStringMember("pageSizeMember").getValue());
      pageSizeName = binding.getLocationName();
      pageSizeMax =
          binding
              .getMember()
              .getMemberTrait(model, RangeTrait.class)
              .flatMap(RangeTrait::getMax)
              .orElseThrow(() -> unsupported("page-size member requires @range(max: ...)"))
              .intValueExact();
      if (pageSizeMax < 1) {
        throw unsupported("page-size maximum must be positive");
      }
    } else {
      pageSizeName = null;
      pageSizeMax = 0;
    }
    sortSeparator =
        "suffix".equals(sort.getStringMemberOrDefault("encoding", ""))
            ? sort.getStringMemberOrDefault("separator", ".")
            : null;
    if (!sort.isEmpty()) {
      if (!collection) {
        throw unsupported("sorting requires a collection result");
      }
      var binding = query(input, request, sort.expectStringMember("sortMember").getValue());
      var member = binding.getMember();
      if (sortSeparator != null) {
        if (sort.containsMember("orderMember")) {
          throw unsupported("suffix sorting cannot bind a separate order member");
        }
        var values =
            model
                .expectShape(member.getTarget())
                .asEnumShape()
                .orElseThrow(() -> unsupported("suffix sorting requires an enum"))
                .getEnumValues()
                .values();
        var pairs = values.stream().map(this::splitSort).toList();
        var defaultNode = defaultValue(member);
        if (defaultNode == null || !defaultNode.isStringNode()) {
          throw unsupported("suffix sorting requires a string default");
        }
        var defaults = splitSort(defaultNode.expectStringNode().getValue());
        sorting.add(
            new Parameter(
                "sort",
                member,
                binding,
                Node.from(defaults[0]),
                pairs.stream().map(pair -> Node.from(pair[0])).distinct().toList()));
        sorting.add(
            new Parameter(
                "order",
                member,
                binding,
                Node.from(defaults[1]),
                pairs.stream().map(pair -> Node.from(pair[1])).distinct().toList()));
      } else {
        if (!"separate".equals(sort.expectStringMember("encoding").getValue())) {
          throw unsupported("unknown sorting encoding");
        }
        sorting.add(new Parameter("sort", member, binding, defaultValue(member), null));
        var order = query(input, request, sort.expectStringMember("orderMember").getValue());
        sorting.add(
            new Parameter(
                "order", order.getMember(), order, defaultValue(order.getMember()), null));
      }
    }
  }

  /**
   * Renders a native asynchronous or blocking operation implementation.
   *
   * @return resolved Python source or identifier
   * @param async selected async
   */
  String render(boolean async) {
    String prefix = async ? "Async" : "Sync";
    String aw = async ? "await " : "";
    var names = new HashSet<>(LOCAL_NAMES);
    names.addAll(PythonSymbols.BUILTINS);
    names.add(prefix + className + "Base");
    parameters.forEach(parameter -> names.add(parameter.name()));
    var writer = new PythonWriter(names);
    String modelName = writer.reference(responseSymbol);
    String base = writer.reference("spitzeisen", prefix + "SpitzeisenApi");
    var arguments = new ArrayList<Argument>();
    parameters.forEach(parameter -> arguments.add(argument(parameter, writer)));
    sorting.forEach(parameter -> arguments.add(argument(parameter, writer)));
    String cost =
        SdkPolicy.of(shape, "rateLimitCost").getNumberMemberOrDefault("units", 1).toString();
    String paginationType =
        writer.reference("spitzeisen", pageName == null ? "NoPagination" : "PageNumber");
    String pageArgs =
        pageName == null
            ? ""
            : "page_param="
                + quote(pageName)
                + ", start="
                + pagination.getNumberMemberOrDefault("start", 1)
                + ", step="
                + pagination.getNumberMemberOrDefault("step", 1)
                + ", ";
    String uri = http.getUri().toString().replace("+}", "}");
    writer.write(
        "_OPERATION = $T(path=$L, cost=$L, pagination=$L($Lresults_key=$L))\n",
        PythonSymbols.symbol("spitzeisen", "SpitzeisenOperationSpec"),
        quote(uri),
        cost,
        paginationType,
        pageArgs,
        resultsKey);
    writer.write("class $L($L):", prefix + className + "Base", base).indent();
    writer.docs(
        "Generated request layer. Customize the public " + prefix + className + " subclass.");
    String rawType =
        (collection ? "list[dict[str, " : "dict[str, ")
            + writer.reference("typing", "Any")
            + (collection ? "]]" : "]")
            + (absent ? " | None" : "");
    signature(writer, arguments, async, true, rawType);
    writer.docs(
        "Fetch raw JSON without model validation. See " + method + " for parameter documentation.");
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
    if (collection) {
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
      if (!sorting.contains(argument.parameter()) && isQuery(argument.parameter().binding())) {
        queryValue(writer, argument, argument.coercion());
      }
    }
    if (sortSeparator != null) {
      var field = arguments.get(arguments.size() - 2);
      var order = arguments.get(arguments.size() - 1);
      queryValue(
          writer,
          field,
          writer.reference("spitzeisen", "coerce_choice")
              + "("
              + writer.reference("spitzeisen", "coerce_sort")
              + "("
              + field.coercion()
              + ", "
              + order.coercion()
              + ", separator="
              + quote(sortSeparator)
              + "), "
              + symbols.type(model.expectShape(field.parameter().member().getTarget()), writer)
              + ", \"sort\")");
    } else {
      for (Argument argument : arguments) {
        if (sorting.contains(argument.parameter())) {
          queryValue(writer, argument, argument.coercion());
        }
      }
    }
    if (pageSizeName != null) {
      writer.write(
          "*$L(min(max_results, $L) if max_results is not None else $L, name=$L),",
          writer.reference("spitzeisen", "serialize_query_param"),
          pageSizeMax,
          pageSizeMax,
          quote(pageSizeName));
    }
    writer.dedent().write("]");
    writer.write("headers = $L({", writer.reference("spitzeisen", "build_header_params")).indent();
    for (Argument argument : arguments) {
      if (argument.parameter().binding().getLocation() == HttpBinding.Location.HEADER) {
        writer.write(
            "$L: $L,",
            quote(argument.parameter().binding().getLocationName()),
            required(argument, writer));
      }
    }
    writer.dedent().write("})");
    writer
        .write(
            "$L$Lself.$L$L(_OPERATION, params=params, headers=headers,",
            collection ? "return " : "data = ",
            aw,
            collection ? "_get_all_pages" : "_request",
            absent ? "_optional" : "")
        .indent();
    if (collection) {
      writer.write("max_results=max_results,");
    }
    for (Argument argument : arguments) {
      var binding = argument.parameter().binding();
      if (binding.getLocation() == HttpBinding.Location.LABEL) {
        String label = binding.getLocationName();
        if (Set.of("params", "headers", "max_results", "spec", "self").contains(label)) {
          throw unsupported("path label conflicts with a runtime request argument: " + label);
        }
        boolean greedy = http.getUri().getLabel(label).orElseThrow().isGreedyLabel();
        String expression =
            writer.reference("spitzeisen", "serialize_path_param")
                + "("
                + required(argument, writer)
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
    if (!collection) {
      if (absent) {
        writer.write("if data is None:").indent().write("return None").dedent();
      }
      writer.write("if not isinstance(data, dict):").indent();
      writer.write("message = $L", quote("expected a response object"));
      writer.write("raise $T(message)", PythonSymbols.symbol("spitzeisen", "ResponseShapeError"));
      writer.dedent();
      if (!"None".equals(resultsKey)) {
        writer.write("result = data.get($L)", resultsKey);
        writer
            .write("if result is None:")
            .indent()
            .write("return $L", absent ? "None" : "{}")
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
        (collection ? "list[" + modelName + "]" : modelName) + (absent ? " | None" : ""));
    writer.docs(documentation(arguments, async));
    writer.write("raw = $Lself.$L_raw(", aw, method).indent();
    for (Argument argument : arguments) {
      writer.write("$L=$L,", argument.parameter().name(), argument.parameter().name());
    }
    if (collection) {
      writer.write("max_results=max_results,");
    }
    writer.dedent().write(")");
    if (absent) {
      writer.write("if raw is None:").indent().write("return None").dedent();
    }
    writer.write(
        collection
            ? "return self._validate_records(raw, $L, self._resolve_validation_mode(on_validation_error))"
            : "return $L.model_validate(raw)",
        modelName);
    writer.dedent().dedent();
    return writer.toString();
  }

  private Argument argument(Parameter parameter, PythonWriter writer) {
    var member = parameter.member();
    var target = model.expectShape(member.getTarget());
    var adapterPolicy = SdkPolicy.of(member, "inputAdapter");
    String type;
    String coercion = parameter.name();
    if (!adapterPolicy.isEmpty() && parameter.literals() == null) {
      String id = adapterPolicy.expectStringMember("id").getValue();
      var adapterNode = settings.inputAdapters().get(id);
      if (adapterNode == null) {
        throw unsupported("input adapter is not registered: " + id);
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
              ? symbols.type(target, writer)
              : PythonSymbols.literals(parameter.literals(), writer);
      Shape value = target;
      MemberShape subject = member;
      boolean multiple = target.isListShape() || target.getType() == ShapeType.SET;
      if (multiple) {
        subject = target.members().iterator().next();
        value = model.expectShape(subject.getTarget());
      }
      if (parameter.literals() != null || value.isEnumShape() || value.isIntEnumShape()) {
        String enumType = multiple ? symbols.type(value, writer) : type;
        coercion =
            writer.reference("spitzeisen", multiple ? "coerce_choices" : "coerce_choice")
                + "("
                + parameter.name()
                + ", "
                + enumType
                + ", "
                + quote(parameter.name())
                + ")";
      } else if (value.isTimestampShape()) {
        var defaultFormat =
            parameter.binding().getLocation() == HttpBinding.Location.HEADER
                ? TimestampFormatTrait.Format.HTTP_DATE
                : TimestampFormatTrait.Format.DATE_TIME;
        String format =
            bindings
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
    if (parameter.defaultValue() != null && parameter.defaultValue().isNullNode()) {
      type += " | None";
    }
    var encoding = SdkPolicy.of(member, "queryEncoding");
    if (!encoding.isEmpty() && !isQuery(parameter.binding())) {
      throw unsupported("query encoding requires a query binding: " + member.getId());
    }
    if ("deepObject".equals(encoding.getStringMemberOrDefault("style", "form"))
        || encoding.getBooleanMemberOrDefault("allowReserved", false)) {
      throw unsupported("deepObject and allowReserved query encoding are not supported");
    }
    return new Argument(parameter, type, coercion, encoding);
  }

  private void signature(
      PythonWriter writer, List<Argument> arguments, boolean async, boolean raw, String returns) {
    writer.write("$Ldef $L$L(self,", async ? "async " : "", method, raw ? "_raw" : "").indent();
    if (collection || !arguments.isEmpty()) {
      writer.write("*,");
    }
    for (Argument argument : arguments) {
      Node value = argument.parameter().defaultValue();
      String defaultSource = value == null ? "" : literal(value);
      if (value != null
          && value.isNumberNode()
          && model.expectShape(argument.parameter().member().getTarget()).isBigDecimalShape()) {
        defaultSource = writer.reference("decimal", "Decimal") + "(" + quote(defaultSource) + ")";
      }
      writer.write(
          "$L: $L$L,",
          argument.parameter().name(),
          argument.type(),
          value == null ? "" : " = " + defaultSource);
    }
    if (collection) {
      writer.write("max_results: int | None = None,");
      if (!raw) {
        writer.write(
            "on_validation_error: $T | None = None,",
            PythonSymbols.symbol("spitzeisen", "ValidationMode"));
      }
    }
    writer.dedent().write(") -> $L:", returns).indent();
  }

  private void queryValue(PythonWriter writer, Argument argument, String value) {
    writer.write(
        "*$L($L, name=$L, style=$L, explode=$L, required=$L, param_name=$L),",
        writer.reference("spitzeisen", "serialize_query_param"),
        value,
        quote(argument.parameter().binding().getLocationName()),
        quote(argument.encoding().getStringMemberOrDefault("style", "form")),
        argument.encoding().getBooleanMemberOrDefault("explode", true) ? "True" : "False",
        argument.parameter().defaultValue() == null
                || argument.parameter().member().hasTrait(RequiredTrait.class)
            ? "True"
            : "False",
        quote(argument.parameter().name()));
  }

  private String required(Argument argument, PythonWriter writer) {
    return argument.parameter().defaultValue() != null
            && !argument.parameter().member().hasTrait(RequiredTrait.class)
            && argument.parameter().binding().getLocation() != HttpBinding.Location.LABEL
        ? argument.coercion()
        : writer.reference("spitzeisen", "require_value")
            + "("
            + argument.coercion()
            + ", "
            + quote(argument.parameter().name())
            + ")";
  }

  private String documentation(List<Argument> arguments, boolean async) {
    String docs =
        shape
            .getTrait(DocumentationTrait.class)
            .map(DocumentationTrait::getValue)
            .orElse("Call " + method + ".");
    String url =
        shape
            .getTrait(ExternalDocumentationTrait.class)
            .map(value -> value.getUrls().values().stream().sorted().findFirst().orElse(""))
            .orElse("");
    String example =
        arguments.stream()
            .filter(argument -> argument.parameter().defaultValue() == null)
            .map(argument -> argument.parameter().name() + "=...")
            .collect(Collectors.joining(", "));
    var text = new StringBuilder(docs).append("\n\n");
    text.append(
        collection
            ? "Fetch every page and validate the returned records."
            : "Fetch and validate the response.");
    if (!url.isEmpty()) {
      text.append("\n\nDocs: ").append(url);
    }
    text.append("\n\nExample::\n\n    ")
        .append(async ? "async with Async" : "with Sync")
        .append(settings.clientName())
        .append("(config) as api:\n        result = ")
        .append(async ? "await " : "")
        .append("api.")
        .append(accessor)
        .append('.')
        .append(method)
        .append('(')
        .append(example)
        .append(")\n");
    if (!arguments.isEmpty() || collection) {
      text.append("\nArgs:\n");
      for (Argument argument : arguments) {
        text.append("    ")
            .append(argument.parameter().name())
            .append(": ")
            .append(
                argument
                    .parameter()
                    .member()
                    .getTrait(DocumentationTrait.class)
                    .map(DocumentationTrait::getValue)
                    .orElse("Value for " + argument.parameter().binding().getLocationName() + "."))
            .append('\n');
      }
      if (collection) {
        text.append("    max_results: Maximum total records (None means no cap).\n")
            .append(
                "    on_validation_error: Override the configured raise/skip validation policy.\n");
      }
    }
    text.append("\nReturns:\n    Validated ")
        .append(collection ? "records" : "response")
        .append(absent ? ", or None for HTTP 404." : ".");
    return text.append(
            "\n\nRaises:\n    pydantic.ValidationError: Invalid response data.\n    ValueError: Invalid input.\n")
        .toString();
  }

  private Node defaultValue(MemberShape member) {
    var configured = SdkPolicy.of(member, "clientDefault");
    if (!configured.isEmpty()) {
      return configured.expectMember("value");
    }
    return member
        .getMemberTrait(model, DefaultTrait.class)
        .map(DefaultTrait::toNode)
        .orElseGet(
            () ->
                !member.hasTrait(RequiredTrait.class)
                        && NullableIndex.of(model).isMemberNullable(member)
                    ? Node.nullNode()
                    : null);
  }

  private HttpBinding query(StructureShape input, Map<String, HttpBinding> request, String name) {
    input
        .getMember(name)
        .orElseThrow(() -> unsupported("portable policy references missing input member " + name));
    var binding = request.get(name);
    if (binding == null || binding.getLocation() != HttpBinding.Location.QUERY) {
      throw unsupported("sorting/pagination member must have a query binding: " + name);
    }
    return binding;
  }

  private String[] splitSort(String value) {
    int index = value.lastIndexOf(sortSeparator);
    if (sortSeparator.isEmpty() || index < 1 || index + sortSeparator.length() == value.length()) {
      throw unsupported("invalid suffix sorting value " + value);
    }
    return new String[] {
      value.substring(0, index), value.substring(index + sortSeparator.length())
    };
  }

  private IllegalArgumentException unsupported(String message) {
    return new IllegalArgumentException(message + " on " + shape.getId());
  }

  private static boolean isQuery(HttpBinding binding) {
    return binding.getLocation() == HttpBinding.Location.QUERY
        || binding.getLocation() == HttpBinding.Location.QUERY_PARAMS;
  }

  /**
   * Returns the JSON property name selected by the protocol's jsonName trait.
   *
   * @return resolved Python source or identifier
   * @param member selected member
   */
  static String jsonName(MemberShape member) {
    return member
        .getTrait(JsonNameTrait.class)
        .map(JsonNameTrait::getValue)
        .orElse(member.getMemberName());
  }

  private record Parameter(
      String name,
      MemberShape member,
      HttpBinding binding,
      Node defaultValue,
      List<? extends Node> literals) {}

  private record Argument(Parameter parameter, String type, String coercion, ObjectNode encoding) {}
}
