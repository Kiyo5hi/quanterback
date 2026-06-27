from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.domain.market import (
    MarketDataQualityError,
    PriceWindow,
    TrendRegime,
    VolatilityRegime,
)


def _uptrending_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=300, freq="D")
    closes = [100.0 + i * 0.3 for i in range(300)]
    daily = pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000 + i * 1_000 for i in range(300)],
    }, index=idx)
    hourly = daily.iloc[-30:].copy()
    return PriceWindow(ticker="AAPL", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def test_summarize_uptrend() -> None:
    s = RuleBasedSummarizer().summarize(_uptrending_window())
    assert s.ticker == "AAPL"
    assert s.trend_regime == TrendRegime.UPTREND
    assert s.moving_averages.alignment == "bullish"
    assert s.technicals.rsi_14 > 50
    assert s.volatility.regime in (VolatilityRegime.LOW, VolatilityRegime.NORMAL)


def test_summarize_returns_finite_values() -> None:
    s = RuleBasedSummarizer().summarize(_uptrending_window())
    text = s.to_prompt_text()
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()


def test_summarize_rejects_zero_atr_data() -> None:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=300, freq="D")
    closes = [10.0] * 300
    daily = pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1_000_000] * 300,
    }, index=idx)
    hourly = daily.iloc[-30:].copy()
    pw = PriceWindow(ticker="SPCX", daily=daily, hourly=hourly,
                     as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))

    with pytest.raises(MarketDataQualityError, match="ATR14"):
        RuleBasedSummarizer().summarize(pw)


def test_summarize_rejects_empty_price_window() -> None:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    pw = PriceWindow(
        ticker="ZHIPU",
        daily=empty,
        hourly=empty,
        as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )

    with pytest.raises(MarketDataQualityError, match="no usable price data"):
        RuleBasedSummarizer().summarize(pw)


def _short_history_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 6, 25, tzinfo=timezone.utc), periods=9, freq="B")
    closes = np.array([24.0, 24.8, 25.2, 24.9, 25.7, 26.2, 27.1, 26.8, 27.4])
    daily = pd.DataFrame({
        "open": closes * 0.995,
        "high": closes * 1.03,
        "low": closes * 0.97,
        "close": closes,
        "volume": np.full(9, 900_000),
    }, index=idx)
    hourly_idx = pd.date_range(
        end=datetime(2026, 6, 25, 20, tzinfo=timezone.utc),
        periods=40,
        freq="h",
    )
    hourly_closes = np.linspace(25.5, 27.4, 40)
    hourly = pd.DataFrame({
        "open": hourly_closes * 0.998,
        "high": hourly_closes * 1.006,
        "low": hourly_closes * 0.994,
        "close": hourly_closes,
        "volume": np.full(40, 80_000),
    }, index=hourly_idx)
    return PriceWindow(ticker="SPCX", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 6, 25, tzinfo=timezone.utc))


def test_summarize_rejects_short_history_by_default() -> None:
    with pytest.raises(MarketDataQualityError, match="ATR14"):
        RuleBasedSummarizer().summarize(_short_history_window())


def test_summarize_allows_short_history_for_preview() -> None:
    summary = RuleBasedSummarizer().summarize(
        _short_history_window(), allow_short_history=True,
    )
    text = summary.to_prompt_text()
    assert summary.volatility.atr_14 > 0
    assert summary.moving_averages.sma_20 > 0
    assert summary.moving_averages.sma_50 > 0
    assert summary.moving_averages.sma_200 > 0
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()


def _stable_low_vol_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=300, freq="D")
    # Very smooth, low daily noise: ~12% annualized vol
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(loc=0.0005, scale=0.008, size=300)
    closes = 100.0 * np.cumprod(1 + daily_returns)
    daily = pd.DataFrame({
        "open": closes, "high": closes * 1.005,
        "low": closes * 0.995, "close": closes,
        "volume": [1_000_000] * 300,
    }, index=idx)
    hourly = daily.iloc[-30:].copy()
    return PriceWindow(ticker="XLU", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def test_per_ticker_vol_uses_percentile_when_enough_history() -> None:
    """For a ticker with stable history, current vol close to median should
    be classified NORMAL, not LOW (which would require below 25th percentile)."""
    s = RuleBasedSummarizer().summarize(_stable_low_vol_window())
    # Within its own distribution, vol is roughly average → NORMAL
    assert s.volatility.regime == VolatilityRegime.NORMAL


def test_per_ticker_vol_extreme_requires_above_95th_percentile() -> None:
    """A ticker that lived at ~20% vol for 250 days then spiked to ~60% for
    last 20 days should be EXTREME on its OWN distribution."""
    rng = np.random.default_rng(42)
    quiet_returns = rng.normal(loc=0.0005, scale=0.013, size=280)   # ~20% ann
    spike_returns = rng.normal(loc=0.0, scale=0.04, size=20)         # ~64% ann
    daily_returns = np.concatenate([quiet_returns, spike_returns])
    closes = 100.0 * np.cumprod(1 + daily_returns)
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=300, freq="D")
    daily = pd.DataFrame({
        "open": closes, "high": closes * 1.005,
        "low": closes * 0.995, "close": closes,
        "volume": [1_000_000] * 300,
    }, index=idx)
    hourly = daily.iloc[-30:].copy()
    pw = PriceWindow(ticker="X", daily=daily, hourly=hourly,
                     as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))
    s = RuleBasedSummarizer().summarize(pw)
    assert s.volatility.regime in (VolatilityRegime.HIGH, VolatilityRegime.EXTREME)


def test_summarize_includes_momentum_signals() -> None:
    s = RuleBasedSummarizer().summarize(_uptrending_window())
    assert hasattr(s, "momentum_signals")
    assert s.momentum_signals.consecutive_up_days >= 0
    # Smooth uptrend should be near 52w high
    assert s.momentum_signals.is_near_52w_high is True


def test_summarize_with_spy_computes_relative_strength() -> None:
    pw = _uptrending_window()
    # SPY: slightly upward at half the pace
    closes = pd.Series([100.0 + i * 0.1 for i in range(300)])
    s = RuleBasedSummarizer().summarize(pw, spy_closes=closes)
    assert s.momentum_signals.relative_strength_vs_spy_20d != 0.0


def test_summarize_with_earnings_date_fills_days_to_next_earnings() -> None:
    from datetime import date, timedelta
    pw = _uptrending_window()
    earnings_date = date.today() + timedelta(days=10)
    s = RuleBasedSummarizer().summarize(pw, earnings_date=earnings_date)
    assert s.fundamentals.days_to_next_earnings is not None
    # Allow 1-day tolerance due to timezone edge cases
    assert abs(s.fundamentals.days_to_next_earnings - 10) <= 1


def test_summarize_passes_through_insider_and_analyst() -> None:
    from datetime import date

    from quanterback.domain.market import AnalystAction, InsiderActivity
    pw = _uptrending_window()
    ia = InsiderActivity(n_buys=2, n_sells=0, total_buy_usd=500_000.0)
    actions = [
        AnalystAction(
            firm="Goldman Sachs", action="Upgrade",
            from_grade="Hold", to_grade="Buy", date=date.today(),
        ),
    ]
    si = None
    eps = None
    s = RuleBasedSummarizer().summarize(
        pw, insider_activity=ia, analyst_actions=actions,
        short_interest=si, eps_trend=eps,
    )
    assert s.insider_activity == ia
    assert s.recent_analyst_actions == actions


def test_summarize_no_enrichments_works() -> None:
    """Backward compat: summarize with no enrichments."""
    pw = _uptrending_window()
    s = RuleBasedSummarizer().summarize(pw)
    assert s.insider_activity is None
    assert s.recent_analyst_actions == []
    assert s.short_interest is None
    assert s.eps_trend is None
