from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def simple_moving_average(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window, min_periods=window).mean()


def rsi_wilder(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100.0).clip(0, 100)


def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def realized_vol_annualized(closes: pd.Series, window: int = 20) -> float:
    returns = closes.pct_change().dropna()
    if len(returns) < window:
        window = len(returns)
    if window <= 1:
        return 0.0
    return float(returns.tail(window).std() * np.sqrt(252))


def macd_recent_cross(
    closes: pd.Series, window: int = 5,
) -> Literal["bullish_cross", "bearish_cross", "none"]:
    fast = closes.ewm(span=12, adjust=False).mean()
    slow = closes.ewm(span=26, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False).mean()
    diff = macd - signal
    if len(diff) < window + 1:
        return "none"
    recent = diff.tail(window + 1).to_list()
    for i in range(1, len(recent)):
        if recent[i - 1] <= 0 < recent[i]:
            return "bullish_cross"
        if recent[i - 1] >= 0 > recent[i]:
            return "bearish_cross"
    return "none"
