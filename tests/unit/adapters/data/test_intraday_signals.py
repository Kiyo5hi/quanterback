from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.domain.market import PriceWindow


def _build_window(
    hourly_returns: list[float],
    daily_returns: list[float] | None = None,
) -> PriceWindow:
    # Daily — 300 days for indicators
    daily_returns = daily_returns or [0.003] * 300
    daily_idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                              periods=len(daily_returns), freq="B")
    daily_closes = [100.0]
    for r in daily_returns:
        daily_closes.append(daily_closes[-1] * (1 + r))
    daily_closes = daily_closes[:len(daily_idx)]
    daily = pd.DataFrame({
        "open": daily_closes, "high": [c * 1.01 for c in daily_closes],
        "low": [c * 0.99 for c in daily_closes], "close": daily_closes,
        "volume": [1_000_000] * len(daily_closes),
    }, index=daily_idx)

    # Hourly — generated returns
    hourly_idx = pd.date_range(
        end=datetime(2026, 5, 22, 16, tzinfo=timezone.utc),
        periods=len(hourly_returns), freq="h",
    )
    base_price = daily_closes[-1] * 0.99
    h_closes = []
    last_price = base_price
    for r in hourly_returns:
        last_price = last_price * (1 + r)
        h_closes.append(last_price)
    hourly = pd.DataFrame({
        "open": h_closes, "high": [c * 1.005 for c in h_closes],
        "low": [c * 0.995 for c in h_closes], "close": h_closes,
        "volume": [100_000] * len(h_closes),
    }, index=hourly_idx)

    return PriceWindow(ticker="X", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def test_intraday_signals_populated() -> None:
    pw = _build_window([0.005, 0.003, 0.002, 0.001, 0.004, 0.002, 0.003])
    s = RuleBasedSummarizer().summarize(pw)
    assert s.intraday.consecutive_up_hours >= 1
    assert s.intraday.return_today_pct >= 0


def test_intraday_streak_resets_on_down_bar() -> None:
    pw = _build_window([0.01, 0.01, 0.01, -0.01, 0.01, 0.01])
    s = RuleBasedSummarizer().summarize(pw)
    # last 2 candles up after the down → streak should be 2
    assert s.intraday.consecutive_up_hours == 2


def test_pct_from_intraday_high_zero_or_negative() -> None:
    pw = _build_window([0.005] * 7)
    s = RuleBasedSummarizer().summarize(pw)
    assert s.intraday.pct_from_intraday_high <= 0.0


def test_handles_insufficient_hourly_data() -> None:
    pw = _build_window([0.01])   # only 1 bar
    s = RuleBasedSummarizer().summarize(pw)
    # All fields should still construct without raising
    assert s.intraday.consecutive_up_hours == 0
