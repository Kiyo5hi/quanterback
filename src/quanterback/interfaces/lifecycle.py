"""Ports for position lifecycle tracking."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class PositionSnapshot(Protocol):
    """Alpaca position snapshot."""
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float | None
    market_value: float | None


class OrderSnapshot(Protocol):
    """Alpaca order snapshot."""
    order_id: str
    ticker: str
    side: str            # "buy" / "sell"
    qty: float
    filled_qty: float
    filled_avg_price: float | None
    status: str          # alpaca order status
    order_type: str      # "market" / "limit" / "stop" / "stop_limit" / "trailing_stop"
    submitted_at: datetime
    filled_at: datetime | None
    legs: list[dict] | None  # bracket legs metadata


class BrokerLifecyclePort(Protocol):
    """Broker port for position lifecycle tracking."""
    def list_positions(self) -> list[PositionSnapshot]:
        """Get all open positions from broker."""
        ...

    def list_orders_after(self, after: datetime) -> list[OrderSnapshot]:
        """Get closed orders since a given timestamp."""
        ...
