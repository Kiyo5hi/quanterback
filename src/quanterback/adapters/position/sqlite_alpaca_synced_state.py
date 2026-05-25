from __future__ import annotations

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.position import OpenLifecycle


class SqliteAlpacaSyncedPositionState:
    """Position-state service backed by SQLite. Optional reconciliation with Alpaca.

    v0 reconciliation is intentionally a stub. In v1 we can pass an `Executor` and
    cross-check `client.get_all_positions()` on construction.
    """

    def __init__(self, store: SqliteStore, *, alpaca_synced: bool = False) -> None:
        self._store = store
        self._alpaca_synced = alpaca_synced

    def has_open_lifecycle(self, ticker: str) -> bool:
        return self.get_open(ticker) is not None

    def get_open(self, ticker: str) -> OpenLifecycle | None:
        ticker = ticker.upper()
        for lc in self._store.query_open_lifecycles():
            if lc.ticker == ticker:
                return lc
        return None
