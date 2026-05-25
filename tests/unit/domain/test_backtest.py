from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quanterback.domain.backtest import BacktestReport, BacktestRequest, TradeRecord


def test_backtest_request_defaults_lookback() -> None:
    r = BacktestRequest(ticker="AAPL", strategy="MOMENTUM",
                        params={"lookback_days": 20, "momentum_threshold": 0.05})
    assert r.lookback_years == 3


def test_trade_record_exit_reason_strict() -> None:
    with pytest.raises(ValidationError):
        TradeRecord(
            entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 10),
            entry_price=100, exit_price=110, return_pct=0.10,
            bars_held=10, exit_reason="i_changed_my_mind",
        )


def test_backtest_report_holds_trades() -> None:
    r = BacktestReport(
        ticker="AAPL", strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=42, win_rate=0.5, max_drawdown=0.06, sharpe=0.8,
        profit_factor=1.5, cumulative_return=0.20, avg_trade_return=0.005,
        avg_bars_held=7.5, trades=[],
    )
    assert r.num_trades == 42
