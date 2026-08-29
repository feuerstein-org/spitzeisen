"""Small renderer plans independent of Smithy semantic compilation."""

from __future__ import annotations

from spitzeisen.codegen.plan import (
    ClientPlan,
    NotFound,
    OperationPlan,
    PageSizePlan,
    PaginationStyle,
    ParamPlan,
    Shape,
    SortArgumentPlan,
    SortingPlan,
)


def client_plan(
    operation: OperationPlan,
    *,
    package: str = "example_api",
    client_name: str = "ExampleApi",
    response_shapes: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
    type_overrides: dict[str, str] | None = None,
) -> ClientPlan:
    """Wrap one operation in a complete renderer contract."""
    return ClientPlan(
        service_id="example#ExampleService",
        vendor="example",
        package=package,
        client_name=client_name,
        operations=(operation,),
        response_shapes=response_shapes,
        model_aliases=aliases or {},
        model_type_overrides=type_overrides or {},
    )


def operation_plan(
    *,
    key: str = "things",
    path: str = "/things",
    method_name: str = "get_things",
    model: str = "Thing",
    summary: str = "Return things.",
    generate_model: bool = True,
    shape: Shape = "collection",
    not_found: NotFound = "raise",
    params: tuple[ParamPlan, ...] = (),
    path_params: tuple[ParamPlan, ...] = (),
    sorting: SortingPlan | None = None,
    page_size: PageSizePlan | None = None,
    pagination: PaginationStyle = "none",
    page_param: str | None = None,
    results_key: str | None = None,
    helpers: tuple[str, ...] = ("NoPagination", "build_header_params", "serialize_query_param"),
    model_imports: tuple[str, ...] = (),
    coerce_function_imports: tuple[str, ...] = (),
) -> OperationPlan:
    """Create a compact operation plan for renderer and CLI tests."""
    query_params = tuple(param for param in params if param.location == "query")
    header_params = tuple(param for param in params if param.location == "header")
    example_args = tuple(param.name for param in path_params if param.client_default is None)
    example_args += tuple(param.name for param in params if param.required)
    return OperationPlan(
        key=key,
        path=path,
        method_name=method_name,
        model=model,
        summary=summary,
        docs_url=None,
        generate_model=generate_model,
        shape=shape,
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
        example_args=example_args,
        model_imports=model_imports,
        coerce_function_imports=coerce_function_imports,
        helpers=helpers,
    )


def things_client(*, package: str = "example_api", client_name: str = "ExampleApi") -> ClientPlan:
    """Plan for one handwritten collection model."""
    category = ParamPlan(
        name="category",
        wire_name="category",
        annotation="str",
        description="Category to return.",
        coercion="category",
        explode=False,
    )
    operation = operation_plan(params=(category,), generate_model=False)
    return client_plan(operation, package=package, client_name=client_name)


def native_weather_client() -> ClientPlan:
    """Plan emitted for the native weather fixture."""
    city = ParamPlan(
        name="city",
        wire_name="city",
        annotation="str",
        description="City to observe.",
        coercion="city",
        required=True,
        client_default=None,
    )
    operation = operation_plan(
        key="weather",
        path="/weather/{city}",
        method_name="get_weather",
        model="Weather",
        summary="Return the current weather for one city.",
        shape="single",
        not_found="empty",
        path_params=(city,),
        helpers=("NoPagination", "build_header_params", "require_value", "serialize_query_param"),
    )
    return client_plan(
        operation,
        package="native_weather_sdk",
        client_name="NativeWeatherApi",
        response_shapes=("native.weather#Weather",),
    )


def splits_client() -> ClientPlan:
    """Policy-rich renderer plan resembling a real market-data operation."""
    ticker = ParamPlan(
        name="ticker",
        wire_name="ticker",
        annotation="str",
        description="Stock symbol to return.",
        coercion="ticker",
        explode=False,
    )
    execution_date = ParamPlan(
        name="execution_date_gte",
        wire_name="execution_date.gte",
        annotation="str | date | datetime",
        description="Earliest execution date.",
        coercion='coerce_date(execution_date_gte, "execution_date_gte")',
        explode=False,
    )
    adjustments = ParamPlan(
        name="adjustment_types",
        wire_name="adjustment_type.any_of",
        annotation="list[AdjustmentType]",
        description="Allowed adjustment types.",
        coercion='coerce_choices(adjustment_types, AdjustmentType, "adjustment_types")',
        explode=False,
    )
    sorting = SortingPlan(
        style="suffix",
        sort=SortArgumentPlan(
            wire_name="sort",
            annotation="SortField",
            default="execution_date",
            coercion='coerce_choice(sort, SortField, "sort")',
            explode=False,
        ),
        order=SortArgumentPlan(
            wire_name=None,
            annotation="SortDirection",
            default="desc",
            coercion='coerce_choice(order, SortDirection, "order")',
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
        page_size=PageSizePlan("limit", 5000),
        pagination="page_number",
        page_param="page",
        helpers=(
            "NoPagination",
            "build_header_params",
            "coerce_choice",
            "coerce_choices",
            "coerce_date",
            "coerce_sort",
            "serialize_query_param",
        ),
        model_imports=("AdjustmentType", "SortDirection", "SortField"),
    )
    return client_plan(operation, response_shapes=("example#Split",))
