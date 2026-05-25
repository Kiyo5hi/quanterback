from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from quanterback.domain.events import ScanEvent


class WatchlistEventSource:
    """Reads tickers from a text file, one per line. `#` lines and blanks ignored.

    Falls back to file reading if store is not available (e.g., testing).
    """

    def __init__(self, path: Path, store: object | None = None) -> None:
        self._path = path
        self._store = store

    def stream(self) -> Iterable[ScanEvent]:
        now = datetime.now(tz=timezone.utc)

        # Try to read from SQLite store first
        if self._store is not None:
            try:
                from quanterback.adapters.store.sqlite_store import SqliteStore
                if isinstance(self._store, SqliteStore):
                    entries = self._store.list_watchlist()
                    if entries:
                        for entry in entries:
                            yield ScanEvent(
                                ticker=entry.ticker, source="watchlist", requested_at=now
                            )
                        return
            except Exception:
                pass  # Fall through to file reading

        # Fall back to file reading
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            ticker = line.strip()
            if not ticker or ticker.startswith("#"):
                continue
            yield ScanEvent(ticker=ticker, source="watchlist", requested_at=now)
