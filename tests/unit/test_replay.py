"""Replay engine tests with fakes — no real LLM, no real yfinance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    IntradaySignals,
    MomentumSignals,
    MovingAverages,
    PriceSnapshot,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)
from quanterback.i18n import I18n
from quanterback.replay import (
    ReplayConfig,
    ReplayEngine,
    generate_replay_report,
)


def _make_df(n: int = 300) -> pd.DataFrame:
    """Generate an upward-trending OHLCV DataFrame."""
    base = 100.0
    ts = pd.date_range(start="2026-01-01", periods=n, freq="D")
    closes = [base + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "open": [c - 0.2 for c in closes],
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    }, index=ts)


@dataclass
class FakeDataProvider:
    series: dict

    def fetch_historical(self, ticker: str, years: int = 5):
        return self.series.get(ticker)


@dataclass
class FakeSummarizer:
    def summarize(self, window, spy_closes=None, news=None):
        return CondensedSummary(
            ticker=window.ticker,
            as_of=window.as_of,
            price=PriceSnapshot(
                last_close=100.0, return_1d=0.01, return_5d=0.02,
                return_20d=0.05, return_60d=0.10,
                pct_from_52w_high=-0.05, pct_from_52w_low=0.20,
            ),
            moving_averages=MovingAverages(
                sma_20=99.0, sma_50=98.0, sma_200=97.0,
                pct_above_sma_20=0.01, pct_above_sma_50=0.02,
                pct_above_sma_200=0.03, alignment="bullish",
            ),
            volatility=VolatilityProfile(
                realized_vol_20d_annualized=0.25,
                atr_14=2.0, atr_pct_of_price=0.02,
                regime=VolatilityRegime.NORMAL,
            ),
            volume=VolumeProfile(
                last_volume=1_000_000, avg_volume_20d=900_000,
                volume_ratio=1.1, regime=VolumeRegime.NORMAL,
            ),
            technicals=TechnicalIndicators(rsi_14=55.0, macd_signal="none"),
            fundamentals=FundamentalLite(
                days_to_next_earnings=None, market_cap_bucket="large",
            ),
            trend_regime=TrendRegime.UPTREND,
            momentum_signals=MomentumSignals(
                gap_up_today_pct=0.0, is_near_52w_high=False,
                is_breakout_20d_high=False, relative_strength_vs_spy_20d=0.05,
                consecutive_up_days=3,
            ),
            intraday=IntradaySignals(
                return_today_pct=0.01, return_last_hour_pct=0.005,
                pct_from_intraday_high=-0.01, is_above_yesterday_high=True,
                intraday_range_pct_of_atr=0.5, consecutive_up_hours=2,
            ),
        )


@dataclass
class FakeStrategist:
    action: str = "BUY"

    def decide(self, summary, market_context=None):
        return StrategyDecision(
            action=self.action,
            ticker=summary.ticker,
            strategy="MOMENTUM",
            params={"lookback_days": 20, "momentum_threshold": 0.05},
            rationale="Test decision for replay",
            confidence=0.7,
        )


@dataclass
class FakeBacktester:
    exit_reason: str = "take_profit"
    exit_pct: float = 5.0

    def simulate(self, *, window, entry_price, entry_atr, sl_atr, tp_atr, trail_pct, timeout_bars):
        from quanterback.adapters.risk.vectorized_backtester import SimResult
        return SimResult(
            entry_price=entry_price,
            exit_price=entry_price * (1 + self.exit_pct / 100),
            bars_held=5,
            exit_reason=self.exit_reason,
        )


def test_max_llm_call_cap_respected() -> None:
    data = FakeDataProvider(series={
        "AAPL": _make_df(),
        "NVDA": _make_df(),
        "TSLA": _make_df(),
    })
    engine = ReplayEngine(
        data_provider=data,
        summarizer=FakeSummarizer(),
        strategist=FakeStrategist(),
        backtester=FakeBacktester(),
    )
    cfg = ReplayConfig(
        start=date(2026, 4, 1), end=date(2026, 5, 30),
        tickers=["AAPL", "NVDA", "TSLA"],
        max_llm_calls=5,
    )
    result = engine.run(cfg)
    assert result.llm_calls_made <= 5


def test_buy_decisions_generate_synthetic_trades() -> None:
    # More data to cover lookback + forward
    data = FakeDataProvider(series={"AAPL": _make_df(n=400)})
    engine = ReplayEngine(
        data_provider=data,
        summarizer=FakeSummarizer(),
        strategist=FakeStrategist(action="BUY"),
        backtester=FakeBacktester(exit_pct=5.0),
    )
    # Start at 2026-05-15: that's bar 135 (May 1 + 14 days),
    # enough for 100-bar lookback
    cfg = ReplayConfig(
        start=date(2026, 5, 15),
        end=date(2026, 5, 25),
        tickers=["AAPL"],
        max_llm_calls=10,
        lookback_bars=100,
    )
    result = engine.run(cfg)
    assert result.llm_calls_made > 0
    assert len(result.synthetic_trades) > 0
    # All trades show winning P&L (5% exit_pct fixed)
    assert all(t.pnl_usd > 0 for t in result.synthetic_trades)


def test_pass_decisions_no_trades() -> None:
    data = FakeDataProvider(series={"AAPL": _make_df(n=400)})
    engine = ReplayEngine(
        data_provider=data,
        summarizer=FakeSummarizer(),
        strategist=FakeStrategist(action="PASS"),
        backtester=FakeBacktester(),
    )
    cfg = ReplayConfig(
        start=date(2026, 5, 15), end=date(2026, 5, 25),
        tickers=["AAPL"], max_llm_calls=10,
        lookback_bars=100,
    )
    result = engine.run(cfg)
    assert result.llm_calls_made > 0
    assert len(result.synthetic_trades) == 0


def test_renders_report(tmp_path: Path) -> None:
    # Create a minimal template just for testing
    templates_dir = tmp_path / "templates" / "en"
    templates_dir.mkdir(parents=True)
    (templates_dir / "replay.j2").write_text(
        "Replay Report\nRange: {{ start }} to {{ end }}\nTrades: {{ total_count }}"
    )
    i18n = I18n(language="en", templates_dir=tmp_path / "templates")

    data = FakeDataProvider(series={"AAPL": _make_df(n=400)})
    engine = ReplayEngine(
        data_provider=data,
        summarizer=FakeSummarizer(),
        strategist=FakeStrategist(action="BUY"),
        backtester=FakeBacktester(exit_pct=3.0),
    )
    cfg = ReplayConfig(
        start=date(2026, 5, 15), end=date(2026, 5, 18),
        tickers=["AAPL"], max_llm_calls=5,
        lookback_bars=100,
    )
    result = engine.run(cfg)
    out = generate_replay_report(result, i18n)
    assert "Replay Report" in out
    assert "2026-05-15" in out
    assert "2026-05-18" in out
