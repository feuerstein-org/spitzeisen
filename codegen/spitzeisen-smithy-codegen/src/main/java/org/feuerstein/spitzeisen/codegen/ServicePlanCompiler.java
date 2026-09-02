package org.feuerstein.spitzeisen.codegen;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import software.amazon.smithy.codegen.core.TopologicalIndex;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.EventStreamIndex;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.knowledge.NullableIndex;
import software.amazon.smithy.model.knowledge.OperationIndex;
import software.amazon.smithy.model.knowledge.PaginatedIndex;
import software.amazon.smithy.model.knowledge.PaginationInfo;
import software.amazon.smithy.model.knowledge.ServiceIndex;
import software.amazon.smithy.model.knowledge.TopDownIndex;
import software.amazon.smithy.model.loader.Prelude;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.ArrayNode;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.EnumShape;
import software.amazon.smithy.model.shapes.IntEnumShape;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeId;
import software.amazon.smithy.model.shapes.ShapeType;
import software.amazon.smithy.model.shapes.StructureShape;
import software.amazon.smithy.model.traits.DefaultTrait;
import software.amazon.smithy.model.traits.DocumentationTrait;
import software.amazon.smithy.model.traits.EnumValueTrait;
import software.amazon.smithy.model.traits.ExternalDocumentationTrait;
import software.amazon.smithy.model.traits.HttpTrait;
import software.amazon.smithy.model.traits.LengthTrait;
import software.amazon.smithy.model.traits.PatternTrait;
import software.amazon.smithy.model.traits.RangeTrait;
import software.amazon.smithy.model.traits.RequiredTrait;
import software.amazon.smithy.model.traits.SparseTrait;
import software.amazon.smithy.model.traits.TitleTrait;
import software.amazon.smithy.model.traits.UniqueItemsTrait;

/** Compiles a prepared Smithy model into the runtime-neutral Spitzeisen service plan. */
final class ServicePlanCompiler {
  private static final String PORTABLE_NAMESPACE = "spitzeisen.api";
  private static final String PROTOCOL_NAMESPACE = "spitzeisen.protocols";
  private static final String TARGET_NAMESPACE_PREFIX = "spitzeisen.";

  private static final ShapeId RESULT = ShapeId.from("spitzeisen.api#result");
  private static final ShapeId NOT_FOUND = ShapeId.from("spitzeisen.api#notFound");
  private static final ShapeId RATE_LIMIT_COST = ShapeId.from("spitzeisen.api#rateLimitCost");
  private static final ShapeId PAGE_NUMBER_PAGINATION =
      ShapeId.from("spitzeisen.api#pageNumberPagination");
  private static final ShapeId SORTING = ShapeId.from("spitzeisen.api#sorting");
  private static final ShapeId QUERY_ENCODING = ShapeId.from("spitzeisen.api#queryEncoding");
  private static final ShapeId CLIENT_DEFAULT = ShapeId.from("spitzeisen.api#clientDefault");
  private static final ShapeId EXCLUDE_OPERATION = ShapeId.from("spitzeisen.api#excludeOperation");
  private static final ShapeId EXCLUDE_PARAMETER = ShapeId.from("spitzeisen.api#excludeParameter");
  private static final ShapeId INPUT_ADAPTER = ShapeId.from("spitzeisen.api#inputAdapter");

  private static final Set<ShapeId> CONSTRAINT_TRAITS =
      Set.of(
          ShapeId.from("smithy.api#enum"),
          LengthTrait.ID,
          PatternTrait.ID,
          RangeTrait.ID,
          RequiredTrait.ID,
          SparseTrait.ID,
          UniqueItemsTrait.ID);

  private final Model model;
  private final ServiceShape service;
  private final EventStreamIndex eventStreams;
  private final HttpBindingIndex httpBindings;
  private final NullableIndex nullableIndex;
  private final OperationIndex operationIndex;
  private final PaginatedIndex paginatedIndex;
  private final ServiceIndex serviceIndex;
  private final TopDownIndex topDownIndex;
  private final Set<ShapeId> recursiveShapes;

  /**
   * Creates a compiler for one prepared Smithy service.
   *
   * @param model prepared semantic model
   * @param serviceId selected service
   */
  ServicePlanCompiler(Model model, ShapeId serviceId) {
    this.model = model;
    this.service = model.expectShape(serviceId, ServiceShape.class);
    this.eventStreams = EventStreamIndex.of(model);
    this.httpBindings = HttpBindingIndex.of(model);
    this.nullableIndex = NullableIndex.of(model);
    this.operationIndex = OperationIndex.of(model);
    this.paginatedIndex = PaginatedIndex.of(model);
    this.serviceIndex = ServiceIndex.of(model);
    this.topDownIndex = TopDownIndex.of(model);
    this.recursiveShapes =
        TopologicalIndex.of(model).getRecursiveShapes().stream()
            .map(Shape::getId)
            .collect(java.util.stream.Collectors.toUnmodifiableSet());
  }

  /**
   * Compiles the service closure.
   *
   * @return immutable service plan and neutral response result roots
   * @throws IllegalArgumentException if the selected service contains no operations
   */
  CompiledServicePlan compile() {
    var operations = topDownIndex.getContainedOperations(service).stream().sorted().toList();
    if (operations.isEmpty()) {
      throw new IllegalArgumentException("service " + service.getId() + " contains no operations");
    }

    var resultRoots = new LinkedHashSet<ShapeId>();
    var operationNodes = ObjectNode.builder();
    for (var operation : operations) {
      operationNodes.withMember(
          operation.getId().toString(), compileOperation(operation, resultRoots));
    }

    var shapeNodes = ObjectNode.builder();
    serviceClosure().stream()
        .filter(ServicePlanCompiler::isDataShape)
        .sorted()
        .forEach(shape -> shapeNodes.withMember(shape.getId().toString(), compileShape(shape)));

    var document =
        ObjectNode.builder()
            .withMember("service", compileService(operations))
            .withMember("operations", operationNodes.build())
            .withMember("shapes", shapeNodes.build())
            .withMember("extensions", Node.objectNode())
            .build();
    return new CompiledServicePlan(document, Set.copyOf(resultRoots));
  }

  private ObjectNode compileService(List<OperationShape> operations) {
    var builder =
        ObjectNode.builder()
            .withMember("id", service.getId().toString())
            .withMember("version", service.getVersion())
            .withMember(
                "protocols",
                strings(
                    serviceIndex.getProtocols(service).keySet().stream()
                        .map(ShapeId::toString)
                        .sorted()
                        .toList()))
            .withMember("auth_schemes", authSchemes(service))
            .withMember(
                "operations",
                strings(operations.stream().map(Shape::getId).map(Object::toString).toList()))
            .withMember("external_documentation", externalDocumentation(service))
            .withMember("traits", traits(service))
            .withMember("policies", policies(service))
            .withMember("extensions", extensions(service));
    documentation(service).ifPresent(value -> builder.withMember("documentation", value));
    service
        .getTrait(TitleTrait.class)
        .ifPresent(value -> builder.withMember("title", value.getValue()));
    return builder.build();
  }

  private ObjectNode compileOperation(OperationShape operation, Set<ShapeId> resultRoots) {
    var errors = operationIndex.getErrors(service, operation);
    var builder =
        ObjectNode.builder()
            .withMember("id", operation.getId().toString())
            .withMember("input", operationIndex.expectInputShape(operation).getId().toString())
            .withMember("output", operationIndex.expectOutputShape(operation).getId().toString())
            .withMember(
                "errors", strings(errors.stream().map(Shape::getId).map(Object::toString).toList()))
            .withMember("auth_schemes", authSchemes(operation))
            .withMember("external_documentation", externalDocumentation(operation))
            .withMember("traits", traits(operation))
            .withMember("policies", operationPolicies(operation))
            .withMember("extensions", extensions(operation));
    documentation(operation).ifPresent(value -> builder.withMember("documentation", value));
    operation
        .getTrait(HttpTrait.class)
        .ifPresent(value -> builder.withMember("http", compileHttp(operation, value, errors)));
    compileEventStreams(operation).ifPresent(value -> builder.withMember("event_streams", value));
    if (!operation.hasTrait(EXCLUDE_OPERATION)) {
      resultRoots.add(resolveResultRoot(operation));
    }
    return builder.build();
  }

  private ObjectNode compileHttp(
      OperationShape operation, HttpTrait http, List<StructureShape> errors) {
    var input = operationIndex.expectInputShape(operation);
    var output = operationIndex.expectOutputShape(operation);
    var errorBindings = ObjectNode.builder();
    for (var error : errors) {
      errorBindings.withMember(
          error.getId().toString(),
          ObjectNode.builder()
              .withMember("code", httpBindings.getResponseCode(error))
              .withMember(
                  "bindings", bindings(error, httpBindings.getResponseBindings(error), http))
              .build());
    }
    return ObjectNode.builder()
        .withMember("method", http.getMethod())
        .withMember("uri", http.getUri().toString())
        .withMember("code", httpBindings.getResponseCode(operation))
        .withMember(
            "request_bindings", bindings(input, httpBindings.getRequestBindings(operation), http))
        .withMember(
            "response_bindings",
            bindings(output, httpBindings.getResponseBindings(operation), http))
        .withMember("error_bindings", errorBindings.build())
        .build();
  }

  private ArrayNode bindings(
      StructureShape container, Map<String, HttpBinding> bindings, HttpTrait http) {
    var result = new ArrayList<Node>();
    for (var member : container.members()) {
      var binding = bindings.get(member.getMemberName());
      if (binding != null) {
        var greedy =
            binding.getLocation() == HttpBinding.Location.LABEL
                && http.getUri()
                    .getLabel(binding.getLocationName())
                    .map(segment -> segment.isGreedyLabel())
                    .orElse(false);
        result.add(
            ObjectNode.builder()
                .withMember("member_id", member.getId().toString())
                .withMember("location", binding.getLocation().toString().toLowerCase(Locale.ROOT))
                .withMember("name", binding.getLocationName())
                .withMember("greedy", greedy)
                .build());
      }
    }
    return array(result);
  }

  private Optional<ObjectNode> compileEventStreams(OperationShape operation) {
    var input = eventStreams.getInputInfo(operation);
    var output = eventStreams.getOutputInfo(operation);
    if (input.isEmpty() && output.isEmpty()) {
      return Optional.empty();
    }
    var builder = ObjectNode.builder();
    input.ifPresent(
        info -> builder.withMember("input", info.getEventStreamMember().getId().toString()));
    output.ifPresent(
        info -> builder.withMember("output", info.getEventStreamMember().getId().toString()));
    return Optional.of(builder.build());
  }

  private ShapeId resolveResultRoot(OperationShape operation) {
    return resolveOperationResult(operation).terminalShape().getId();
  }

  private ResolvedResult resolveOperationResult(OperationShape operation) {
    var explicit = traitObject(operation, RESULT);
    if (explicit.isPresent()) {
      return resolveResult(operation, explicit.orElseThrow());
    }
    if (operation.hasTrait(HttpTrait.ID)) {
      var payloads = httpBindings.getResponseBindings(operation, HttpBinding.Location.PAYLOAD);
      if (payloads.size() == 1) {
        var payload = payloads.get(0).getMember();
        return new ResolvedResult(
            List.of(payload.getMemberName()), model.expectShape(payload.getTarget()));
      }
    }
    return new ResolvedResult(List.of(), operationIndex.expectOutputShape(operation));
  }

  private ResolvedResult resolveResult(OperationShape operation, ObjectNode node) {
    var path =
        node.expectArrayMember("path").getElements().stream()
            .map(value -> value.expectStringNode().getValue())
            .toList();
    Shape current = operationIndex.expectOutputShape(operation);
    for (var memberName : path) {
      if (!(current instanceof StructureShape structure)) {
        throw new IllegalArgumentException(
            "result path for "
                + operation.getId()
                + " cannot traverse "
                + current.getId()
                + " because it is not a structure");
      }
      var member =
          structure
              .getMember(memberName)
              .orElseThrow(
                  () ->
                      new IllegalArgumentException(
                          "result path for "
                              + operation.getId()
                              + " references missing member "
                              + structure.getId().withMember(memberName)));
      current = model.expectShape(member.getTarget());
    }
    return new ResolvedResult(path, current);
  }

  private ObjectNode compileShape(Shape shape) {
    var builder =
        ObjectNode.builder()
            .withMember("id", shape.getId().toString())
            .withMember("kind", shape.getType().toString())
            .withMember("recursive", recursiveShapes.contains(shape.getId()))
            .withMember(
                "members", array(shape.members().stream().map(this::compileMember).toList()))
            .withMember("enum_values", array(List.of()))
            .withMember("traits", traits(shape))
            .withMember("constraints", constraints(shape))
            .withMember("policies", policies(shape))
            .withMember("extensions", extensions(shape));
    documentation(shape).ifPresent(value -> builder.withMember("documentation", value));
    if (shape instanceof EnumShape enumShape) {
      builder.withMember("enum_values", enumValues(enumShape));
    } else if (shape instanceof IntEnumShape intEnumShape) {
      builder.withMember("enum_values", enumValues(intEnumShape));
    }
    return builder.build();
  }

  private ObjectNode compileMember(MemberShape member) {
    var builder =
        ObjectNode.builder()
            .withMember("id", member.getId().toString())
            .withMember("name", member.getMemberName())
            .withMember("target", member.getTarget().toString())
            .withMember("required", member.hasTrait(RequiredTrait.ID))
            .withMember("client_nullable", nullableIndex.isMemberNullable(member))
            .withMember("default", defaultValue(member))
            .withMember("traits", traits(member))
            .withMember("constraints", constraints(member))
            .withMember("policies", policies(member))
            .withMember("extensions", extensions(member));
    documentation(member).ifPresent(value -> builder.withMember("documentation", value));
    return builder.build();
  }

  private ArrayNode enumValues(EnumShape shape) {
    return enumValues(shape.members());
  }

  private ArrayNode enumValues(IntEnumShape shape) {
    return enumValues(shape.members());
  }

  private ArrayNode enumValues(Collection<MemberShape> members) {
    var nodes = new ArrayList<Node>();
    for (var member : members) {
      var builder =
          ObjectNode.builder()
              .withMember("name", member.getMemberName())
              .withMember("value", member.expectTrait(EnumValueTrait.class).toNode())
              .withMember("traits", traits(member))
              .withMember("extensions", extensions(member));
      documentation(member).ifPresent(value -> builder.withMember("documentation", value));
      nodes.add(builder.build());
    }
    return array(nodes);
  }

  private ObjectNode defaultValue(MemberShape member) {
    var trait = member.getTrait(DefaultTrait.class);
    var builder = ObjectNode.builder().withMember("present", trait.isPresent());
    trait.ifPresent(value -> builder.withMember("value", value.toNode()));
    return builder.build();
  }

  private ObjectNode operationPolicies(OperationShape operation) {
    var standardPagination = paginatedIndex.getPaginationInfo(service, operation);
    if (operation.hasTrait(PAGE_NUMBER_PAGINATION) && standardPagination.isPresent()) {
      throw new IllegalArgumentException(
          "operation "
              + operation.getId()
              + " cannot use both smithy.api#paginated and "
              + PAGE_NUMBER_PAGINATION);
    }
    var builder = ObjectNode.builder().withMember("result", result(operation));
    addPortablePolicies(operation, builder);
    standardPagination.ifPresent(
        value -> builder.withMember("smithy_pagination", smithyPagination(value)));
    return builder.build();
  }

  private ObjectNode smithyPagination(PaginationInfo pagination) {
    var builder =
        ObjectNode.builder()
            .withMember("input_token", pagination.getInputTokenMember().getId().toString())
            .withMember(
                "output_token",
                strings(
                    pagination.getOutputTokenMemberPath().stream()
                        .map(Shape::getId)
                        .map(Object::toString)
                        .toList()))
            .withMember(
                "items",
                strings(
                    pagination.getItemsMemberPath().stream()
                        .map(Shape::getId)
                        .map(Object::toString)
                        .toList()));
    pagination
        .getPageSizeMember()
        .ifPresent(value -> builder.withMember("page_size", value.getId().toString()));
    return builder.build();
  }

  private ObjectNode policies(Shape shape) {
    var builder = ObjectNode.builder();
    addPortablePolicies(shape, builder);
    return builder.build();
  }

  private void addPortablePolicies(Shape shape, ObjectNode.Builder builder) {
    if (shape instanceof OperationShape operation) {
      traitObject(operation, NOT_FOUND)
          .ifPresent(value -> builder.withMember("not_found", notFound(value)));
      traitObject(operation, RATE_LIMIT_COST)
          .ifPresent(value -> builder.withMember("rate_limit_cost", rateLimitCost(value)));
      traitObject(operation, PAGE_NUMBER_PAGINATION)
          .ifPresent(
              value ->
                  builder.withMember(
                      "page_number_pagination", pageNumberPagination(operation, value)));
      traitObject(operation, SORTING)
          .ifPresent(value -> builder.withMember("sorting", sorting(operation, value)));
      if (operation.hasTrait(EXCLUDE_OPERATION)) {
        builder.withMember("exclude_operation", true);
      }
    }
    traitObject(shape, QUERY_ENCODING)
        .ifPresent(value -> builder.withMember("query_encoding", queryEncoding(value)));
    traitObject(shape, CLIENT_DEFAULT)
        .ifPresent(value -> builder.withMember("client_default", clientDefault(value)));
    if (shape.hasTrait(EXCLUDE_PARAMETER)) {
      builder.withMember("exclude_parameter", true);
    }
    traitObject(shape, INPUT_ADAPTER)
        .ifPresent(value -> builder.withMember("input_adapter", inputAdapter(value)));
  }

  private ObjectNode result(OperationShape operation) {
    var explicit = traitObject(operation, RESULT);
    var resolved = resolveOperationResult(operation);
    var terminal = resolved.terminalShape();
    var declaredCardinality = explicit.flatMap(node -> stringMember(node, "cardinality"));
    var builder = ObjectNode.builder().withMember("path", strings(resolved.path()));
    if (terminal.getType() == ShapeType.DOCUMENT) {
      var cardinality =
          declaredCardinality.orElseThrow(
              () ->
                  new IllegalArgumentException(
                      "@result cardinality is required for Document target "
                          + terminal.getId()
                          + " on "
                          + operation.getId()));
      builder.withMember("cardinality", cardinality);
    } else if (declaredCardinality.isPresent()) {
      throw new IllegalArgumentException(
          "result cardinality may only be set for a Document target on " + operation.getId());
    }
    return builder.build();
  }

  private static ObjectNode notFound(ObjectNode node) {
    return ObjectNode.builder()
        .withMember("behavior", stringMember(node, "behavior").orElse("raise"))
        .build();
  }

  private static ObjectNode rateLimitCost(ObjectNode node) {
    return ObjectNode.builder()
        .withMember("units", node.expectNumberMember("units").getValue())
        .build();
  }

  private ObjectNode pageNumberPagination(OperationShape operation, ObjectNode node) {
    var input = operationIndex.expectInputShape(operation);
    var builder =
        ObjectNode.builder()
            .withMember(
                "page_member",
                resolveInputMember(input, node.expectStringMember("pageMember").getValue())
                    .toString())
            .withMember("start", numberMember(node, "start").orElse(1))
            .withMember("step", numberMember(node, "step").orElse(1));
    stringMember(node, "pageSizeMember")
        .ifPresent(
            value ->
                builder.withMember(
                    "page_size_member", resolveInputMember(input, value).toString()));
    return builder.build();
  }

  private ObjectNode sorting(OperationShape operation, ObjectNode node) {
    var input = operationIndex.expectInputShape(operation);
    var encoding = node.expectStringMember("encoding").getValue();
    var orderMember = stringMember(node, "orderMember");
    if (encoding.equals("separate") && orderMember.isEmpty()) {
      throw new IllegalArgumentException(
          "sorting orderMember is required for separate encoding on " + operation.getId());
    }
    if (encoding.equals("suffix") && orderMember.isPresent()) {
      throw new IllegalArgumentException(
          "sorting orderMember is not allowed for suffix encoding on " + operation.getId());
    }
    var builder =
        ObjectNode.builder()
            .withMember("encoding", encoding)
            .withMember(
                "sort_member",
                resolveInputMember(input, node.expectStringMember("sortMember").getValue())
                    .toString())
            .withMember("separator", stringMember(node, "separator").orElse("."));
    orderMember.ifPresent(
        value -> builder.withMember("order_member", resolveInputMember(input, value).toString()));
    return builder.build();
  }

  private static ObjectNode queryEncoding(ObjectNode node) {
    return ObjectNode.builder()
        .withMember("style", node.expectStringMember("style").getValue())
        .withMember("explode", node.expectBooleanMember("explode").getValue())
        .withMember(
            "allow_reserved",
            node.getBooleanMember("allowReserved").map(value -> value.getValue()).orElse(false))
        .build();
  }

  private static ObjectNode clientDefault(ObjectNode node) {
    return ObjectNode.builder()
        .withMember(
            "value",
            node.getMember("value")
                .orElseThrow(() -> new IllegalArgumentException("clientDefault requires value")))
        .build();
  }

  private static ObjectNode inputAdapter(ObjectNode node) {
    return ObjectNode.builder().withMember("id", node.expectStringMember("id").getValue()).build();
  }

  private static ShapeId resolveInputMember(StructureShape input, String memberName) {
    return input
        .getMember(memberName)
        .orElseThrow(
            () ->
                new IllegalArgumentException(
                    "portable policy references missing input member "
                        + input.getId().withMember(memberName)))
        .getId();
  }

  private ObjectNode traits(Shape shape) {
    return traitMap(shape, false, false);
  }

  private ObjectNode constraints(Shape shape) {
    return traitMap(shape, false, true);
  }

  private ObjectNode extensions(Shape shape) {
    return traitMap(shape, true, false);
  }

  private static ObjectNode externalDocumentation(Shape shape) {
    return shape
        .getTrait(ExternalDocumentationTrait.class)
        .map(value -> ObjectNode.fromStringMap(new TreeMap<>(value.getUrls())))
        .orElseGet(Node::objectNode);
  }

  private ObjectNode traitMap(Shape shape, boolean extensionsOnly, boolean constraintsOnly) {
    var builder = ObjectNode.builder();
    var sorted = new TreeMap<>(shape.getAllTraits());
    for (var entry : sorted.entrySet()) {
      var namespace = entry.getKey().getNamespace();
      var isExtension =
          namespace.startsWith(TARGET_NAMESPACE_PREFIX)
              && !namespace.equals(PORTABLE_NAMESPACE)
              && !namespace.equals(PROTOCOL_NAMESPACE);
      if (extensionsOnly != isExtension) {
        continue;
      }
      if (constraintsOnly && !CONSTRAINT_TRAITS.contains(entry.getKey())) {
        continue;
      }
      if (!constraintsOnly || CONSTRAINT_TRAITS.contains(entry.getKey())) {
        builder.withMember(entry.getKey().toString(), entry.getValue().toNode());
      }
    }
    return builder.build();
  }

  private ArrayNode authSchemes(Shape shape) {
    Map<ShapeId, ?> schemes;
    if (shape instanceof ServiceShape) {
      schemes =
          serviceIndex.getEffectiveAuthSchemes(service, ServiceIndex.AuthSchemeMode.NO_AUTH_AWARE);
    } else {
      schemes =
          serviceIndex.getEffectiveAuthSchemes(
              service, shape, ServiceIndex.AuthSchemeMode.NO_AUTH_AWARE);
    }
    return strings(schemes.keySet().stream().map(ShapeId::toString).sorted().toList());
  }

  private Set<Shape> serviceClosure() {
    var closure = new LinkedHashSet<>(new Walker(model).walkShapes(service));
    for (var operation : topDownIndex.getContainedOperations(service)) {
      traitObject(operation, RESULT)
          .ifPresent(
              value ->
                  closure.addAll(
                      new Walker(model)
                          .walkShapes(resolveResult(operation, value).terminalShape())));
    }
    return closure;
  }

  private static boolean isDataShape(Shape shape) {
    return !Prelude.isPreludeShape(shape)
        && shape.getType() != ShapeType.SERVICE
        && shape.getType() != ShapeType.OPERATION
        && shape.getType() != ShapeType.RESOURCE
        && shape.getType() != ShapeType.MEMBER;
  }

  private static Optional<ObjectNode> traitObject(Shape shape, ShapeId traitId) {
    return shape.findTrait(traitId).map(trait -> trait.toNode().expectObjectNode());
  }

  private static Optional<String> documentation(Shape shape) {
    return shape.getTrait(DocumentationTrait.class).map(DocumentationTrait::getValue);
  }

  private static Optional<String> stringMember(ObjectNode node, String name) {
    return node.getStringMember(name).map(value -> value.getValue());
  }

  private static Optional<Number> numberMember(ObjectNode node, String name) {
    return node.getNumberMember(name).map(value -> value.getValue());
  }

  private static ArrayNode strings(List<String> values) {
    return Node.fromStrings(values);
  }

  private static ArrayNode array(List<? extends Node> values) {
    return Node.fromNodes(values);
  }

  private record ResolvedResult(List<String> path, Shape terminalShape) {}
}
