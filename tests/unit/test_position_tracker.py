"""Position lifecycle tracker tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanterback.adapters.lifecycle.position_tracker import PositionTracker
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.i18n import I18n


def _create_order_in_store(store: SqliteStore, ticker: str = "TEST") -> int:
    """Helper to create a minimal order record in the store."""
    now = datetime.now(tz=timezone.utc)
    scan_run = ScanRun(started_at=now, source="test")
    scan_id = store.insert_scan_run(scan_run)
    decision = PersistedDecision(
        scan_run_id=scan_id, ticker=ticker,
        summary_json='{}', decision_json='{"action":"BUY"}',
        llm_model="test", created_at=now,
    )
    decision_id = store.insert_decision(decision)
    backtest = PersistedBacktest(
        decision_id=decision_id, report_json='{}', passed=True,
        created_at=now,
    )
    backtest_id = store.insert_backtest(backtest)
    order = PersistedOrder(
        decision_id=decision_id, backtest_id=backtest_id,
        bracket_spec_json='{}', submitted_at=now,
    )
    return store.insert_order(order)


@dataclass
class FakePos:
    """Fake position snapshot from broker."""
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float | None = None
    market_value: float | None = None


@dataclass
class FakeOrd:
    """Fake order snapshot from broker."""
    order_id: str
    ticker: str
    side: str
    qty: float
    filled_qty: float
    filled_avg_price: float | None
    status: str
    order_type: str
    submitted_at: datetime
    filled_at: datetime | None
    legs: list | None = None


class FakeBroker:
    """In-memory fake broker for testing."""
    def __init__(self):
        self.positions: list = []
        self.orders: list = []
        self.cancelled: list[str] = []

    def list_positions(self):
        return list(self.positions)

    def list_orders_after(self, after: datetime):
        return [o for o in self.orders if (o.filled_at or o.submitted_at) >= after]

    def list_all_orders(self, status: str | None = None, after: datetime | None = None) -> list[dict]:
        # Reconciler expects dict shape with id/status. Empty by default.
        return []

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True


class FakeNotifier:
    """In-memory fake notifier for testing."""
    def __init__(self):
        self.events: list = []

    def push(self, evt):
        """Record notification event."""
        self.events.append(evt)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    """Create a temporary in-memory SQLite store."""
    return SqliteStore(tmp_path / "tracker.sqlite")




def test_detects_new_open(store: SqliteStore, i18n_en: I18n) -> None:
    """Test detection of newly opened positions."""
    broker = FakeBroker()
    now = datetime.now(tz=timezone.utc)
    broker.positions = [FakePos(ticker="AAPL", qty=10, avg_entry_price=150.0)]
    broker.orders = [
        FakeOrd(
            order_id="o1", ticker="AAPL", side="buy", qty=10, filled_qty=10,
            filled_avg_price=150.0, status="filled", order_type="market",
            submitted_at=now - timedelta(minutes=5),
            filled_at=now - timedelta(minutes=4),
        )
    ]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    result = tracker.tick()
    assert result["opens"] == 1
    assert result["closes"] == 0
    assert len(notifier.events) == 1
    assert notifier.events[0].kind == "position.opened"


def test_detects_close_with_stop_loss(store: SqliteStore, i18n_en: I18n) -> None:
    """Test detection of position close via stop loss."""
    # Seed prior open position
    order_id = _create_order_in_store(store, "NVDA")
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    store.upsert_position(PersistedPosition(
        ticker="NVDA", order_id=order_id, qty=10, entry_price=500.0, opened_at=entry_at,
        state="bracket_active", sl=470.0, tp=560.0,
    ))

    broker = FakeBroker()
    broker.positions = []  # now empty
    exit_at = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    broker.orders = [
        FakeOrd(
            order_id="exit-1", ticker="NVDA", side="sell", qty=10, filled_qty=10,
            filled_avg_price=470.0, status="filled", order_type="stop",
            submitted_at=exit_at - timedelta(minutes=1), filled_at=exit_at,
        )
    ]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    result = tracker.tick()
    assert result["closes"] == 1
    assert len(notifier.events) == 1
    assert notifier.events[0].kind == "position.closed"

    # Trade persisted with correct exit reason and P&L
    trades = store.list_recent_trades(limit=10)
    assert len(trades) == 1
    t = trades[0]
    assert t.ticker == "NVDA"
    assert t.exit_reason == "STOP_LOSS"
    assert abs(t.pnl_usd - (-300.0)) < 0.5  # (470-500) * 10 = -300


def test_take_profit_classification(store: SqliteStore, i18n_en: I18n) -> None:
    """Test that limit orders are classified as take profit."""
    order_id = _create_order_in_store(store, "TSLA")
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=12)
    store.upsert_position(PersistedPosition(
        ticker="TSLA", order_id=order_id, qty=5, entry_price=200.0, opened_at=entry_at,
        state="bracket_active", sl=180.0, tp=230.0,
    ))
    broker = FakeBroker()
    broker.positions = []
    exit_at = datetime.now(tz=timezone.utc)
    broker.orders = [FakeOrd(
        order_id="tp-1", ticker="TSLA", side="sell", qty=5, filled_qty=5,
        filled_avg_price=230.0, status="filled", order_type="limit",
        submitted_at=exit_at - timedelta(minutes=2), filled_at=exit_at,
    )]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    tracker.tick()
    trades = store.list_recent_trades(limit=10)
    assert trades[0].exit_reason == "TAKE_PROFIT"
    assert abs(trades[0].pnl_usd - 150.0) < 0.5  # (230-200) * 5 = 150


def test_trailing_stop_classification(store: SqliteStore, i18n_en: I18n) -> None:
    """Test that trailing stop orders are classified correctly."""
    order_id = _create_order_in_store(store, "MSFT")
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    store.upsert_position(PersistedPosition(
        ticker="MSFT", order_id=order_id, qty=2, entry_price=400.0, opened_at=entry_at,
        state="bracket_active", sl=380.0, tp=430.0,
    ))
    broker = FakeBroker()
    broker.positions = []
    exit_at = datetime.now(tz=timezone.utc)
    broker.orders = [FakeOrd(
        order_id="trail-1", ticker="MSFT", side="sell", qty=2, filled_qty=2,
        filled_avg_price=410.0, status="filled", order_type="trailing_stop",
        submitted_at=exit_at - timedelta(minutes=1), filled_at=exit_at,
    )]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    tracker.tick()
    trades = store.list_recent_trades(limit=10)
    assert trades[0].exit_reason == "TRAILING_STOP"


def test_idempotent_on_rerun(store: SqliteStore, i18n_en: I18n) -> None:
    """Second tick on same broker state must not create duplicate trades."""
    order_id = _create_order_in_store(store, "MSFT")
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    store.upsert_position(PersistedPosition(
        ticker="MSFT", order_id=order_id, qty=2, entry_price=400.0, opened_at=entry_at,
        state="bracket_active", sl=380.0, tp=430.0,
    ))
    broker = FakeBroker()
    exit_at = datetime.now(tz=timezone.utc)
    broker.orders = [FakeOrd(
        order_id="dup-1", ticker="MSFT", side="sell", qty=2, filled_qty=2,
        filled_avg_price=430.0, status="filled", order_type="limit",
        submitted_at=exit_at - timedelta(minutes=1), filled_at=exit_at,
    )]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    tracker.tick()
    tracker.tick()  # second tick on same state
    trades = store.list_recent_trades(limit=10)
    assert len(trades) == 1  # no duplicate


def test_pnl_calculation(store: SqliteStore, i18n_en: I18n) -> None:
    """Test P&L calculation in different scenarios."""
    # Winning trade
    order_id = _create_order_in_store(store, "GOOG")
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    store.upsert_position(PersistedPosition(
        ticker="GOOG", order_id=order_id, qty=10, entry_price=100.0, opened_at=entry_at,
        state="bracket_active", sl=90.0, tp=120.0,
    ))
    broker = FakeBroker()
    broker.positions = []
    exit_at = datetime.now(tz=timezone.utc)
    broker.orders = [FakeOrd(
        order_id="win-1", ticker="GOOG", side="sell", qty=10, filled_qty=10,
        filled_avg_price=120.0, status="filled", order_type="limit",
        submitted_at=exit_at - timedelta(minutes=1), filled_at=exit_at,
    )]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    tracker.tick()
    trades = store.list_recent_trades(limit=10)
    assert trades[0].pnl_usd == pytest.approx(200.0, abs=0.5)  # (120-100)*10
    assert trades[0].pnl_pct == pytest.approx(20.0, abs=0.1)  # ((120-100)/100)*100


def test_trade_carries_decision_id(store: SqliteStore, i18n_en: I18n) -> None:
    """When position has decision_id, the resulting Trade should too."""
    entry_at = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    order_id = _create_order_in_store(store, "NVDA")
    store.upsert_position(PersistedPosition(
        ticker="NVDA", order_id=order_id, qty=10, entry_price=500.0, opened_at=entry_at,
        state="bracket_active", sl=470.0, tp=560.0,
        decision_id=42,
    ))
    broker = FakeBroker()
    broker.positions = []
    exit_at = datetime.now(tz=timezone.utc)
    broker.orders = [FakeOrd(
        order_id="exit-1", ticker="NVDA", side="sell", qty=10, filled_qty=10,
        filled_avg_price=560.0, status="filled", order_type="limit",
        submitted_at=exit_at - timedelta(minutes=1), filled_at=exit_at,
    )]
    notifier = FakeNotifier()
    tracker = PositionTracker(broker=broker, store=store, notifier=notifier, i18n=i18n_en)
    tracker.tick()
    trades = store.list_recent_trades(limit=10)
    assert len(trades) == 1
    assert trades[0].decision_id == 42
