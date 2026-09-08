"""A second endpoint group with different vendor query conventions."""

from collections.abc import Collection
from datetime import date, datetime

from handwritten_sdk.models import Split
from handwritten_sdk.params import AdjustmentType, Order, SplitSort
from spitzeisen import (
    AsyncSpitzeisenApi,
    CursorPagination,
    SpitzeisenOperationSpec,
    ValidationMode,
    coerce_choice,
    coerce_choices,
    coerce_date,
    coerce_sort,
    resolve_page_size,
    serialize_query_map,
    serialize_query_param,
)

SPLITS = SpitzeisenOperationSpec(
    path="/stocks/v1/splits",
    pagination=CursorPagination(next_key="next_url", results_key="results", cursor_from_url=True),
)
SPLITS_MAX_PAGE_SIZE = 5000


class AsyncSplitsApi(AsyncSpitzeisenApi):
    """Compound sort and comma-separated filters belong to the vendor adapter."""

    async def get_splits(
        self,
        *,
        ticker: str | None = None,
        execution_date_gte: str | date | datetime | None = None,
        adjustment_types: Collection[AdjustmentType] | None = None,
        max_results: int | None = None,
        sort: SplitSort = "execution_date",
        order: Order = "desc",
        on_validation_error: ValidationMode | None = None,
    ) -> list[Split]:
        """Collect validated splits with this endpoint's page size and filter bindings."""
        params = serialize_query_map(
            {
                "ticker": ticker,
                "execution_date.gte": coerce_date(execution_date_gte, "execution_date_gte"),
                "sort": coerce_sort(coerce_choice(sort, SplitSort, "sort"), coerce_choice(order, Order, "order")),
                "limit": resolve_page_size(max_results, SPLITS_MAX_PAGE_SIZE),
            },
        )
        params += serialize_query_param(
            coerce_choices(adjustment_types, AdjustmentType, "adjustment_types"),
            name="adjustment_type.any_of",
            explode=False,
        )
        return await self.get_models(
            SPLITS,
            Split,
            params=params,
            max_results=max_results,
            on_validation_error=on_validation_error,
        )
