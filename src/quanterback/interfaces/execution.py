from __future__ import annotations

from datetime import datetime
from typing import Protocol

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


class Executor(Protocol):
    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult: ...

    def get_account_value(self) -> float: ...

    def get_day_trade_count(self) -> int:
        """Day trades executed in the last 5 business days (PDT-relevant)."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order by ID. Returns True if successful."""
        ...

    def market_close(self, ticker: str, qty: float | None = None) -> bool:
        """Close a position with a market sell order. Returns True if successful."""
        ...

    def replace_stop_loss(self, ticker: str, new_sl_price: float) -> bool:
        """Replace a bracket's stop_loss leg with a new stop price. Returns True if successful."""
        ...

    def is_market_open(self) -> bool:
        """Check if Alpaca market is currently open (respects holidays, early closes)."""
        ...

    def next_market_open(self) -> datetime | None:
        """Get the next market open time. Returns None if unavailable."""
        ...
