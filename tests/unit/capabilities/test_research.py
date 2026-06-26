from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.capabilities.research import ResearchAnalyzer
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import NewsItem, PriceWindow


def _window(ticker: str = "SPCX") -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 6, 25, tzinfo=timezone.utc), periods=9, freq="B")
    closes = np.array([24.0, 24.8, 25.2, 24.9, 25.7, 26.2, 27.1, 26.8, 27.4])
    daily = pd.DataFrame({
        "open": closes * 0.995,
        "high": closes * 1.03,
        "low": closes * 0.97,
        "close": closes,
        "volume": np.full(9, 900_000),
    }, index=idx)
    hourly_idx = pd.date_range(end=datetime(2026, 6, 25, 20, tzinfo=timezone.utc),
                               periods=40, freq="h")
    hourly_closes = np.linspace(25.5, 27.4, 40)
    hourly = pd.DataFrame({
        "open": hourly_closes * 0.998,
        "high": hourly_closes * 1.006,
        "low": hourly_closes * 0.994,
        "close": hourly_closes,
        "volume": np.full(40, 80_000),
    }, index=hourly_idx)
    return PriceWindow(
        ticker=ticker, daily=daily, hourly=hourly,
        as_of=datetime(2026, 6, 25, tzinfo=timezone.utc),
    )


@dataclass
class FakeDataProvider:
    def fetch(self, ticker: str) -> PriceWindow:
        return _window(ticker)


@dataclass
class FakeNewsProvider:
    def fetch_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return [
            NewsItem(
                title=f"{ticker} trading volume surges",
                publisher="Newswire",
                age_hours=1.0,
            ),
        ]


@dataclass
class FakeStrategist:
    model_name: str = "fake-research"
    seen_news: int = 0

    def decide(self, summary):
        self.seen_news = len(summary.news)
        return StrategyDecision(
            action="PASS",
            ticker=summary.ticker,
            strategy="MOMENTUM",
            params=None,
            rationale="Research view only; signal is not strong enough for action.",
            confidence=0.42,
            news_sentiment=0.1,
        )


def test_research_analyzer_runs_single_ticker_without_trading_dependencies() -> None:
    strategist = FakeStrategist()
    analyzer = ResearchAnalyzer(
        data_provider=FakeDataProvider(),
        summarizer=RuleBasedSummarizer(),
        strategist=strategist,
        news_provider=FakeNewsProvider(),
    )

    result = analyzer.analyze_ticker("spcx")

    assert result.ticker == "SPCX"
    assert result.summary.ticker == "SPCX"
    assert result.summary.volatility.atr_14 > 0
    assert result.decision.action == "PASS"
    assert result.model_name == "fake-research"
    assert strategist.seen_news == 1

