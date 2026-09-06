package org.feuerstein.spitzeisen.codegen;

import static org.feuerstein.spitzeisen.codegen.PythonWriter.quote;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.feuerstein.spitzeisen.codegen.PythonParameters.Parameter;
import software.amazon.smithy.codegen.core.Symbol;
import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.EventStreamIndex;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
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
import software.amazon.smithy.model.traits.HttpTrait;
import software.amazon.smithy.model.traits.JsonNameTrait;
import software.amazon.smithy.model.traits.RangeTrait;

/** Resolves one operation against Smithy indexes and the supported target policies. */
final class PythonOperation {
  private static final Set<HttpBinding.Location> REQUEST_LOCATIONS =
      Set.of(
          HttpBinding.Location.LABEL,
          HttpBinding.Location.QUERY,
          HttpBinding.Location.QUERY_PARAMS,
          HttpBinding.Location.HEADER);
  static final Set<String> LOCAL_NAMES =
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
  final HttpTrait http;
  final boolean collection;
  final boolean absent;
  final String resultsKey;
  final List<Parameter> parameters = new ArrayList<>();
  final List<Parameter> sorting = new ArrayList<>();
  final ObjectNode pagination;
  final String pageName;
  final String pageSizeName;
  final int pageSizeMax;
  final String sortSeparator;

  /**
   * Resolves the target's supported surface without creating an intermediate wire model.
   *
   * @param model assembled semantic model
   * @param settings validated Python target settings
   * @param symbols selected symbols
   * @param names deterministic Python naming scopes
   * @param shape modeled shape
   */
  PythonOperation(
      Model model,
      PythonSettings settings,
      SymbolProvider symbols,
      PythonNames names,
      OperationShape shape) {
    this.model = model;
    this.settings = settings;
    this.shape = shape;
    AlloyProtocol.validate(model, shape);
    var bindings = HttpBindingIndex.of(model);
    http = shape.getTrait(HttpTrait.class).orElseThrow(() -> unsupported("HTTP binding required"));
    if (!"GET".equals(http.getMethod())) {
      throw unsupported("generated Alloy operations support GET only");
    }
    if (http.getCode() == 204 || http.getCode() == 205) {
      throw unsupported("empty response bodies are not supported by generated JSON methods");
    }
    if (!http.getUri().getQueryLiterals().isEmpty()) {
      throw unsupported(
          "URI query literals are not supported; use a query member with a client default");
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
        names.allocate(
            "modules",
            shape.getId().toString(),
            PythonSymbols.snake(python.getStringMemberOrDefault("module", contextualName)));
    method =
        names.allocate(
            "methods",
            shape.getId().toString(),
            PythonSymbols.snake(python.getStringMemberOrDefault("method", contextualName)));
    className =
        names.allocate("classes", shape.getId().toString(), PythonSymbols.pascal(key + "_api"));
    accessor =
        names.allocate(
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
    if (!response.isStructureShape() && !response.isDocumentShape()) {
      throw unsupported("response records must be JSON objects modeled as structures or Documents");
    }
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
      if (binding.getLocation() == HttpBinding.Location.HEADER
          && model.expectShape(binding.getMember().getTarget()).isListShape()) {
        throw unsupported(
            "header collections are not supported by the Alloy client subset; use a String and adapter");
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
    names.reserve(scope, LOCAL_NAMES);
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
            || !MemberPresence.inputNullable(model, member, settings)) {
          throw unsupported("cannot exclude a required input: " + member.getId());
        }
        continue;
      }
      String preferred =
          SdkPolicy.trait(member, "spitzeisen.python#parameter")
              .getStringMemberOrDefault("name", member.getMemberName());
      parameters.add(
          new Parameter(
              names.allocate(scope, member.getId().toString(), PythonSymbols.snake(preferred)),
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

  private Node defaultValue(MemberShape member) {
    return MemberPresence.inputDefault(model, member, settings);
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
}
