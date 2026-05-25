from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quanterback.adapters.events.universe_screener_event_source import (
    UniverseScreenerEventSource,
)


@dataclass
class FakeHist:
    """HistoricalDataProvider that returns canned dfs per ticker."""
    dfs: dict

    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        if ticker.upper() not in self.dfs:
            raise KeyError(ticker)
        return self.dfs[ticker.upper()].copy()


def _build_df(returns: list[float], volume_pattern: list[float] | None = None) -> pd.DataFrame:
    n = len(returns) + 1
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=n, freq="B")
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    closes = np.array(prices)
    if volume_pattern is None:
        vols = np.full(n, 1_000_000)
    else:
        # left-pad if shorter
        if len(volume_pattern) < n:
            volume_pattern = [1_000_000] * (n - len(volume_pattern)) + volume_pattern
        vols = np.array(volume_pattern)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes,
        "volume": vols.astype(int),
    }, index=idx)


def test_screener_yields_top_n_by_score(tmp_path: Path) -> None:
    # Three tickers with different momentum strengths
    weak = _build_df([0.0001] * 260)         # flat
    medium = _build_df([0.005] * 260)        # steady up
    strong = _build_df([0.01] * 260)         # strong up
    universe = tmp_path / "u.txt"
    universe.write_text("WEAK\nMEDIUM\nSTRONG\n")
    src = UniverseScreenerEventSource(
        universe_path=universe,
        hist_provider=FakeHist({"WEAK": weak, "MEDIUM": medium, "STRONG": strong}),
        top_n=2,
    )
    events = list(src.stream())
    assert len(events) == 2
    tickers = [e.ticker for e in events]
    assert tickers == ["STRONG", "MEDIUM"]
    assert all(e.priority == 5 for e in events)
    assert all(e.source == "screener" for e in events)


def test_failed_fetch_isolated(tmp_path: Path) -> None:
    good = _build_df([0.01] * 260)
    universe = tmp_path / "u.txt"
    universe.write_text("GOOD\nBROKEN\n")
    src = UniverseScreenerEventSource(
        universe_path=universe,
        hist_provider=FakeHist({"GOOD": good}),  # BROKEN not in dict → KeyError
        top_n=10,
    )
    events = list(src.stream())
    assert [e.ticker for e in events] == ["GOOD"]


def test_missing_universe_file_returns_empty(tmp_path: Path) -> None:
    src = UniverseScreenerEventSource(
        universe_path=tmp_path / "missing.txt",
        hist_provider=FakeHist({}),
        top_n=10,
    )
    assert list(src.stream()) == []


def test_comments_and_blanks_filtered(tmp_path: Path) -> None:
    good = _build_df([0.01] * 260)
    universe = tmp_path / "u.txt"
    universe.write_text("# comment\nGOOD\n\n")
    src = UniverseScreenerEventSource(
        universe_path=universe,
        hist_provider=FakeHist({"GOOD": good}),
        top_n=10,
    )
    events = list(src.stream())
    assert [e.ticker for e in events] == ["GOOD"]
