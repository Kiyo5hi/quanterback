from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import (
    AnalystAction,
    CondensedSummary,
    EpsTrend,
    InsiderActivity,
    NewsItem,
    ShortInterestSnapshot,
)
from quanterback.interfaces.data import (
    DataProvider,
    FundamentalsProvider,
    NewsProvider,
    Summarizer,
)
from quanterback.interfaces.decision import LLMStrategist

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchAnalysis:
    """Single-ticker research output with no trading side effects."""

    ticker: str
    summary: CondensedSummary
    decision: StrategyDecision
    model_name: str


@dataclass
class ResearchAnalyzer:
    """Read-only single ticker analysis capability.

    This intentionally stops at the LLM decision. It does not run approval
    gates, backtests, risk gates, order builders, broker calls, position
    reconciliation, or notification delivery.
    """

    data_provider: DataProvider
    summarizer: Summarizer
    strategist: LLMStrategist
    news_provider: NewsProvider | None = None
    fundamentals_provider: FundamentalsProvider | None = None
    spy_closes: object | None = None
    allow_short_history: bool = True

    def analyze_ticker(self, ticker: str) -> ResearchAnalysis:
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker is required")

        window = self.data_provider.fetch(ticker)
        news = self._fetch_news(ticker)
        fundamentals = self._fetch_fundamentals(ticker)

        kwargs = self._summarizer_kwargs(
            news=news,
            earnings_date=fundamentals.earnings_date,
            insider_activity=fundamentals.insider_activity,
            analyst_actions=fundamentals.analyst_actions,
            short_interest=fundamentals.short_interest,
            eps_trend=fundamentals.eps_trend,
            fundamental_ratios=fundamentals.fundamental_ratios,
        )
        summary = self.summarizer.summarize(window, **kwargs)
        decision = self.strategist.decide(summary)
        return ResearchAnalysis(
            ticker=ticker,
            summary=summary,
            decision=decision,
            model_name=getattr(self.strategist, "model_name", "unknown"),
        )

    def _summarizer_kwargs(
        self,
        *,
        news: list[NewsItem],
        earnings_date: date | None,
        insider_activity: InsiderActivity | None,
        analyst_actions: list[AnalystAction] | None,
        short_interest: ShortInterestSnapshot | None,
        eps_trend: EpsTrend | None,
        fundamental_ratios: dict[str, Any],
    ) -> dict[str, Any]:
        sig = inspect.signature(self.summarizer.summarize)
        kwargs: dict[str, Any] = {}
        if "spy_closes" in sig.parameters:
            kwargs["spy_closes"] = self.spy_closes
        if "news" in sig.parameters:
            kwargs["news"] = news
        if "earnings_date" in sig.parameters:
            kwargs["earnings_date"] = earnings_date
        if "insider_activity" in sig.parameters:
            kwargs["insider_activity"] = insider_activity
        if "analyst_actions" in sig.parameters:
            kwargs["analyst_actions"] = analyst_actions
        if "short_interest" in sig.parameters:
            kwargs["short_interest"] = short_interest
        if "eps_trend" in sig.parameters:
            kwargs["eps_trend"] = eps_trend
        if "fundamental_ratios" in sig.parameters:
            kwargs["fundamental_ratios"] = fundamental_ratios
        if "allow_short_history" in sig.parameters:
            kwargs["allow_short_history"] = self.allow_short_history
        return kwargs

    def _fetch_news(self, ticker: str) -> list[NewsItem]:
        if self.news_provider is None:
            return []
        try:
            return self.news_provider.fetch_news(ticker)
        except Exception as exc:
            log.warning("Research news fetch failed for %s: %s", ticker, exc)
            return []

    def _fetch_fundamentals(self, ticker: str) -> "_FundamentalInputs":
        provider = self.fundamentals_provider
        if provider is None:
            return _FundamentalInputs()

        earnings_date: date | None = None
        insider_activity: InsiderActivity | None = None
        analyst_actions: list[AnalystAction] = []
        short_interest: ShortInterestSnapshot | None = None
        eps_trend: EpsTrend | None = None
        fundamental_ratios: dict[str, Any] = {}

        try:
            earnings_date = provider.fetch_next_earnings_date(ticker)
        except Exception as exc:
            log.warning("Research earnings date fetch failed for %s: %s", ticker, exc)
        try:
            insider_activity = provider.fetch_insider_activity(ticker)
        except Exception as exc:
            log.warning("Research insider activity fetch failed for %s: %s", ticker, exc)
        try:
            analyst_actions = provider.fetch_analyst_actions(ticker)
        except Exception as exc:
            log.warning("Research analyst actions fetch failed for %s: %s", ticker, exc)
        try:
            short_interest = provider.fetch_short_interest(ticker)
        except Exception as exc:
            log.warning("Research short interest fetch failed for %s: %s", ticker, exc)
        try:
            eps_trend = provider.fetch_eps_trend(ticker)
        except Exception as exc:
            log.warning("Research EPS trend fetch failed for %s: %s", ticker, exc)
        if hasattr(provider, "fetch_fundamentals"):
            try:
                fundamental_ratios = provider.fetch_fundamentals(ticker)
            except Exception as exc:
                log.warning("Research ratios fetch failed for %s: %s", ticker, exc)
        return _FundamentalInputs(
            earnings_date=earnings_date,
            insider_activity=insider_activity,
            analyst_actions=analyst_actions,
            short_interest=short_interest,
            eps_trend=eps_trend,
            fundamental_ratios=fundamental_ratios,
        )


@dataclass(frozen=True)
class _FundamentalInputs:
    earnings_date: date | None = None
    insider_activity: InsiderActivity | None = None
    analyst_actions: list[AnalystAction] | None = None
    short_interest: ShortInterestSnapshot | None = None
    eps_trend: EpsTrend | None = None
    fundamental_ratios: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.analyst_actions is None:
            object.__setattr__(self, "analyst_actions", [])
        if self.fundamental_ratios is None:
            object.__setattr__(self, "fundamental_ratios", {})

