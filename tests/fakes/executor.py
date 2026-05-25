from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


@dataclass
class InMemorySimulatorExecutor:
    """Records every spec received. Returns a synthetic order id."""

    account_value: float = 100_000.0
    submitted: list[BracketOrderSpec] = field(default_factory=list)
    next_id: int = 1
    fail_next: bool = False
    day_trade_count: int = 0

    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult:
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
