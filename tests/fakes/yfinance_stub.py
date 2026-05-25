"""Stub of `yfinance.Ticker.history` used to make YFinanceProvider testable."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd


def make_daily_df(days: int = 260, start_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=days, freq="D")
    closes = [start_price + i * 0.5 for i in range(days)]
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 1000 for i in range(days)],
    }, index=idx)


def make_hourly_df(hours: int = 30 * 7) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=hours, freq="h")
    closes = [100.0 + i * 0.1 for i in range(hours)]
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes],
        "Close": closes,
        "Volume": [50_000 for _ in range(hours)],
    }, index=idx)


class StubTicker:
    def __init__(self, daily: pd.DataFrame, hourly: pd.DataFrame) -> None:
        self._daily = daily
        self._hourly = hourly
        self.calendar = pd.DataFrame({
            "Earnings Date": [date(2026, 6, 15)],
        })
        self.insider_transactions = pd.DataFrame({
            "Date": [datetime.now(tz=timezone.utc) - timedelta(days=5)],
            "Insider": ["CEO"],
            "Transaction": ["Purchase"],
            "Value": [500_000.0],
        })
        self.upgrades_downgrades = pd.DataFrame({
            "Firm": ["Goldman Sachs"],
            "Action": ["Upgrade"],
            "FromGrade": ["Hold"],
            "ToGrade": ["Buy"],
        }, index=pd.DatetimeIndex([datetime.now(tz=timezone.utc) - timedelta(days=3)], name="GradeDate"))
        self.info = {
            "shortPercentOfFloat": 0.15,
            "daysToCover": 2.5,
            "shortRatio": 1.2,
            "earningsQuarterlyGrowth": 0.25,
        }
        self.earnings_estimate = pd.DataFrame({
            "avg": [2.5],
        }, index=["0q"])

    def history(self, period: str | None = None, interval: str = "1d", **kw) -> pd.DataFrame:
        if interval == "1d":
            return self._daily
        return self._hourly
