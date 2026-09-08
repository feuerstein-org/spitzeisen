"""Vendor value sets; the runtime only knows how to validate and serialize them."""

from typing import Literal

Market = Literal["stocks", "crypto", "fx", "otc", "indices"]
Order = Literal["asc", "desc"]
TickerSort = Literal["ticker", "name", "market"]
SplitSort = Literal["ticker", "execution_date"]
AdjustmentType = Literal["forward_split", "reverse_split", "stock_dividend"]
