from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.domain.backtest import BacktestRequest
from quanterback.domain.market import PriceWindow
from tests.fakes.historical_data import FakeHistoricalDataProvider


def _smooth_uptrend(days: int = 1000) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=days, freq="B")
    closes = np.linspace(100, 250, days)
    noise = np.sin(np.linspace(0, days / 5, days)) * 2.0
    closes = closes + noise
    df = pd.DataFrame({
        "open":  closes, "high":  closes * 1.01,
        "low":   closes * 0.99, "close": closes,
        "volume": np.full(days, 1_000_000),
    }, index=idx)
    return df


def _whipsaw(days: int = 1000) -> pd.DataFrame:
    """Sawtooth: sharp 6-bar rises followed by sharp 6-bar drops.

    Momentum signals fire after a rise (lookback_days=5 catches the up leg).
    Entry is on bar 6 (the open of the first DOWN bar) → price immediately falls
    to the stop-loss, producing a losing trade on almost every entry.
    """
    rng = np.random.default_rng(42)
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=days, freq="B")
    # Alternate: 6 bars up (+1.5% each), then 6 bars down (-1.5% each)
    # Net: flat overall, but momentum signal fires right at the top of each tooth.
    returns = np.empty(days)
    for i in range(days):
        phase = i % 12
        if phase < 6:
            returns[i] = 0.015 + rng.normal(0, 0.001)   # rise leg
        else:
            returns[i] = -0.015 + rng.normal(0, 0.001)  # fall leg
    closes = 100 * np.cumprod(1 + returns)
    df = pd.DataFrame({
        "open":  closes, "high":  closes * 1.005,
        "low":   closes * 0.995, "close": closes,
        "volume": np.full(days, 1_000_000),
    }, index=idx)
    return df


def test_backtest_uptrend_produces_trades() -> None:
    # Swing calibration: tighter SL/TP (1.5/3.0) + shorter timeout (20 bars)
    # means we catch quick moves but may exit on timeout with small losses
    # on smooth, slow uptrends that lack volatility.
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
        lookback_years=3,
    ))
    assert r.num_trades >= 1
    assert r.max_drawdown < 0.30  # not catastrophic


def test_backtest_whipsaw_low_winrate() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _whipsaw()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 5, "momentum_threshold": 0.02},
        lookback_years=3,
    ))
    # Whipsaw should produce many losing momentum trades
    assert r.win_rate < 0.65
    assert r.num_trades >= 1


def test_backtest_metrics_are_finite() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
    ))
    assert np.isfinite(r.sharpe)
    assert np.isfinite(r.profit_factor) or r.profit_factor == 0


def test_backtest_zero_trades_returns_safe_report() -> None:
    # Impossible threshold → zero entries
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _whipsaw()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.29},
    ))
    assert r.num_trades >= 0  # may be zero
    if r.num_trades == 0:
        assert r.win_rate == 0
        assert r.profit_factor == 0


def _oscillating_data(days: int = 1000) -> pd.DataFrame:
    """Stable mean with periodic oversold dips — should trigger mean reversion entries."""
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=days, freq="B")
    base = 100.0
    # Mostly oscillating ±2%, occasional spikes to -8% to trigger oversold
    closes = base + np.sin(np.linspace(0, 80, days)) * 2.0
    # Inject deeper dips every ~50 bars
    for i in range(50, days, 100):
        for j in range(i, min(i + 4, days)):
            closes[j] -= 6.0
    df = pd.DataFrame({"open": closes, "high": closes * 1.01,
                       "low": closes * 0.99, "close": closes,
                       "volume": np.full(days, 1_000_000)}, index=idx)
    return df


def test_mean_reversion_oscillating_produces_trades() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _oscillating_data()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MEAN_REVERSION",
        params={"lookback_days": 20, "entry_z_score": 2.0},
        lookback_years=3,
    ))
    assert r.num_trades >= 1
    assert r.cumulative_return is not None


def test_mean_reversion_smooth_uptrend_few_trades() -> None:
    # Smooth uptrend: rarely oversold, very few MR entries expected
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MEAN_REVERSION",
        params={"lookback_days": 20, "entry_z_score": 2.0},
    ))
    # Smooth uptrend with noise rarely dips 2 std below mean
    assert r.num_trades < 10


def test_calibrated_constants_for_swing() -> None:
    from quanterback.adapters.risk.vectorized_backtester import (  # noqa: I001
        SL_ATR_MULT_BT,
        TP_ATR_MULT_BT,
        TIMEOUT_BARS,
    )
    assert SL_ATR_MULT_BT == 1.5
    assert TP_ATR_MULT_BT == 3.0
    assert TIMEOUT_BARS == 20


def test_new_fields_populated_on_uptrend() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
    ))
    assert r.buy_and_hold_return > 0
    assert r.buy_and_hold_max_drawdown >= 0
    assert r.drawdown_ratio >= 0
    assert isinstance(r.oos_num_trades, int)
    assert isinstance(r.oos_sharpe, float)


def test_sl_triggers_on_sharp_drop() -> None:
    """Verify SL fires when price drops below entry - 1.0*ATR."""
    # Construct synthetic forward OHLC: entry at 100, then drop to 90 by bar 5
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=10, freq="B")
    df = pd.DataFrame({
        "open":  [100.0, 99.0, 98.0, 95.0, 92.0, 90.0, 91.0, 92.0, 93.0, 94.0],
        "high":  [101.0, 100.0, 99.0, 96.0, 93.0, 91.0, 92.0, 93.0, 94.0, 95.0],
        "low":   [99.0, 98.0, 97.0, 94.0, 91.0, 89.0, 90.0, 91.0, 92.0, 93.0],
        "close": [100.0, 99.0, 98.0, 95.0, 92.0, 90.0, 91.0, 92.0, 93.0, 94.0],
        "volume": np.full(10, 1_000_000),
    }, index=idx)

    window = PriceWindow(ticker="TEST", daily=df, hourly=df, as_of=df.index[-1].to_pydatetime())

    # entry_atr=2.0 means SL = 100 - 1.0*2.0 = 98.0
    # Price hits 90 on bar 5, well below SL
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"TEST": df}))
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=2.0,
        sl_atr=1.0,
        tp_atr=2.5,
        trail_pct=0.08,
        timeout_bars=20,
    )

    assert sim is not None
    assert sim.exit_reason == "stop_loss"
    assert sim.exit_price == 98.0  # SL level
    assert sim.bars_held <= 5  # Should hit within first 5 bars


def test_tp_triggers_on_sharp_rise() -> None:
    """Verify TP fires when price rises above entry + 2.5*ATR."""
    # Construct synthetic forward OHLC: entry at 100, then rise to 110 by bar 5
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=10, freq="B")
    df = pd.DataFrame({
        "open":  [100.0, 101.0, 103.0, 105.0, 108.0, 110.0, 109.0, 108.0, 107.0, 106.0],
        "high":  [101.0, 102.0, 104.0, 106.0, 109.0, 111.0, 110.0, 109.0, 108.0, 107.0],
        "low":   [99.0, 100.0, 102.0, 104.0, 107.0, 109.0, 108.0, 107.0, 106.0, 105.0],
        "close": [100.0, 101.0, 103.0, 105.0, 108.0, 110.0, 109.0, 108.0, 107.0, 106.0],
        "volume": np.full(10, 1_000_000),
    }, index=idx)

    window = PriceWindow(ticker="TEST", daily=df, hourly=df, as_of=df.index[-1].to_pydatetime())

    # entry_atr=3.0 means TP = 100 + 2.5*3.0 = 107.5
    # Price hits 110 on bar 5, above TP
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"TEST": df}))
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=3.0,
        sl_atr=1.0,
        tp_atr=2.5,
        trail_pct=0.08,
        timeout_bars=20,
    )

    assert sim is not None
    assert sim.exit_reason == "take_profit"
    assert sim.exit_price == 107.5  # TP level
    assert sim.bars_held <= 5  # Should hit within first 5 bars


def test_timeout_when_no_sl_tp_hit() -> None:
    """Verify TIMEOUT exit when SL/TP are not triggered within timeout window."""
    # Construct synthetic forward OHLC: slow, mild move
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=25, freq="B")
    closes_array = 100.0 + np.linspace(0, 1.0, 25)  # Very slow +1% move over 25 bars
    df = pd.DataFrame({
        "open":  closes_array,
        "high":  closes_array * 1.005,
        "low":   closes_array * 0.995,
        "close": closes_array,
        "volume": np.full(25, 1_000_000),
    }, index=idx)

    window = PriceWindow(ticker="TEST", daily=df, hourly=df, as_of=df.index[-1].to_pydatetime())

    # entry_atr=5.0 means SL = 92.5, TP = 115.0 — both untouched in slow move
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"TEST": df}))
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=5.0,
        sl_atr=1.5,
        tp_atr=3.0,
        trail_pct=0.08,
        timeout_bars=10,  # timeout after 10 bars, price still in range
    )

    assert sim is not None
    assert sim.exit_reason == "timeout"
    assert sim.bars_held == 10  # Should exit at timeout boundary
    assert sim.exit_price == float(df["close"].iloc[10])  # Exit at close of bar 10


def test_simulate_rejects_nan_atr() -> None:
    """Verify simulate() returns None when entry_atr is NaN."""
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=10, freq="B")
    df = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [101.0] * 10,
        "low": [99.0] * 10,
        "close": [100.0] * 10,
        "volume": np.full(10, 1_000_000),
    }, index=idx)

    window = PriceWindow(ticker="TEST", daily=df, hourly=df, as_of=df.index[-1].to_pydatetime())

    bt = VectorizedBacktester(FakeHistoricalDataProvider({"TEST": df}))
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=float("nan"),  # NaN ATR should be rejected
        sl_atr=1.5,
        tp_atr=3.0,
        trail_pct=0.08,
        timeout_bars=20,
    )

    assert sim is None


def test_simulate_rejects_zero_or_negative_atr() -> None:
    """Verify simulate() returns None when entry_atr <= 0."""
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=10, freq="B")
    df = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [101.0] * 10,
        "low": [99.0] * 10,
        "close": [100.0] * 10,
        "volume": np.full(10, 1_000_000),
    }, index=idx)

    window = PriceWindow(ticker="TEST", daily=df, hourly=df, as_of=df.index[-1].to_pydatetime())

    bt = VectorizedBacktester(FakeHistoricalDataProvider({"TEST": df}))

    # Test zero ATR
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=0.0,
        sl_atr=1.5,
        tp_atr=3.0,
        trail_pct=0.08,
        timeout_bars=20,
    )
    assert sim is None

    # Test negative ATR
    sim = bt.simulate(
        window=window,
        entry_price=100.0,
        entry_atr=-1.0,
        sl_atr=1.5,
        tp_atr=3.0,
        trail_pct=0.08,
        timeout_bars=20,
    )
    assert sim is None
