"""Auto-selects top-N tickers from a universe by composite momentum score.

Score for each ticker (computed from 60d daily bars):
  + return_20d   (20-day price change %, weight 1.0)
  + return_5d    (5-day price change %, weight 0.5)
  - distance_from_high (52w high gap, weight 0.5; closer to high = better)
  + volume_ratio (last 5d avg / 20d avg, weight 0.3)

Top N (default 10) by score are yielded as ScanEvent with priority=5.

Failures fetching a single ticker are isolated — the screener continues.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from quanterback.domain.events import ScanEvent
from quanterback.interfaces.data import HistoricalDataProvider

log = logging.getLogger(__name__)


class UniverseScreenerEventSource:
    """EventSource that ranks a static universe by composite momentum score
    and yields the top-N as ScanEvents with priority=5."""

    def __init__(
        self,
        universe_path: Path,
        hist_provider: HistoricalDataProvider,
        *,
        top_n: int = 10,
    ) -> None:
        self._universe_path = universe_path
        self._hist = hist_provider
        self._top_n = top_n

    def set_top_n(self, top_n: int) -> None:
        """Dynamically set the top_n value before streaming."""
        self._top_n = top_n

    def stream(self) -> Iterable[ScanEvent]:
        if not self._universe_path.exists():
            log.warning("Universe file %s missing — yielding nothing", self._universe_path)
            return
        tickers = self._read_universe()
        scored: list[tuple[str, float]] = []
        for t in tickers:
            try:
                score = self._score(t)
                scored.append((t, score))
            except Exception as e:
                log.debug("Score failed for %s: %s — skipped", t, e)
                continue
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[: self._top_n]
        log.info(
            "Universe screener: scored %d/%d tickers; top %d: %s",
            len(scored), len(tickers), len(top),
            ", ".join(f"{t}={s:.2f}" for t, s in top[:5]),
        )
        now = datetime.now(tz=timezone.utc)
        for ticker, _ in top:
            yield ScanEvent(
                ticker=ticker, source="screener",
                priority=5, requested_at=now,
            )

    def _read_universe(self) -> list[str]:
        lines = self._universe_path.read_text().splitlines()
        return [t.strip().upper() for t in lines
                if t.strip() and not t.startswith("#")]

    def _score(self, ticker: str) -> float:
        df = self._hist.fetch_historical(ticker, years=1)
        if len(df) < 60:
            return -1e9
        closes = df["close"]
        last = float(closes.iloc[-1])
        ret_20d = float(closes.iloc[-1] / closes.iloc[-21] - 1) if len(closes) > 21 else 0.0
        ret_5d = float(closes.iloc[-1] / closes.iloc[-6] - 1) if len(closes) > 6 else 0.0
        hi_52w = float(closes.tail(252).max())
        dist_high = (last / hi_52w - 1) if hi_52w > 0 else 0.0  # negative or zero
        # Volume ratio: last 5d vs 20d
        vol = df["volume"]
        avg5 = float(vol.tail(5).mean())
        avg20 = float(vol.tail(20).mean())
        vol_ratio = (avg5 / avg20) if avg20 > 0 else 1.0
        score = (
            1.0 * ret_20d
            + 0.5 * ret_5d
            + 0.5 * (dist_high + 0.10)   # near-high = ~0; bonus when close
            + 0.3 * (vol_ratio - 1.0)
        )
        if not np.isfinite(score):
            return -1e9
        return score
