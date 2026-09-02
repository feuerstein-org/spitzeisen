"""Small neutral and Python-target plans for pipeline and renderer tests."""

from __future__ import annotations

from spitzeisen.codegen.python_plan import (
    PythonCoercionPlan,
    PythonDefaultPlan,
    PythonNotFound,
    PythonOperationPlan,
    PythonPageSizePlan,
    PythonPaginationStyle,
    PythonParameterPlan,
    PythonPlan,
    PythonResponseCardinality,
    PythonSortArgumentPlan,
    PythonSortingPlan,
    PythonTypePlan,
    PythonWireLocation,
)
from spitzeisen.codegen.service_plan import (
    DefaultValue,
    HttpBindingPlan,
    HttpPlan,
    MemberPlan,
    OperationPlan,
    Service,
    ServicePlan,
    ShapePlan,
)

PROTOCOL = "spitzeisen.protocols#genericRestJson"
_OPTIONAL_DEFAULT = PythonDefaultPlan("value")


def parameter_plan(
    *,
    name: str,
    wire_name: str,
    type: PythonTypePlan,  # noqa: A002 - mirrors the renderer plan field name
    description: str,
    coercion: PythonCoercionPlan,
    location: PythonWireLocation = "query",
    required: bool = False,
    default: PythonDefaultPlan = _OPTIONAL_DEFAULT,
    explode: bool = True,
) -> PythonParameterPlan:
    """Build one compact renderer-ready Python parameter."""
    return PythonParameterPlan(
        member_id=f"example#Input${name}",
        name=name,
        wire_name=wire_name,
        type=type,
        description=description,
        coercion=coercion,
        location=location,
        explode=explode,
        required=required,
        default=default,
    )


def python_plan(
    operation: PythonOperationPlan,
    *,
    package: str = "example_api",
    client_name: str = "ExampleApi",
    response_shapes: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
) -> PythonPlan:
    """Wrap one operation in a complete renderer contract."""
    return PythonPlan(
        service_id="example#ExampleService",
        protocol_id=PROTOCOL,
        vendor="example",
        package=package,
        client_name=client_name,
        operations=(operation,),
        response_shapes=response_shapes,
        model_aliases=aliases or {},
    )


def operation_plan(
    *,
    key: str = "things",
    path: str = "/things",
    method_name: str = "get_things",
    model: str = "Thing",
    model_module: str | None = None,
    response_shape: str | None = None,
    summary: str = "Return things.",
    generate_model: bool = True,
    response_cardinality: PythonResponseCardinality = "collection",
    not_found: PythonNotFound = "raise",
    params: tuple[PythonParameterPlan, ...] = (),
    path_params: tuple[PythonParameterPlan, ...] = (),
    sorting: PythonSortingPlan | None = None,
    page_size: PythonPageSizePlan | None = None,
    pagination: PythonPaginationStyle = "none",
    page_param: str | None = None,
    results_key: str | None = None,
    package: str = "example_api",
) -> PythonOperationPlan:
    """Create a compact renderer-ready operation plan."""
    query_params = tuple(param for param in params if param.location == "query")
    header_params = tuple(param for param in params if param.location == "header")
    example_args = tuple(param.name for param in path_params if param.default.is_required)
    example_args += tuple(param.name for param in params if param.required)
    response_shape = response_shape or f"example#{model}"
    title = "".join(word.title() for word in key.split("_"))
    return PythonOperationPlan(
        shape_id=f"example#{title}",
        response_shape=response_shape,
        key=key,
        path=path,
        method_name=method_name,
        class_name=f"{title}Api",
        accessor=f"{key}_api",
        const_name=f"{key.upper()}_OPERATION",
        model=model,
        model_module=model_module or f"{package}.models.{_snake(model)}",
        summary=summary,
        docs_url=None,
        generate_model=generate_model,
        response_cardinality=response_cardinality,
        not_found=not_found,
        cost=1.0,
        pagination=pagination,
        results_key=results_key,
        page_param=page_param,
        page_start=1,
        page_step=1,
        params=params,
        query_params=query_params,
        header_params=header_params,
        path_params=path_params,
        sorting=sorting,
        page_size=page_size,
        smithy_pagination=None,
        example_args=example_args,
    )


def things_plan(*, package: str = "example_api", client_name: str = "ExampleApi") -> PythonPlan:
    """Target plan for one handwritten collection model."""
    category = parameter_plan(
        name="category",
        wire_name="category",
        type=PythonTypePlan("str"),
        description="Category to return.",
        coercion=PythonCoercionPlan("identity"),
        explode=False,
    )
    operation = operation_plan(
        params=(category,),
        generate_model=False,
        package=package,
        model_module=f"{package}.models.thing",
    )
    return python_plan(operation, package=package, client_name=client_name)


def native_weather_plan() -> PythonPlan:
    """Target plan lowered from the native weather fixture."""
    city = parameter_plan(
        name="city",
        wire_name="city",
        type=PythonTypePlan("str"),
        description="City to observe.",
        coercion=PythonCoercionPlan("identity"),
        location="path",
        required=True,
        default=PythonDefaultPlan("required"),
    )
    operation = operation_plan(
        key="weather",
        path="/weather/{city}",
        method_name="get_weather",
        model="Weather",
        summary="Return the current weather for one city.",
        response_cardinality="single",
        not_found="absent",
        path_params=(city,),
        package="native_weather_sdk",
        response_shape="native.weather#Weather",
    )
    return python_plan(
        operation,
        package="native_weather_sdk",
        client_name="NativeWeatherApi",
        response_shapes=("native.weather#Weather",),
    )


def splits_plan() -> PythonPlan:
    """Policy-rich target plan resembling a real market-data operation."""
    ticker = parameter_plan(
        name="ticker",
        wire_name="ticker",
        type=PythonTypePlan("str"),
        description="Stock symbol to return.",
        coercion=PythonCoercionPlan("identity"),
        explode=False,
    )
    execution_date = parameter_plan(
        name="execution_date_gte",
        wire_name="execution_date.gte",
        type=PythonTypePlan("date_input"),
        description="Earliest execution date.",
        coercion=PythonCoercionPlan("date"),
        explode=False,
    )
    adjustment_type = PythonTypePlan("symbol", name="AdjustmentType", module="example_api.models")
    adjustments = parameter_plan(
        name="adjustment_types",
        wire_name="adjustment_type.any_of",
        type=PythonTypePlan("list", members=(adjustment_type,)),
        description="Allowed adjustment types.",
        coercion=PythonCoercionPlan("choices", literal_type=adjustment_type),
        explode=False,
    )
    sort_field = PythonTypePlan("symbol", name="SortField", module="example_api.models")
    sort_direction = PythonTypePlan("symbol", name="SortDirection", module="example_api.models")
    sorting = PythonSortingPlan(
        style="suffix",
        separator=".",
        sort=PythonSortArgumentPlan(
            name="sort",
            wire_name="sort",
            type=sort_field,
            default=PythonDefaultPlan("value", "execution_date"),
            coercion=PythonCoercionPlan("choice", literal_type=sort_field),
            explode=False,
        ),
        order=PythonSortArgumentPlan(
            name="order",
            wire_name=None,
            type=sort_direction,
            default=PythonDefaultPlan("value", "desc"),
            coercion=PythonCoercionPlan("choice", literal_type=sort_direction),
        ),
    )
    operation = operation_plan(
        key="splits",
        path="/stocks/v1/splits",
        method_name="get_splits",
        model="Split",
        summary="Get splits, validated into `Split` models.",
        params=(ticker, execution_date, adjustments),
        sorting=sorting,
        page_size=PythonPageSizePlan("limit", 5000),
        pagination="page_number",
        page_param="page",
    )
    return python_plan(operation, response_shapes=("example#Split",))


def things_service_plan() -> ServicePlan:
    """Neutral service plan which lowers to :func:`things_plan`."""
    text = ShapePlan("smithy.api#String", "string", recursive=False)
    thing = ShapePlan("example#Thing", "structure", recursive=False)
    things = ShapePlan(
        "example#ThingList",
        "list",
        recursive=False,
        members=(_member("example#ThingList$member", "member", thing.id),),
    )
    input_shape = ShapePlan(
        "example#GetThingsInput",
        "structure",
        recursive=False,
        members=(_member("example#GetThingsInput$category", "category", text.id),),
    )
    output = ShapePlan(
        "example#GetThingsOutput",
        "structure",
        recursive=False,
        members=(_member("example#GetThingsOutput$things", "things", things.id, required=True),),
    )
    operation = OperationPlan(
        id="example#GetThings",
        input=input_shape.id,
        output=output.id,
        errors=(),
        auth_schemes=(),
        documentation="Return things.",
        http=HttpPlan(
            "GET",
            "/things",
            200,
            request_bindings=(HttpBindingPlan(input_shape.members[0].id, "query", "category"),),
            response_bindings=(HttpBindingPlan(output.members[0].id, "payload", "things"),),
        ),
        extensions={"spitzeisen.python#operation": {"module": "things", "method": "get_things"}},
    )
    return _service_plan(operation, (text, thing, things, input_shape, output))


def native_weather_service_plan() -> ServicePlan:
    """Neutral service plan emitted for the native weather fixture."""
    text = ShapePlan("smithy.api#String", "string", recursive=False)
    city = _member(
        "native.weather#GetWeatherInput$city",
        "city",
        text.id,
        required=True,
        client_nullable=False,
        documentation="City to observe.",
    )
    input_shape = ShapePlan(
        "native.weather#GetWeatherInput",
        "structure",
        recursive=False,
        members=(city,),
    )
    weather = ShapePlan("native.weather#Weather", "structure", recursive=False)
    payload = _member(
        "native.weather#GetWeatherOutput$weather",
        "weather",
        weather.id,
        required=True,
        client_nullable=False,
    )
    output = ShapePlan(
        "native.weather#GetWeatherOutput",
        "structure",
        recursive=False,
        members=(payload,),
    )
    operation = OperationPlan(
        id="native.weather#GetWeather",
        input=input_shape.id,
        output=output.id,
        errors=(),
        auth_schemes=(),
        documentation="Return the current weather for one city.",
        http=HttpPlan(
            "GET",
            "/weather/{city}",
            200,
            request_bindings=(HttpBindingPlan(city.id, "label", "city"),),
            response_bindings=(HttpBindingPlan(payload.id, "payload", "weather"),),
        ),
        extensions={"spitzeisen.python#operation": {"module": "weather", "method": "get_weather"}},
        policies={"not_found": {"behavior": "absent"}},
    )
    return _service_plan(operation, (text, input_shape, weather, output), namespace="native.weather")


def _service_plan(
    operation: OperationPlan,
    shapes: tuple[ShapePlan, ...],
    *,
    namespace: str = "example",
) -> ServicePlan:
    service_name = "ExampleService" if namespace == "example" else "WeatherService"
    service = Service(
        id=f"{namespace}#{service_name}",
        version="1.0",
        protocols=(PROTOCOL,),
        auth_schemes=(),
        operations=(operation.id,),
    )
    return ServicePlan(
        service=service,
        operations={operation.id: operation},
        shapes={shape.id: shape for shape in shapes},
    )


def _member(
    shape_id: str,
    name: str,
    target: str,
    *,
    required: bool = False,
    client_nullable: bool = True,
    documentation: str | None = None,
) -> MemberPlan:
    return MemberPlan(
        id=shape_id,
        name=name,
        target=target,
        required=required,
        client_nullable=client_nullable,
        default=DefaultValue(present=False),
        documentation=documentation,
    )


def _snake(value: str) -> str:
    """Handle fixture model names without depending on the production allocator."""
    result = ""
    for character in value:
        if result and character.isupper():
            result += "_"
        result += character.lower()
    return result
