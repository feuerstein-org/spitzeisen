package org.feuerstein.spitzeisen.codegen;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.HttpBinding;
import software.amazon.smithy.model.knowledge.HttpBindingIndex;
import software.amazon.smithy.model.knowledge.OperationIndex;
import software.amazon.smithy.model.knowledge.TopDownIndex;
import software.amazon.smithy.model.node.ArrayNode;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.node.ObjectNode;
import software.amazon.smithy.model.shapes.CollectionShape;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.shapes.OperationShape;
import software.amazon.smithy.model.shapes.ServiceShape;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeId;
import software.amazon.smithy.model.traits.DefaultTrait;
import software.amazon.smithy.model.traits.DocumentationTrait;
import software.amazon.smithy.model.traits.ExternalDocumentationTrait;
import software.amazon.smithy.model.traits.HttpTrait;
import software.amazon.smithy.model.traits.JsonNameTrait;
import software.amazon.smithy.model.traits.RangeTrait;

/** Compiles Smithy's semantic model into the renderer contract used by Spitzeisen. */
final class ClientPlanCompiler {
    private static final ShapeId SDK_OPERATION = ShapeId.from("spitzeisen.api#sdkOperation");
    private static final ShapeId PAGE_NUMBER_PAGINATION = ShapeId.from("spitzeisen.api#pageNumberPagination");
    private static final ShapeId SORTING = ShapeId.from("spitzeisen.api#sorting");
    private static final ShapeId PYTHON_PARAMETER = ShapeId.from("spitzeisen.api#pythonParameter");
    private static final ShapeId HIDDEN = ShapeId.from("spitzeisen.api#hidden");
    private static final ShapeId MODEL_PROPERTY = ShapeId.from("spitzeisen.api#modelProperty");
    private static final ShapeId PAGINATED = ShapeId.from("smithy.api#paginated");
    private static final Pattern URI_LABEL = Pattern.compile("\\{([^}+]+)\\+?}");
    private static final Pattern FIRST_CAPITAL = Pattern.compile("(.)([A-Z][a-z]+)");
    private static final Pattern SECOND_CAPITAL = Pattern.compile("([a-z0-9])([A-Z])");
    private static final Pattern PYTHON_IDENTIFIER = Pattern.compile("[A-Za-z_][A-Za-z0-9_]*");
    private static final Set<String> PYTHON_KEYWORDS = Set.of(
            "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
            "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
            "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
            "try", "while", "with", "yield"
    );
    private static final Set<String> QUERY_STYLES = Set.of("form", "spaceDelimited", "pipeDelimited");

    private final Model model;
    private final SpitzeisenSettings settings;
    private final HttpBindingIndex httpBindings;
    private final OperationIndex operationIndex;
    private final TopDownIndex topDownIndex;

    ClientPlanCompiler(Model model, SpitzeisenSettings settings) {
        this.model = model;
        this.settings = settings;
        this.httpBindings = HttpBindingIndex.of(model);
        this.operationIndex = OperationIndex.of(model);
        this.topDownIndex = TopDownIndex.of(model);
    }

    CompiledClientPlan compile() {
        validateTargetSettings();
        rejectConverterPlaceholders();
        var service = resolveService();
        var responseShapes = new LinkedHashSet<ShapeId>();
        var operations = new ArrayList<CompiledOperation>();
        topDownIndex.getContainedOperations(service).stream()
                .sorted(Comparator.comparing(operation -> operation.getId().toString()))
                .filter(operation -> !operation.hasTrait(HIDDEN))
                .forEach(operation -> operations.add(compileOperation(service, operation, responseShapes)));
        if (operations.isEmpty()) {
            throw new IllegalArgumentException("service " + service.getId() + " contains no visible operations");
        }
        validateOperationNames(operations);

        var customizations = modelCustomizations();
        var client = ObjectNode.builder()
                .withMember("service_id", service.getId().toString())
                .withMember("vendor", settings.vendor().orElseGet(() -> humanizeServiceName(service.getId().getName())))
                .withMember("package", settings.packageName())
                .withMember("client_name", settings.clientName())
                .withMember("operations", array(operations.stream().map(CompiledOperation::node).toList()))
                .withMember("response_shapes", strings(responseShapes.stream().map(ShapeId::toString).toList()))
                .withMember("model_aliases", customizations.aliases())
                .withMember("model_type_overrides", customizations.typeOverrides())
                .build();
        var document = ObjectNode.builder().withMember("schema_version", 1).withMember("client", client).build();
        return new CompiledClientPlan(document, Set.copyOf(responseShapes));
    }

    private void rejectConverterPlaceholders() {
        var placeholders = model.toSet().stream()
                .filter(shape -> shape.getAllTraits().keySet().stream()
                        .anyMatch(id -> id.toString().endsWith("#errorMessage")))
                .map(shape -> shape.getId().toString())
                .sorted()
                .toList();
        if (!placeholders.isEmpty()) {
            throw new IllegalArgumentException(
                    "OpenAPI conversion left unsupported Smithy placeholders: " + placeholders
            );
        }
    }

    private ServiceShape resolveService() {
        if (settings.service().isPresent()) {
            return model.expectShape(settings.service().orElseThrow(), ServiceShape.class);
        }
        if (model.getServiceShapes().size() != 1) {
            throw new IllegalArgumentException("service is required when the model does not contain exactly one service");
        }
        return model.getServiceShapes().iterator().next();
    }

    private CompiledOperation compileOperation(
            ServiceShape service,
            OperationShape operation,
            Set<ShapeId> responseShapes
    ) {
        var http = operation.expectTrait(HttpTrait.class);
        if (!http.getMethod().equalsIgnoreCase("GET")) {
            throw new IllegalArgumentException(
                    operation.getId() + " uses " + http.getMethod() + "; the current renderer supports GET only"
            );
        }
        if (httpBindings.hasRequestBody(operation)) {
            throw new IllegalArgumentException(operation.getId() + " has a request body; the current renderer does not");
        }
        if (operation.hasTrait(PAGINATED)) {
            throw new IllegalArgumentException(operation.getId() + " uses cursor pagination, which is not supported yet");
        }

        var sdk = traitObject(operation, SDK_OPERATION);
        var pagination = paginationPolicy(operation);
        var sortingPolicy = sortingPolicy(operation);
        var response = inferResponse(service, operation);
        var shape = stringMember(sdk, "shape").orElse(pagination == null ? response.cardinality() : "collection");
        var modelName = stringMember(sdk, "responseModel").orElse(response.modelName());
        if (modelName == null) {
            throw new IllegalArgumentException(
                    operation.getId() + " has no inferable response model; set @sdkOperation(responseModel: ...)"
            );
        }
        validateOperationPolicy(operation, sdk, pagination, sortingPolicy, shape);
        var generateModel = booleanMember(sdk, "generateModel").orElse(true);
        if (generateModel && response.shapeId() != null) {
            responseShapes.add(response.shapeId());
        }

        var methodName = stringMember(sdk, "methodName").orElseGet(() -> snakeCase(operation.getId().getName()));
        var key = stringMember(sdk, "name").orElseGet(() -> operationKey(methodName, http.getMethod()));
        var path = http.getUri().toString();
        var boundMembers = boundMembers(operation);
        var structural = structuralParameters(pagination, sortingPolicy);
        var params = compileParameters(service, operation, path, boundMembers, structural);
        var pathParams = params.stream().filter(param -> param.location() == HttpBinding.Location.LABEL).toList();
        var queryParams = params.stream().filter(param -> param.location() == HttpBinding.Location.QUERY).toList();
        var headerParams = params.stream().filter(param -> param.location() == HttpBinding.Location.HEADER).toList();
        var publicParams = new ArrayList<CompiledParam>();
        publicParams.addAll(queryParams);
        publicParams.addAll(headerParams);
        var sorting = compileSorting(service, operation, sortingPolicy, boundMembers);
        var pageSize = compilePageSize(operation, pagination, boundMembers);
        validateSignatureNames(key, shape, pathParams, publicParams, sorting);

        var exampleArgs = new ArrayList<String>();
        pathParams.stream().filter(param -> param.clientDefault() == null).forEach(param -> exampleArgs.add(param.name()));
        publicParams.stream().filter(CompiledParam::required).forEach(param -> exampleArgs.add(param.name()));

        var modelImports = modelImports(service, operation, modelName);
        var coerceFunctionImports = coerceFunctionImports(operation);
        var calls = new ArrayList<String>();
        params.forEach(param -> calls.add(param.coercion()));
        pathParams.forEach(param -> calls.add(param.coercion()));
        pathParams.forEach(param -> calls.add("require_value("));
        headerParams.stream().filter(CompiledParam::required).forEach(param -> calls.add("require_value("));
        if (sorting != null) {
            calls.add(sorting.sort().coercion());
            calls.add(sorting.order().coercion());
            if (sorting.style().equals("suffix")) {
                calls.add("coerce_sort(");
            }
        }
        var helpers = helpersUsed(calls, modelImports);

        var builder = ObjectNode.builder()
                .withMember("key", key)
                .withMember("path", path)
                .withMember("method_name", methodName)
                .withMember("model", modelName)
                .withMember("summary", documentation(operation))
                .withMember("generate_model", generateModel)
                .withMember("shape", shape)
                .withMember("not_found", stringMember(sdk, "notFound").orElse("raise"))
                .withMember("cost", numberMember(sdk, "cost").orElse(1.0))
                .withMember("pagination", pagination == null ? "none" : "page_number")
                .withMember("page_start", pagination == null ? 1 : pagination.start())
                .withMember("page_step", pagination == null ? 1 : pagination.step())
                .withMember("params", paramNodes(publicParams))
                .withMember("query_params", paramNodes(queryParams))
                .withMember("header_params", paramNodes(headerParams))
                .withMember("path_params", paramNodes(pathParams))
                .withMember("example_args", strings(exampleArgs))
                .withMember("model_imports", strings(modelImports))
                .withMember("coerce_function_imports", strings(coerceFunctionImports))
                .withMember("helpers", strings(helpers));
        nullable(builder, "docs_url", stringMember(sdk, "documentationUrl").orElseGet(() -> serviceDocs(service)));
        nullable(builder, "results_key", resultPath(sdk, pagination));
        nullable(builder, "page_param", pagination == null ? null : pagination.pageParam());
        builder.withMember("sorting", sorting == null ? Node.nullNode() : sorting.toNode());
        builder.withMember("page_size", pageSize == null ? Node.nullNode() : pageSize.toNode());
        return new CompiledOperation(builder.build(), key, modelName, generateModel);
    }

    private ResponseShape inferResponse(ServiceShape service, OperationShape operation) {
        ShapeId target;
        var payloads = httpBindings.getResponseBindings(operation, HttpBinding.Location.PAYLOAD);
        if (payloads.size() == 1) {
            target = payloads.get(0).getMember().getTarget();
        } else {
            target = operationIndex.expectOutputShape(operation).getId();
        }
        var shape = model.expectShape(target);
        if (shape instanceof CollectionShape collection) {
            var item = collection.getMember().getTarget();
            var itemShape = model.expectShape(item);
            return new ResponseShape(
                    itemShape.isStructureShape() ? service.getContextualName(item) : null,
                    "collection",
                    itemShape.isStructureShape() ? item : null
            );
        }
        return new ResponseShape(
                shape.isStructureShape() ? service.getContextualName(target) : null,
                "single",
                shape.isStructureShape() ? target : null
        );
    }

    private Map<String, BoundMember> boundMembers(OperationShape operation) {
        var bindings = httpBindings.getRequestBindings(operation);
        var input = operationIndex.expectInputShape(operation);
        var result = new LinkedHashMap<String, BoundMember>();
        for (var member : input.members()) {
            var binding = bindings.get(member.getMemberName());
            if (binding == null) {
                continue;
            }
            if (!Set.of(HttpBinding.Location.LABEL, HttpBinding.Location.QUERY, HttpBinding.Location.HEADER)
                    .contains(binding.getLocation())) {
                throw new IllegalArgumentException(
                        operation.getId() + " uses unsupported input binding " + binding.getLocation()
                );
            }
            result.put(binding.getLocationName(), new BoundMember(member, binding));
        }
        return result;
    }

    private List<CompiledParam> compileParameters(
            ServiceShape service,
            OperationShape operation,
            String path,
            Map<String, BoundMember> boundMembers,
            Set<String> structural
    ) {
        var byMember = new LinkedHashMap<String, CompiledParam>();
        for (var source : boundMembers.values()) {
            var member = source.member();
            var binding = source.binding();
            var wireName = binding.getLocationName();
            if (member.hasTrait(HIDDEN)
                    || (binding.getLocation() == HttpBinding.Location.QUERY && structural.contains(wireName))) {
                continue;
            }
            var customization = parameterPolicy(member);
            var name = customization.name() == null ? pythonName(wireName) : customization.name();
            validateArgumentName(name, wireName);
            var required = binding.getLocation() == HttpBinding.Location.LABEL || member.isRequired();
            var defaultValue = clientDefault(member, customization, required, wireName);
            var description = documentation(member);
            if (description.isBlank()) {
                var kind = binding.getLocation() == HttpBinding.Location.LABEL ? "Path" : "Query";
                description = kind + " param `" + wireName + "`.";
            }
            var location = binding.getLocation();
            byMember.put(member.getMemberName(), new CompiledParam(
                    name,
                    wireName,
                    annotation(service, member, customization),
                    tidy(description),
                    coercion(member, customization, name),
                    location,
                    queryStyle(customization),
                    location == HttpBinding.Location.QUERY ? queryExplode(customization) : true,
                    required,
                    defaultValue
            ));
        }

        var ordered = new ArrayList<CompiledParam>();
        var matcher = URI_LABEL.matcher(path);
        while (matcher.find()) {
            var label = matcher.group(1);
            boundMembers.values().stream()
                    .filter(source -> source.binding().getLocation() == HttpBinding.Location.LABEL)
                    .filter(source -> source.binding().getLocationName().equals(label))
                    .map(source -> byMember.remove(source.member().getMemberName()))
                    .filter(java.util.Objects::nonNull)
                    .findFirst()
                    .ifPresent(ordered::add);
        }
        byMember.values().stream().filter(param -> param.location() == HttpBinding.Location.QUERY).forEach(ordered::add);
        byMember.values().stream().filter(param -> param.location() == HttpBinding.Location.HEADER).forEach(ordered::add);
        validateUniqueArguments(operation, ordered);
        return ordered;
    }

    private String annotation(ServiceShape service, MemberShape member, ParameterPolicy customization) {
        if (customization.annotation() != null) {
            return customization.annotation();
        }
        return switch (customization.coercion()) {
            case "date" -> "str | date | datetime";
            case "comma_list" -> "list[str]";
            case "comma_choice_list" -> "list[" + requiredLiteral(customization) + "]";
            case "choice" -> requiredLiteral(customization);
            default -> annotation(service, member, new HashSet<>());
        };
    }

    private String annotation(ServiceShape service, MemberShape member, Set<ShapeId> visiting) {
        if (hasDateFormat(member) || hasDateFormat(model.expectShape(member.getTarget()))) {
            return "str | date | datetime";
        }
        return annotation(service, model.expectShape(member.getTarget()), visiting);
    }

    private String annotation(ServiceShape service, Shape shape, Set<ShapeId> visiting) {
        if (!visiting.add(shape.getId())) {
            return service.getContextualName(shape.getId());
        }
        try {
            return switch (shape.getType()) {
                case BLOB, STRING, TIMESTAMP -> "str";
                case BYTE, SHORT, INTEGER, LONG, BIG_INTEGER, INT_ENUM -> {
                    if (shape.isIntEnumShape()) {
                        var values = shape.asIntEnumShape().orElseThrow().getEnumValues().values();
                        yield literal(values.stream().map(value -> (Object) value).toList());
                    }
                    yield "int";
                }
                case FLOAT, DOUBLE, BIG_DECIMAL -> "float";
                case BOOLEAN -> "bool";
                case ENUM -> {
                    var values = shape.asEnumShape().orElseThrow().getEnumValues().values();
                    yield literal(values.stream().map(value -> (Object) value).toList());
                }
                case LIST, SET -> {
                    var member = ((CollectionShape) shape).getMember();
                    yield "list[" + annotation(service, member, visiting) + "]";
                }
                case MAP -> {
                    var value = shape.asMapShape().orElseThrow().getValue();
                    yield "dict[str, " + annotation(service, value, visiting) + "]";
                }
                case DOCUMENT -> "dict[str, object]";
                case STRUCTURE -> service.getContextualName(shape.getId());
                case UNION -> {
                    var values = new LinkedHashSet<String>();
                    shape.asUnionShape().orElseThrow().members()
                            .forEach(member -> values.add(annotation(service, member, visiting)));
                    yield String.join(" | ", values);
                }
                default -> "str";
            };
        } finally {
            visiting.remove(shape.getId());
        }
    }

    private String coercion(MemberShape member, ParameterPolicy customization, String name) {
        if (customization.function() != null) {
            var literal = customization.literal() == null ? "None" : customization.literal();
            return customization.function() + "(" + name + ", param_name=\"" + name + "\", literal_type="
                    + literal + ")";
        }
        var style = customization.coercion();
        if (style.equals("plain")
                && (hasDateFormat(member) || hasDateFormat(model.expectShape(member.getTarget())))) {
            style = "date";
        }
        return switch (style) {
            case "date" -> "coerce_date(" + name + ", \"" + name + "\")";
            case "comma_list" -> "\",\".join(" + name + ") if " + name + " else None";
            case "comma_choice_list" -> "coerce_choices(" + name + ", " + requiredLiteral(customization)
                    + ", \"" + name + "\")";
            case "choice" -> "coerce_choice(" + name + ", " + requiredLiteral(customization)
                    + ", \"" + name + "\")";
            default -> name;
        };
    }

    private static String requiredLiteral(ParameterPolicy customization) {
        if (customization.literal() == null) {
            throw new IllegalArgumentException(
                    "coercion='" + customization.coercion() + "' requires @pythonParameter literal"
            );
        }
        return customization.literal();
    }

    private ParameterPolicy parameterPolicy(MemberShape member) {
        var node = traitObject(member, PYTHON_PARAMETER);
        var coercion = stringMember(node, "coercion").orElse("plain");
        var function = stringMember(node, "function").orElse(null);
        var literal = stringMember(node, "literal").orElse(null);
        if (Set.of("choice", "comma_choice_list").contains(coercion) && literal == null) {
            throw new IllegalArgumentException(
                    "@pythonParameter coercion='" + coercion + "' requires literal on " + member.getId()
            );
        }
        if (function != null && !coercion.equals("plain")) {
            throw new IllegalArgumentException(
                    "@pythonParameter function cannot be combined with a non-plain coercion on " + member.getId()
            );
        }
        if (function != null) {
            validateIdentifier(function, "@pythonParameter function");
        }
        if (literal != null) {
            validateIdentifier(literal, "@pythonParameter literal");
        }
        var style = stringMember(node, "style").orElse(null);
        if (style != null && !QUERY_STYLES.contains(style)) {
            throw new IllegalArgumentException("query param style '" + style + "' is unsupported");
        }
        return new ParameterPolicy(
                stringMember(node, "name").orElse(null),
                style,
                booleanMember(node, "explode").orElse(null),
                coercion,
                function,
                literal,
                stringMember(node, "annotation").orElse(null),
                node.getMember("clientDefault").orElse(null)
        );
    }

    private String clientDefault(
            MemberShape member,
            ParameterPolicy customization,
            boolean required,
            String wireName
    ) {
        if (customization.clientDefault() != null) {
            var result = pythonRepr(customization.clientDefault());
            if (required && result.equals("None")) {
                throw new IllegalArgumentException(
                        "required param '" + wireName + "' cannot declare a client_default of None"
                );
            }
            return result;
        }
        if (required) {
            return null;
        }
        return member.getMemberTrait(model, DefaultTrait.class)
                .map(trait -> pythonRepr(trait.toNode()))
                .orElse("None");
    }

    private PaginationPolicy paginationPolicy(OperationShape operation) {
        if (!operation.hasTrait(PAGE_NUMBER_PAGINATION)) {
            return null;
        }
        var node = traitObject(operation, PAGE_NUMBER_PAGINATION);
        var pageSize = stringMember(node, "pageSize").orElse(null);
        var maximum = integerMember(node, "maxPageSize").orElse(null);
        var start = integerMember(node, "start").orElse(1);
        var step = integerMember(node, "step").orElse(1);
        if (maximum != null && pageSize == null) {
            throw new IllegalArgumentException("@pageNumberPagination maxPageSize requires pageSize");
        }
        if (step < 1) {
            throw new IllegalArgumentException("@pageNumberPagination step must be at least one");
        }
        return new PaginationPolicy(
                stringMember(node, "page").orElse("page"),
                pageSize,
                stringMember(node, "items").orElse(null),
                maximum,
                start,
                step
        );
    }

    private SortingPolicy sortingPolicy(OperationShape operation) {
        if (!operation.hasTrait(SORTING)) {
            return null;
        }
        var node = traitObject(operation, SORTING);
        var style = stringMember(node, "style").orElseThrow(
                () -> new IllegalArgumentException("@sorting requires style")
        );
        var sort = stringMember(node, "sort").orElseThrow(
                () -> new IllegalArgumentException("@sorting requires sort")
        );
        var order = stringMember(node, "order").orElse(null);
        if (style.equals("suffix") && order != null) {
            throw new IllegalArgumentException("@sorting style 'suffix' cannot declare order");
        }
        if (style.equals("param") && order == null) {
            throw new IllegalArgumentException("@sorting style 'param' requires order");
        }
        if (!Set.of("suffix", "param").contains(style)) {
            throw new IllegalArgumentException("@sorting style must be 'suffix' or 'param'");
        }
        if (sort.equals(order)) {
            throw new IllegalArgumentException("@sorting sort and order must name different members");
        }
        var sortLiteral = stringMember(node, "sortLiteral").orElse(null);
        var orderLiteral = stringMember(node, "orderLiteral").orElse(null);
        if (sortLiteral != null) {
            validateIdentifier(sortLiteral, "@sorting sortLiteral");
        }
        if (orderLiteral != null) {
            validateIdentifier(orderLiteral, "@sorting orderLiteral");
        }
        return new SortingPolicy(
                style,
                sort,
                order,
                sortLiteral,
                orderLiteral,
                node.getMember("sortDefault").orElse(null),
                node.getMember("orderDefault").orElse(null),
                node.getMember("sortDefault").isPresent(),
                node.getMember("orderDefault").isPresent()
        );
    }

    private CompiledSorting compileSorting(
            ServiceShape service,
            OperationShape operation,
            SortingPolicy policy,
            Map<String, BoundMember> members
    ) {
        if (policy == null) {
            return null;
        }
        var sortSource = namedQueryMember(operation, members, policy.sortParam(), "sorting");
        var orderSource = policy.orderParam() == null
                ? null
                : namedQueryMember(operation, members, policy.orderParam(), "sorting");
        var sortDefault = memberDefault(sortSource.member());
        var orderDefault = orderSource == null ? null : memberDefault(orderSource.member());
        var sortValues = enumValues(sortSource.member());
        var orderValues = orderSource == null ? List.<Object>of() : enumValues(orderSource.member());
        if (policy.style().equals("suffix")) {
            var defaults = splitSuffixDefault(sortDefault);
            sortDefault = defaults.field();
            if (orderDefault == null) {
                orderDefault = defaults.direction();
            }
            var literals = splitSuffixLiterals(sortValues, orderValues);
            sortValues = literals.fields();
            orderValues = literals.directions();
        }
        if (policy.hasSortDefault()) {
            sortDefault = policy.sortDefault();
        }
        if (policy.hasOrderDefault()) {
            orderDefault = policy.orderDefault();
        }
        if (sortDefault == null || sortDefault.isNullNode()) {
            throw new IllegalArgumentException(
                    "sorting param '" + policy.sortParam() + "' needs a Smithy @default or @sorting sortDefault"
            );
        }
        if (orderDefault == null || orderDefault.isNullNode()) {
            var source = policy.orderParam() == null
                    ? "the suffix on '" + policy.sortParam() + "'"
                    : "'" + policy.orderParam() + "'";
            throw new IllegalArgumentException(
                    "sorting direction " + source + " needs a Smithy @default or @sorting orderDefault"
            );
        }
        return new CompiledSorting(
                policy.style(),
                sortArgument(
                        service,
                        "sort",
                        policy.sortParam(),
                        sortDefault,
                        sortSource.member(),
                        parameterPolicy(sortSource.member()),
                        policy.sortLiteral(),
                        sortValues
                ),
                sortArgument(
                        service,
                        "order",
                        policy.orderParam(),
                        orderDefault,
                        orderSource == null ? null : orderSource.member(),
                        orderSource == null ? ParameterPolicy.empty() : parameterPolicy(orderSource.member()),
                        policy.orderLiteral(),
                        orderValues
                )
        );
    }

    private CompiledSortArgument sortArgument(
            ServiceShape service,
            String name,
            String wireName,
            Node defaultValue,
            MemberShape member,
            ParameterPolicy customization,
            String explicitLiteral,
            List<Object> values
    ) {
        var literal = explicitLiteral != null ? explicitLiteral : customization.literal();
        if (literal == null && !values.isEmpty()) {
            literal = literal(values);
        }
        String expression;
        if (!customization.coercion().equals("plain") || customization.function() != null) {
            if (member == null) {
                throw new IllegalArgumentException("sorting coercion requires a Smithy input member");
            }
            expression = coercion(member, customization, name);
        } else if (literal != null) {
            expression = "coerce_choice(" + name + ", " + literal + ", \"" + name + "\")";
        } else {
            expression = name;
        }
        var annotation = literal;
        if (annotation == null) {
            annotation = member == null ? "str" : annotation(service, member, customization);
        }
        return new CompiledSortArgument(
                wireName,
                annotation,
                defaultValue,
                expression,
                wireName == null ? "form" : queryStyle(customization),
                wireName == null || queryExplode(customization)
        );
    }

    private CompiledPageSize compilePageSize(
            OperationShape operation,
            PaginationPolicy policy,
            Map<String, BoundMember> members
    ) {
        if (policy == null || policy.pageSizeParam() == null) {
            return null;
        }
        var source = namedQueryMember(operation, members, policy.pageSizeParam(), "page-size");
        var maximum = policy.maxPageSize();
        if (maximum == null) {
            maximum = source.member().getMemberTrait(model, RangeTrait.class)
                    .flatMap(RangeTrait::getMax)
                    .map(BigDecimal::intValue)
                    .orElse(null);
        }
        if (maximum == null) {
            throw new IllegalArgumentException(
                    "page-size param '" + policy.pageSizeParam() + "' needs @pageNumberPagination maxPageSize "
                            + "or a Smithy @range maximum"
            );
        }
        return new CompiledPageSize(policy.pageSizeParam(), maximum);
    }

    private BoundMember namedQueryMember(
            OperationShape operation,
            Map<String, BoundMember> members,
            String wireName,
            String purpose
    ) {
        var result = members.get(wireName);
        if (result == null || result.binding().getLocation() != HttpBinding.Location.QUERY) {
            throw new IllegalArgumentException(
                    purpose + " param '" + wireName + "' is absent from the Smithy operation " + operation.getId()
            );
        }
        return result;
    }

    private List<String> modelImports(ServiceShape service, OperationShape operation, String modelName) {
        var names = new LinkedHashSet<String>();
        var input = operationIndex.expectInputShape(operation);
        for (var member : input.members()) {
            var policy = parameterPolicy(member);
            if (policy.literal() != null) {
                names.add(policy.literal());
            }
            referenceNames(service, member, names, new HashSet<>());
        }
        var sorting = sortingPolicy(operation);
        if (sorting != null) {
            if (sorting.sortLiteral() != null) {
                names.add(sorting.sortLiteral());
            }
            if (sorting.orderLiteral() != null) {
                names.add(sorting.orderLiteral());
            }
        }
        names.remove(modelName);
        return names.stream().sorted().toList();
    }

    private void referenceNames(
            ServiceShape service,
            MemberShape member,
            Set<String> names,
            Set<ShapeId> visiting
    ) {
        referenceNames(service, model.expectShape(member.getTarget()), names, visiting);
    }

    private void referenceNames(ServiceShape service, Shape shape, Set<String> names, Set<ShapeId> visiting) {
        if (!visiting.add(shape.getId())) {
            return;
        }
        try {
            if (shape.isStructureShape()) {
                names.add(service.getContextualName(shape.getId()));
            } else if (shape instanceof CollectionShape collection) {
                referenceNames(service, collection.getMember(), names, visiting);
            } else if (shape.isMapShape()) {
                referenceNames(service, shape.asMapShape().orElseThrow().getValue(), names, visiting);
            } else if (shape.isUnionShape()) {
                shape.asUnionShape().orElseThrow().members()
                        .forEach(member -> referenceNames(service, member, names, visiting));
            }
        } finally {
            visiting.remove(shape.getId());
        }
    }

    private List<String> coerceFunctionImports(OperationShape operation) {
        var names = new LinkedHashSet<String>();
        var input = operationIndex.expectInputShape(operation);
        for (var member : input.members()) {
            var function = parameterPolicy(member).function();
            if (function != null) {
                names.add(function);
            }
        }
        return names.stream().sorted().toList();
    }

    private static List<String> helpersUsed(List<String> calls, List<String> modelImports) {
        var joined = String.join(" ", calls);
        var helpers = new LinkedHashSet<String>();
        var helperCalls = Map.of(
                "coerce_date(", "coerce_date",
                "coerce_choices(", "coerce_choices",
                "coerce_choice(", "coerce_choice",
                "coerce_sort(", "coerce_sort",
                "require_value(", "require_value"
        );
        helperCalls.forEach((call, helper) -> {
            if (joined.contains(call)) {
                helpers.add(helper);
            }
        });
        helpers.add("NoPagination");
        helpers.add("build_header_params");
        helpers.add("serialize_query_param");
        helpers.removeAll(modelImports);
        return helpers.stream().sorted().toList();
    }

    private static Set<String> structuralParameters(PaginationPolicy pagination, SortingPolicy sorting) {
        var result = new LinkedHashSet<String>();
        if (pagination != null) {
            result.add(pagination.pageParam());
            if (pagination.pageSizeParam() != null) {
                result.add(pagination.pageSizeParam());
            }
        }
        if (sorting != null) {
            result.add(sorting.sortParam());
            if (sorting.orderParam() != null) {
                result.add(sorting.orderParam());
            }
        }
        return result;
    }

    private static String resultPath(ObjectNode sdk, PaginationPolicy pagination) {
        var result = pagination == null || pagination.resultsKey() == null
                ? stringMember(sdk, "resultPath").orElse(null)
                : pagination.resultsKey();
        if (result != null && result.contains(".")) {
            throw new IllegalArgumentException("nested resultPath/items paths are not supported by the current runtime");
        }
        return result;
    }

    private void validateOperationPolicy(
            OperationShape operation,
            ObjectNode sdk,
            PaginationPolicy pagination,
            SortingPolicy sorting,
            String shape
    ) {
        var cost = numberMember(sdk, "cost").orElse(1.0);
        if (cost <= 0) {
            throw new IllegalArgumentException("@sdkOperation cost must be greater than zero");
        }
        if (pagination != null && !shape.equals("collection")) {
            throw new IllegalArgumentException(
                    "operation " + operation.getId() + " cannot combine page-number pagination with shape='" + shape + "'"
            );
        }
        if (sorting != null && !shape.equals("collection")) {
            throw new IllegalArgumentException(
                    "operation " + operation.getId() + " cannot combine a single response with sorting"
            );
        }
        if (stringMember(sdk, "notFound").orElse("raise").equals("empty") && !shape.equals("single")) {
            throw new IllegalArgumentException(
                    "operation " + operation.getId() + " can use notFound='empty' only with a single response"
            );
        }
    }

    private static void validateSignatureNames(
            String key,
            String shape,
            List<CompiledParam> pathParams,
            List<CompiledParam> params,
            CompiledSorting sorting
    ) {
        var names = new ArrayList<String>();
        pathParams.forEach(param -> names.add(param.name()));
        params.forEach(param -> names.add(param.name()));
        var reserved = new HashSet<String>();
        if (shape.equals("collection")) {
            reserved.add("max_results");
            reserved.add("on_validation_error");
        }
        if (sorting != null) {
            reserved.add("sort");
            reserved.add("order");
        }
        var duplicates = duplicates(names);
        names.stream().filter(reserved::contains).forEach(duplicates::add);
        if (!duplicates.isEmpty()) {
            throw new IllegalArgumentException(
                    "operation '" + key + "' produces duplicate or reserved arguments " + duplicates.stream().sorted().toList()
            );
        }
    }

    private static void validateUniqueArguments(OperationShape operation, List<CompiledParam> params) {
        var names = params.stream().map(CompiledParam::name).toList();
        var duplicates = duplicates(names);
        if (!duplicates.isEmpty()) {
            throw new IllegalArgumentException(
                    "operation '" + operation.getId().getName() + "' maps multiple wire params to "
                            + duplicates.stream().sorted().toList()
            );
        }
    }

    private static Set<String> duplicates(List<String> values) {
        var seen = new HashSet<String>();
        var duplicates = new LinkedHashSet<String>();
        values.forEach(value -> {
            if (!seen.add(value)) {
                duplicates.add(value);
            }
        });
        return duplicates;
    }

    private static void validateOperationNames(List<CompiledOperation> operations) {
        validateDuplicates(operations.stream().map(operation -> operation.key() + "_api").toList(), "accessor");
        validateDuplicates(operations.stream().map(operation -> className(operation.key())).toList(), "class name");
        validateDuplicates(operations.stream().map(operation -> operation.key().toUpperCase(Locale.ROOT) + "_OPERATION")
                .toList(), "const name");
        validateDuplicates(operations.stream().filter(CompiledOperation::generateModel).map(CompiledOperation::modelName)
                .toList(), "public response model");
    }

    private static void validateDuplicates(List<String> values, String label) {
        var duplicates = duplicates(values);
        if (!duplicates.isEmpty()) {
            throw new IllegalArgumentException(
                    "operations produce duplicate " + label + " values: " + duplicates.stream().sorted().toList()
            );
        }
    }

    private void validateTargetSettings() {
        for (var part : settings.packageName().split("\\.", -1)) {
            validateIdentifier(part, "package");
        }
        validateIdentifier(settings.clientName(), "client name");
    }

    private static void validateArgumentName(String name, String wireName) {
        if (!isIdentifier(name)) {
            throw new IllegalArgumentException(
                    "param '" + wireName + "' maps to invalid Python argument '" + name
                            + "'; apply @pythonParameter(name: ...)"
            );
        }
    }

    private static void validateIdentifier(String value, String label) {
        if (!isIdentifier(value)) {
            throw new IllegalArgumentException(label + " must be a valid Python identifier: '" + value + "'");
        }
    }

    private static boolean isIdentifier(String value) {
        return PYTHON_IDENTIFIER.matcher(value).matches() && !PYTHON_KEYWORDS.contains(value);
    }

    private ModelCustomizations modelCustomizations() {
        var aliases = ObjectNode.builder();
        var typeOverrides = ObjectNode.builder();
        for (var structure : model.getStructureShapes()) {
            for (var member : structure.members()) {
                var customization = traitObject(member, MODEL_PROPERTY);
                if (customization.isEmpty()) {
                    continue;
                }
                var wireName = member.getMemberTrait(model, JsonNameTrait.class)
                        .map(JsonNameTrait::getValue)
                        .orElse(member.getMemberName());
                var key = structure.getId().getName() + "." + wireName;
                stringMember(customization, "name").ifPresent(value -> aliases.withMember(key, value));
                stringMember(customization, "type").ifPresent(value -> typeOverrides.withMember(key, value));
            }
        }
        return new ModelCustomizations(aliases.build(), typeOverrides.build());
    }

    private static ObjectNode traitObject(Shape shape, ShapeId traitId) {
        return shape.findTrait(traitId).map(trait -> trait.toNode().expectObjectNode()).orElseGet(Node::objectNode);
    }

    private static Optional<String> stringMember(ObjectNode node, String name) {
        return node.getStringMember(name).map(value -> value.getValue());
    }

    private static Optional<Boolean> booleanMember(ObjectNode node, String name) {
        return node.getBooleanMember(name).map(value -> value.getValue());
    }

    private static Optional<Double> numberMember(ObjectNode node, String name) {
        return node.getNumberMember(name).map(value -> value.getValue().doubleValue());
    }

    private static Optional<Integer> integerMember(ObjectNode node, String name) {
        return node.getNumberMember(name).map(value -> value.getValue().intValue());
    }

    private static String documentation(Shape shape) {
        return shape.getTrait(DocumentationTrait.class).map(DocumentationTrait::getValue).map(ClientPlanCompiler::tidy)
                .orElse("");
    }

    private String serviceDocs(ServiceShape service) {
        return service.getTrait(ExternalDocumentationTrait.class)
                .flatMap(trait -> trait.getUrls().values().stream().findFirst())
                .orElse(null);
    }

    private static ArrayNode paramNodes(List<CompiledParam> params) {
        return array(params.stream().map(CompiledParam::toNode).toList());
    }

    private static ArrayNode array(List<? extends Node> nodes) {
        return Node.fromNodes(nodes);
    }

    private static ArrayNode strings(List<String> values) {
        return Node.fromStrings(values);
    }

    private static void nullable(ObjectNode.Builder builder, String name, String value) {
        builder.withMember(name, value == null ? Node.nullNode() : Node.from(value));
    }

    private static String snakeCase(String name) {
        var first = FIRST_CAPITAL.matcher(name).replaceAll("$1_$2");
        return SECOND_CAPITAL.matcher(first).replaceAll("$1_$2").replace('-', '_').toLowerCase(Locale.ROOT);
    }

    private static String operationKey(String methodName, String httpMethod) {
        var prefix = httpMethod.toLowerCase(Locale.ROOT) + "_";
        return methodName.startsWith(prefix) && methodName.length() > prefix.length()
                ? methodName.substring(prefix.length())
                : methodName;
    }

    private static String humanizeServiceName(String serviceName) {
        var base = serviceName.endsWith("Service")
                ? serviceName.substring(0, serviceName.length() - "Service".length())
                : serviceName;
        if (base.endsWith("Api")) {
            base = base.substring(0, base.length() - "Api".length());
        }
        return snakeCase(base.isEmpty() ? serviceName : base).replace('_', ' ');
    }

    private static String pythonName(String wireName) {
        return wireName.replace('.', '_').replace('-', '_');
    }

    private static String tidy(String value) {
        return value == null ? "" : value.replaceAll("\\s+", " ").trim();
    }

    private static String queryStyle(ParameterPolicy policy) {
        return policy.style() == null ? "form" : policy.style();
    }

    private static boolean queryExplode(ParameterPolicy policy) {
        return policy.explode() != null ? policy.explode() : false;
    }

    private static boolean hasDateFormat(Shape shape) {
        return shape.getAllTraits().keySet().stream().anyMatch(id -> id.toString().endsWith("#dateFormat"));
    }

    private Node memberDefault(MemberShape member) {
        return member.getMemberTrait(model, DefaultTrait.class).map(DefaultTrait::toNode).orElse(null);
    }

    private List<Object> enumValues(MemberShape member) {
        var target = model.expectShape(member.getTarget());
        if (target.isEnumShape()) {
            return target.asEnumShape().orElseThrow().getEnumValues().values().stream()
                    .map(value -> (Object) value).toList();
        }
        if (target.isIntEnumShape()) {
            return target.asIntEnumShape().orElseThrow().getEnumValues().values().stream()
                    .map(value -> (Object) value).toList();
        }
        return List.of();
    }

    private static SplitDefault splitSuffixDefault(Node value) {
        if (value == null || value.isNullNode()) {
            return new SplitDefault(value, null);
        }
        var text = value.isStringNode() ? value.expectStringNode().getValue() : value.toString();
        var separator = text.lastIndexOf('.');
        if (separator < 0) {
            return new SplitDefault(Node.from(text), null);
        }
        return new SplitDefault(Node.from(text.substring(0, separator)), Node.from(text.substring(separator + 1)));
    }

    private static SplitLiterals splitSuffixLiterals(List<Object> sortValues, List<Object> orderValues) {
        if (sortValues.isEmpty() || sortValues.stream().anyMatch(value -> !(value instanceof String text)
                || !text.contains("."))) {
            return new SplitLiterals(sortValues, orderValues);
        }
        var fields = new LinkedHashSet<Object>();
        var directions = new LinkedHashSet<Object>();
        for (var value : sortValues) {
            var text = (String) value;
            var separator = text.lastIndexOf('.');
            fields.add(text.substring(0, separator));
            directions.add(text.substring(separator + 1));
        }
        return new SplitLiterals(List.copyOf(fields), List.copyOf(directions));
    }

    private static String literal(List<Object> values) {
        return "Literal[" + values.stream().map(ClientPlanCompiler::pythonScalar).reduce((a, b) -> a + ", " + b)
                .orElse("") + "]";
    }

    private static String pythonScalar(Object value) {
        if (value instanceof String text) {
            return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'";
        }
        if (value instanceof Boolean bool) {
            return bool ? "True" : "False";
        }
        return String.valueOf(value);
    }

    private static String pythonRepr(Node node) {
        if (node.isNullNode()) {
            return "None";
        }
        if (node.isStringNode()) {
            return pythonScalar(node.expectStringNode().getValue());
        }
        if (node.isBooleanNode()) {
            return node.expectBooleanNode().getValue() ? "True" : "False";
        }
        if (node.isNumberNode()) {
            return node.expectNumberNode().getValue().toString();
        }
        if (node.isArrayNode()) {
            return "[" + node.expectArrayNode().getElements().stream().map(ClientPlanCompiler::pythonRepr)
                    .reduce((a, b) -> a + ", " + b).orElse("") + "]";
        }
        if (node.isObjectNode()) {
            return "{" + node.expectObjectNode().getStringMap().entrySet().stream()
                    .map(entry -> pythonScalar(entry.getKey()) + ": " + pythonRepr(entry.getValue()))
                    .reduce((a, b) -> a + ", " + b).orElse("") + "}";
        }
        throw new IllegalArgumentException("unsupported Smithy node: " + node);
    }

    private static String className(String key) {
        var result = new StringBuilder();
        for (var part : key.split("_")) {
            if (!part.isEmpty()) {
                result.append(part.substring(0, 1).toUpperCase(Locale.ROOT));
                result.append(part.substring(1).toLowerCase(Locale.ROOT));
            }
        }
        return result + "Api";
    }

    private record ResponseShape(String modelName, String cardinality, ShapeId shapeId) {}

    private record ModelCustomizations(ObjectNode aliases, ObjectNode typeOverrides) {}

    private record CompiledOperation(ObjectNode node, String key, String modelName, boolean generateModel) {}

    private record BoundMember(MemberShape member, HttpBinding binding) {}

    private record ParameterPolicy(
            String name,
            String style,
            Boolean explode,
            String coercion,
            String function,
            String literal,
            String annotation,
            Node clientDefault
    ) {
        static ParameterPolicy empty() {
            return new ParameterPolicy(null, null, null, "plain", null, null, null, null);
        }
    }

    private record PaginationPolicy(
            String pageParam,
            String pageSizeParam,
            String resultsKey,
            Integer maxPageSize,
            int start,
            int step
    ) {}

    private record SortingPolicy(
            String style,
            String sortParam,
            String orderParam,
            String sortLiteral,
            String orderLiteral,
            Node sortDefault,
            Node orderDefault,
            boolean hasSortDefault,
            boolean hasOrderDefault
    ) {}

    private record SplitDefault(Node field, Node direction) {}

    private record SplitLiterals(List<Object> fields, List<Object> directions) {}

    private record CompiledPageSize(String wireName, int maximum) {
        ObjectNode toNode() {
            return ObjectNode.builder().withMember("wire_name", wireName).withMember("maximum", maximum).build();
        }
    }

    private record CompiledSortArgument(
            String wireName,
            String annotation,
            Node defaultValue,
            String coercion,
            String style,
            boolean explode
    ) {
        ObjectNode toNode() {
            var builder = ObjectNode.builder()
                    .withMember("annotation", annotation)
                    .withMember("default", defaultValue)
                    .withMember("coercion", coercion)
                    .withMember("style", style)
                    .withMember("explode", explode);
            nullable(builder, "wire_name", wireName);
            return builder.build();
        }
    }

    private record CompiledSorting(String style, CompiledSortArgument sort, CompiledSortArgument order) {
        ObjectNode toNode() {
            return ObjectNode.builder()
                    .withMember("style", style)
                    .withMember("sort", sort.toNode())
                    .withMember("order", order.toNode())
                    .build();
        }
    }

    private record CompiledParam(
            String name,
            String wireName,
            String annotation,
            String description,
            String coercion,
            HttpBinding.Location location,
            String style,
            boolean explode,
            boolean required,
            String clientDefault
    ) {
        ObjectNode toNode() {
            var builder = ObjectNode.builder()
                    .withMember("name", name)
                    .withMember("wire_name", wireName)
                    .withMember("annotation", annotation)
                    .withMember("description", description)
                    .withMember("coercion", coercion)
                    // Path params retain the renderer's historical default value here.
                    .withMember("location", location == HttpBinding.Location.HEADER ? "header" : "query")
                    .withMember("style", style)
                    .withMember("explode", explode)
                    .withMember("required", required);
            nullable(builder, "client_default", clientDefault);
            return builder.build();
        }
    }
}
