"""Stress test — multi-window replay across market regimes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
from quanterback.domain.trade import Trade
from quanterback.i18n import I18n
from quanterback.replay import ReplayConfig, ReplayEngine
from quanterback.stress import (
    DEFAULT_WINDOWS,
    StressWindow,
    generate_stress_report,
    run_stress,
    summarize_stress,
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
            rationale="Test decision for stress test",
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


def test_default_windows_present() -> None:
    assert len(DEFAULT_WINDOWS) >= 5
    assert all(isinstance(w, StressWindow) for w in DEFAULT_WINDOWS)
    # Verify they're ordered by date
    for i in range(len(DEFAULT_WINDOWS) - 1):
        assert DEFAULT_WINDOWS[i].end <= DEFAULT_WINDOWS[i + 1].start


def test_run_stress_executes_all_windows() -> None:
    data = FakeDataProvider(series={
        "AAPL": _make_df(),
        "NVDA": _make_df(),
    })
    engine = ReplayEngine(
        data_provider=data,
        summarizer=FakeSummarizer(),
        strategist=FakeStrategist(action="BUY"),
        backtester=FakeBacktester(exit_pct=3.0),
    )
    windows = [
        StressWindow("test1", date(2026, 4, 1), date(2026, 4, 10)),
        StressWindow("test2", date(2026, 5, 1), date(2026, 5, 10)),
    ]
    rows = run_stress(
        engine=engine, windows=windows, tickers=["AAPL"],
        max_llm_calls_per_window=10,
    )
    assert len(rows) == 2
    assert all(isinstance(w, StressWindow) for w, _ in rows)


def test_summarize_empty_window() -> None:
    from quanterback.replay import ReplayResult
    r = ReplayResult(config=ReplayConfig(
        start=date(2024, 1, 1), end=date(2024, 1, 5),
        tickers=["AAPL"], max_llm_calls=5,
    ))
    summary = summarize_stress([(DEFAULT_WINDOWS[0], r)])
    assert len(summary) == 1
    assert summary[0].n_trades == 0
    assert summary[0].total_pnl_usd == 0.0
    assert summary[0].trade_sharpe == 0.0


def test_summarize_with_trades() -> None:
    from quanterback.replay import ReplayResult
    r = ReplayResult(config=ReplayConfig(
        start=date(2024, 1, 1), end=date(2024, 1, 5),
        tickers=["AAPL"], max_llm_calls=5,
    ))
    now = datetime.now(tz=timezone.utc)
    r.synthetic_trades.append(Trade(
        ticker="AAPL", qty=10, entry_price=100, entry_at=now - timedelta(hours=24),
        exit_price=105, exit_at=now, exit_reason="TAKE_PROFIT",
        pnl_usd=50.0, pnl_pct=5.0, holding_hours=24.0,
    ))
    r.decisions.append({"action": "BUY"})
    summary = summarize_stress([(DEFAULT_WINDOWS[0], r)])
    assert len(summary) == 1
    assert summary[0].n_trades == 1
    assert summary[0].total_pnl_usd == 50.0
    assert summary[0].win_rate_pct == 100.0
    assert summary[0].n_buys == 1


def test_summarize_mixed_wins_losses() -> None:
    from quanterback.replay import ReplayResult
    r = ReplayResult(config=ReplayConfig(
        start=date(2024, 1, 1), end=date(2024, 1, 5),
        tickers=["AAPL"], max_llm_calls=5,
    ))
    now = datetime.now(tz=timezone.utc)
    # Win
    r.synthetic_trades.append(Trade(
        ticker="AAPL", qty=10, entry_price=100, entry_at=now - timedelta(hours=48),
        exit_price=105, exit_at=now - timedelta(hours=24), exit_reason="TAKE_PROFIT",
        pnl_usd=50.0, pnl_pct=5.0, holding_hours=24.0,
    ))
    # Loss
    r.synthetic_trades.append(Trade(
        ticker="AAPL", qty=10, entry_price=100, entry_at=now - timedelta(hours=24),
        exit_price=95, exit_at=now, exit_reason="STOP_LOSS",
        pnl_usd=-50.0, pnl_pct=-5.0, holding_hours=24.0,
    ))
    r.decisions.extend([{"action": "BUY"}, {"action": "BUY"}])
    summary = summarize_stress([(DEFAULT_WINDOWS[0], r)])
    assert summary[0].n_trades == 2
    assert summary[0].total_pnl_usd == 0.0
    assert summary[0].win_rate_pct == 50.0
    assert summary[0].n_buys == 2


def test_renders_report(tmp_path: Path) -> None:
    from quanterback.replay import ReplayResult
    # Create a minimal template just for testing
    templates_dir = tmp_path / "templates" / "en"
    templates_dir.mkdir(parents=True)
    (templates_dir / "stress.j2").write_text(
        "Stress Report\nWindows: {{ n_windows }}\nTrades: {{ rows[0].n_trades }}"
    )
    i18n = I18n(language="en", templates_dir=tmp_path / "templates")

    r = ReplayResult(config=ReplayConfig(
        start=date(2024, 1, 1), end=date(2024, 1, 5),
        tickers=["AAPL"], max_llm_calls=5,
    ))
    now = datetime.now(tz=timezone.utc)
    r.synthetic_trades.append(Trade(
        ticker="AAPL", qty=10, entry_price=100, entry_at=now - timedelta(hours=24),
        exit_price=105, exit_at=now, exit_reason="TAKE_PROFIT",
        pnl_usd=50.0, pnl_pct=5.0, holding_hours=24.0,
    ))
    r.decisions.append({"action": "BUY"})
    summary = summarize_stress([(DEFAULT_WINDOWS[0], r)])
    out = generate_stress_report(summary, i18n)
    assert "Stress Report" in out
    assert "Windows: 1" in out
    assert "Trades: 1" in out
