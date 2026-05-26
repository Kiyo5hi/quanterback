from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


@dataclass(frozen=False)
class _PositionSnapshot:
    """Concrete position snapshot from fake executor."""
    ticker: str
    qty: float = 1.0
    avg_entry_price: float = 100.0
    current_price: float | None = None
    market_value: float | None = None


@dataclass
class InMemorySimulatorExecutor:
    """Records every spec received. Returns a synthetic order id."""

    account_value: float = 100_000.0
    submitted: list[BracketOrderSpec] = field(default_factory=list)
    next_id: int = 1
    fail_next: bool = False
    day_trade_count: int = 0
    _open_positions: dict[str, _PositionSnapshot] = field(default_factory=dict)

    def submit(self, spec: BracketOrderSpec, *, dry_run: bool, decision_id: int | None = None) -> ExecutionResult:
        """Submit an order. decision_id is accepted but ignored in simulator."""
        if self.fail_next:
            self.fail_next = False
            return ExecutionResult(submitted=False, order_id=None,
                                   error="simulated failure", raw_response={})
        if dry_run:
            return ExecutionResult(submitted=False, order_id=None, error=None,
                                   raw_response={"dry_run": True})
        self.submitted.append(spec)
        oid = f"sim-{self.next_id}"
        self.next_id += 1
        # Track this as an open position in the fake executor
        self._open_positions[spec.ticker] = _PositionSnapshot(
            ticker=spec.ticker, qty=spec.qty, avg_entry_price=100.0
        )
        return ExecutionResult(submitted=True, order_id=oid, error=None,
                               raw_response={"id": oid})

    def get_account_value(self) -> float:
        return self.account_value

    def get_day_trade_count(self) -> int:
        return self.day_trade_count

    def is_market_open(self) -> bool:
        """Mock: always assume market is open."""
        return True

    def next_market_open(self) -> datetime | None:
        """Mock: return a time in the future."""
        return datetime.now(tz=timezone.utc)

    def list_positions(self) -> list:
        """Return currently open positions tracked by this executor."""
        return list(self._open_positions.values())

    def list_orders_after(self, after: datetime) -> list:
        """Mock: return empty list (no historical orders in simulator)."""
        return []

    def list_all_orders(self, status: str | None = None, after: datetime | None = None) -> list[dict]:
        """Mock: return empty list (no orders in simulator)."""
        return []

    def cancel_order(self, order_id: str) -> bool:
        """Mock: always succeed."""
        return True

    def seed_position(self, ticker: str, qty: float = 1.0, entry_price: float = 100.0) -> None:
        """Seed a position (for testing scenarios with pre-existing positions)."""
        self._open_positions[ticker] = _PositionSnapshot(
            ticker=ticker, qty=qty, avg_entry_price=entry_price
        )
