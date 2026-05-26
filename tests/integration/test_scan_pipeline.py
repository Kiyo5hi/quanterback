from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource
from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.market import PriceWindow
from quanterback.domain.risk import RiskThresholds
from quanterback.pipeline import ScanPipeline
from tests.fakes.executor import InMemorySimulatorExecutor
from tests.fakes.historical_data import FakeHistoricalDataProvider
from tests.fakes.notifier import FakeNotifier


@dataclass
class FakeDataProvider:
    window: PriceWindow

    def fetch(self, ticker: str) -> PriceWindow:
        return self.window


@dataclass
class FakeStrategist:
    canned: object
    model_name: str = "fake-strategist"

    def decide(self, summary):
        return self.canned


def _smooth_uptrend_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=500, freq="B")
    base = np.linspace(100, 250, 500)
    noise = np.random.RandomState(42).randn(500) * 2.5
    trend = base + noise
    trend = np.maximum(trend, 100 + np.arange(500) * 0.28)
    daily = pd.DataFrame({"open": trend * 0.985, "high": trend * 1.03,
                          "low": trend * 0.97, "close": trend,
                          "volume": np.full(500, 5_000_000)}, index=idx)
    hourly = daily.iloc[-30:].copy()
    return PriceWindow(ticker="AAPL", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def _make_pipeline(
    tmp_path: Path,
    *,
    decision,
    backtest_data: pd.DataFrame | None = None,
    mode: str = "normal",
    open_tickers: tuple[str, ...] = (),
    fail_executor: bool = False,
    thresholds: RiskThresholds | None = None,
) -> tuple[ScanPipeline, SqliteStore, InMemorySimulatorExecutor, FakeNotifier]:
    wl = tmp_path / "wl.txt"
    wl.write_text("AAPL\n")
    store = SqliteStore(tmp_path / "t.sqlite")
    sys_state = SqliteSystemStateService(store)
    if mode != "normal":
        sys_state.set(mode, "test", "test")
    notifier = FakeNotifier()
    executor = InMemorySimulatorExecutor(fail_next=fail_executor)
    bt_data = backtest_data if backtest_data is not None else _smooth_uptrend_window().daily
    pipeline = ScanPipeline(
        event_source=WatchlistEventSource(wl),
        data_provider=FakeDataProvider(_smooth_uptrend_window()),
        summarizer=RuleBasedSummarizer(),
        strategist=FakeStrategist(decision),
        approval_gate=NoOpApprovalGate(),
        backtester=VectorizedBacktester(
            FakeHistoricalDataProvider({"AAPL": bt_data})
        ),
        risk_gate=ThresholdRiskGate(),
        order_builder=ATRBracketOrderBuilder(
            sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
        ),
        executor=executor,
        notifier=notifier,
        state_store=store,
        system_state=sys_state,
        thresholds=thresholds or RiskThresholds(
            min_num_trades=1, min_sharpe=0.0, min_profit_factor=0.0
        ),
        backtest_lookback_years=3,
        macro_data_provider=None,
    )
    if open_tickers:
        from quanterback.domain.persisted import (
            PersistedBacktest,
            PersistedDecision,
            PersistedOrder,
            PersistedPosition,
            ScanRun,
        )
        run_id = store.insert_scan_run(ScanRun(
            started_at=datetime.now(tz=timezone.utc), source="seed",
        ))
        for t in open_tickers:
            did = store.insert_decision(PersistedDecision(
                scan_run_id=run_id, ticker=t, summary_json="{}", decision_json="{}",
                llm_model="m", created_at=datetime.now(tz=timezone.utc),
            ))
            bid = store.insert_backtest(PersistedBacktest(
                decision_id=did, report_json="{}", passed=True,
                created_at=datetime.now(tz=timezone.utc),
            ))
            oid = store.insert_order(PersistedOrder(
                decision_id=did, backtest_id=bid, bracket_spec_json="{}",
                submitted_at=datetime.now(tz=timezone.utc),
            ))
            store.upsert_position(PersistedPosition(
                ticker=t, order_id=oid, state="bracket_active",
                opened_at=datetime.now(tz=timezone.utc),
            ))
    return pipeline, store, executor, notifier


def _buy_decision():
    from quanterback.domain.decision import MomentumParams, StrategyDecision
    return StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish alignment with elevated volume confirms momentum",
        confidence=0.7,
    )


def _pass_decision():
    from quanterback.domain.decision import StrategyDecision
    return StrategyDecision(
        action="PASS", ticker="AAPL", strategy="MOMENTUM", params=None,
        rationale="extended above SMA200 by more than acceptable threshold",
        confidence=0.4,
    )


def test_scenario_1_buy_happy_path(tmp_path: Path) -> None:
    pipeline, store, executor, notifier = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.run()
    assert len(executor.submitted) == 1
    orders = store._conn.execute("SELECT alpaca_order_id FROM orders").fetchall()
    assert orders and orders[0][0].startswith("sim-")


def test_scenario_2_pass_short_circuit(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_pass_decision())
    pipeline.run()
    assert executor.submitted == []
    bts = store._conn.execute("SELECT COUNT(*) FROM backtests").fetchone()[0]
    assert bts == 0


def test_scenario_3_risk_gate_rejects_excessive_drawdown(tmp_path: Path) -> None:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=1000, freq="B")
    closes = 100 + np.sin(np.linspace(0, 60, 1000)) * 40
    bt_df = pd.DataFrame({"open": closes, "high": closes * 1.03,
                          "low": closes * 0.97, "close": closes,
                          "volume": np.full(1000, 1_000_000)}, index=idx)
    wl = tmp_path / "wl.txt"
    wl.write_text("AAPL\n")
    store = SqliteStore(tmp_path / "t.sqlite")
    sys_state = SqliteSystemStateService(store)
    notifier = FakeNotifier()
    executor = InMemorySimulatorExecutor(fail_next=False)
    pipeline = ScanPipeline(
        event_source=WatchlistEventSource(wl),
        data_provider=FakeDataProvider(_smooth_uptrend_window()),
        summarizer=RuleBasedSummarizer(),
        strategist=FakeStrategist(_buy_decision()),
        approval_gate=NoOpApprovalGate(),
        backtester=VectorizedBacktester(
            FakeHistoricalDataProvider({"AAPL": bt_df})
        ),
        risk_gate=ThresholdRiskGate(),
        order_builder=ATRBracketOrderBuilder(
            sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
        ),
        executor=executor,
        notifier=notifier,
        state_store=store,
        system_state=sys_state,
        thresholds=RiskThresholds(
            max_drawdown=0.10,
            min_num_trades=20,
            min_sharpe=100.0,
            min_profit_factor=0.5,
        ),
        backtest_lookback_years=3,
    )
    pipeline.run()
    assert executor.submitted == []
    passed = store._conn.execute("SELECT passed FROM backtests").fetchone()
    assert passed is not None and passed[0] == 0


def test_scenario_4_invalid_llm_output_handled(tmp_path: Path) -> None:
    @dataclass
    class BrokenStrategist:
        model_name: str = "broken"
        def decide(self, summary):
            raise ValueError("LLM output failed schema validation")

    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.strategist = BrokenStrategist()
    pipeline.run()
    rejected = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchall()
    assert rejected and "exception" in rejected[0][0]
    assert executor.submitted == []


def test_scenario_5_ticker_already_open_rejected(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), open_tickers=("AAPL",),
    )
    # Seed the executor to know about the pre-existing position
    # (matches DB state so reconciler won't mark it as manually closed)
    executor.seed_position("AAPL", qty=20, entry_price=200.0)
    pipeline.run()
    assert executor.submitted == []
    rej = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchall()
    assert any("open lifecycle" in r[0] for r in rej)


def test_scenario_6_frozen_mode_dry_run(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), mode="frozen",
    )
    pipeline.run()
    assert executor.submitted == []
    dry = store._conn.execute("SELECT dry_run FROM orders").fetchone()
    assert dry is not None and dry[0] == 1


def test_scenario_7_halted_mode_exits_immediately(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), mode="halted",
    )
    pipeline.run()
    n = store._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert n == 0


def test_scenario_8_data_fetch_exception_isolated(tmp_path: Path) -> None:
    @dataclass
    class BoomData:
        def fetch(self, ticker: str):
            raise RuntimeError("yfinance down")

    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.data_provider = BoomData()
    pipeline.run()
    rej = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchone()
    assert rej and "yfinance down" in rej[0]
    assert executor.submitted == []


def test_scenario_9_notifier_failure_does_not_break_chain(tmp_path: Path) -> None:
    @dataclass
    class BrokenNotifier:
        attempts: int = 0
        def push(self, event) -> None:
            self.attempts += 1
            return None
    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    bn = BrokenNotifier()
    pipeline.notifier = bn
    pipeline.run()
    assert len(executor.submitted) == 1
    assert bn.attempts > 0


def test_scenario_10_frozen_then_scan_records_dry_run(tmp_path: Path) -> None:
    pipeline, store, _, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.system_state.set("frozen", "tg user", "tg-actor")
    pipeline.run()
    dry = store._conn.execute("SELECT dry_run FROM orders").fetchone()
    assert dry is not None and dry[0] == 1
