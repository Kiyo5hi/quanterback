"""Auto-promote/demote watchlist based on decision history."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore

log = logging.getLogger(__name__)


@dataclass
class WatchlistAutoManager:
    """Auto-manage watchlist membership based on scan decision history."""

    store: SqliteStore
    promote_min_buys: int = 3  # ≥3 BUYs in last 7 days → promote
    promote_window_days: int = 7
    demote_max_quiet_days: int = 14  # no BUY in 14 days → demote (auto-source only)
    enabled: bool = True

    def tick(self) -> dict:
        """Run after each scan completes. Returns counts of promoted/demoted tickers."""
        if not self.enabled:
            return {"promoted": 0, "demoted": 0}
        promoted = self._promote_from_recent_buys()
        demoted = self._demote_quiet_auto_entries()
        return {"promoted": promoted, "demoted": demoted}

    def _promote_from_recent_buys(self) -> int:
        """Find tickers with ≥N BUY decisions in past M days and add them to watchlist."""
        from quanterback.adapters.store.sqlite_store import SqliteStore
        assert isinstance(self.store, SqliteStore)
        conn = self.store._conn
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=self.promote_window_days)).isoformat()
        rows = conn.execute("""
            SELECT ticker FROM decisions
            WHERE created_at >= ? AND rejected_reason IS NULL
              AND decision_json LIKE '%"action": "BUY"%'
        """, (cutoff,)).fetchall()
        counter = Counter(r["ticker"] for r in rows)
        current = {e.ticker for e in self.store.list_watchlist()}
        n = 0
        for ticker, count in counter.items():
            if count >= self.promote_min_buys and ticker not in current:
                added = self.store.add_watchlist_ticker(
                    ticker, source="auto",
                    notes=f"auto-promoted ({count} BUYs in {self.promote_window_days}d)",
                )
                if added:
                    log.info("Auto-promoted %s to watchlist (%d BUYs)", ticker, count)
                    n += 1
        return n

    def _demote_quiet_auto_entries(self) -> int:
        """Remove auto-source entries with no BUY decision in past N days."""
        from quanterback.adapters.store.sqlite_store import SqliteStore
        assert isinstance(self.store, SqliteStore)
        conn = self.store._conn
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=self.demote_max_quiet_days)).isoformat()
        n = 0
        for entry in self.store.list_watchlist():
            if entry.source != "auto":
                continue
            row = conn.execute("""
                SELECT COUNT(*) as c FROM decisions
                WHERE ticker = ? AND created_at >= ?
                  AND rejected_reason IS NULL
                  AND decision_json LIKE '%"action": "BUY"%'
            """, (entry.ticker, cutoff)).fetchone()
            if row["c"] == 0:
                self.store.remove_watchlist_ticker(entry.ticker)
                log.info("Auto-demoted %s (no BUY in %dd)", entry.ticker, self.demote_max_quiet_days)
                n += 1
        return n
