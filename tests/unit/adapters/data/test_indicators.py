from __future__ import annotations

import pandas as pd

from quanterback.adapters.data.indicators import (
    atr_wilder,
    macd_recent_cross,
    realized_vol_annualized,
    rsi_wilder,
    simple_moving_average,
)


def _flat_then_up_close() -> pd.Series:
    # Strong downtrend followed by strong uptrend
    # Ensures MACD cross happens toward the end of the series (within recent window)
    down = [100.0 - 2 * i for i in range(0, 25)]  # 100 down to 50 in 25 bars
    up = [50.0 + 3 * i for i in range(0, 25)]     # 50 up to 122 in 25 bars
    return pd.Series(down + up)


def test_sma_window_matches_manual() -> None:
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert simple_moving_average(s, 3).iloc[-1] == 4.0


def test_rsi_full_uptrend_is_100() -> None:
    s = pd.Series(range(1, 31), dtype=float)
    rsi = rsi_wilder(s, period=14)
    assert rsi.iloc[-1] > 95


def test_rsi_full_downtrend_is_0() -> None:
    s = pd.Series(range(30, 0, -1), dtype=float)
    rsi = rsi_wilder(s, period=14)
    assert rsi.iloc[-1] < 5


def test_atr_nonnegative() -> None:
    df = pd.DataFrame({
        "high":  [10, 11, 12, 11, 13],
        "low":   [8,  9,  9,  10, 11],
        "close": [9,  10, 11, 10, 12],
    }, dtype=float)
    atr = atr_wilder(df, period=3)
    assert (atr.dropna() > 0).all()


def test_realized_vol_zero_for_flat() -> None:
    s = pd.Series([100.0] * 50)
    assert realized_vol_annualized(s, 20) == 0.0


def test_macd_bullish_cross_detected() -> None:
    # Sharp downtrend followed by recovery causes MACD to cross above signal
    # Cross happens around bar 20, recent window (last 6) catches it
    down = [100.0 - 3.0 * i for i in range(0, 16)]  # Steep down: 100→52
    up = [52.0 + 4.0 * i for i in range(0, 9)]      # Partial recovery: 52→84, keeps cross recent
    s = pd.Series(down + up)
    result = macd_recent_cross(s, window=5)
    assert result == "bullish_cross"


def test_macd_no_cross_on_flat() -> None:
    s = pd.Series([100.0] * 60)
    assert macd_recent_cross(s, window=5) == "none"
