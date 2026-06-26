from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

from quanterback.domain.market import (
    AnalystAction,
    CondensedSummary,
    EpsTrend,
    InsiderActivity,
    NewsItem,
    PriceWindow,
    ShortInterestSnapshot,
)


class DataProvider(Protocol):
    def fetch(self, ticker: str) -> PriceWindow: ...


class Summarizer(Protocol):
    def summarize(
        self,
        window: PriceWindow,
        spy_closes: pd.Series | None = None,
        news: list[NewsItem] | None = None,
        earnings_date: date | None = None,
        insider_activity: InsiderActivity | None = None,
        analyst_actions: list[AnalystAction] | None = None,
        short_interest: ShortInterestSnapshot | None = None,
        eps_trend: EpsTrend | None = None,
        allow_short_history: bool = False,
    ) -> CondensedSummary:
        ...


class HistoricalDataProvider(Protocol):
    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        """Return a normalized daily OHLCV DataFrame for the past `years` years."""
        ...


class NewsProvider(Protocol):
    """Adapter that fetches recent news headlines for a ticker."""
    def fetch_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        ...


class FundamentalsProvider(Protocol):
    """Adapter that fetches fundamental and market microstructure data."""
    def fetch_next_earnings_date(self, ticker: str) -> date | None:
        """Returns next earnings call date if known."""
        ...

    def fetch_insider_activity(self, ticker: str, lookback_days: int = 30) -> InsiderActivity | None:
        """Aggregates Form 4 activity in lookback window."""
        ...

    def fetch_analyst_actions(self, ticker: str, lookback_days: int = 14) -> list[AnalystAction]:
        """Returns analyst rating changes in lookback window."""
        ...

    def fetch_short_interest(self, ticker: str) -> ShortInterestSnapshot | None:
        """Returns current short interest metrics."""
        ...

    def fetch_eps_trend(self, ticker: str) -> EpsTrend | None:
        """Returns EPS estimate and trend."""
        ...
