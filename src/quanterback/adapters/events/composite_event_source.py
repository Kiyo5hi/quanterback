"""EventSource that merges watchlist + pending user triggers."""
from __future__ import annotations

from collections.abc import Iterable

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import ScanEvent
from quanterback.interfaces.events import EventSource


class CompositeEventSource:
    """Drains user triggers (priority 10), then screener picks (priority 5),
    then watchlist (priority 0). Marks user triggers processed."""

    def __init__(
        self,
        watchlist: EventSource,
        store: SqliteStore,
        screener: EventSource | None = None,
    ) -> None:
        self._watchlist = watchlist
        self._store = store
        self._screener = screener

    def stream(self) -> Iterable[ScanEvent]:
        # 1. User triggers
        triggers = self._store.query_pending_user_triggers()
        for t in triggers:
            yield ScanEvent(
                ticker=t.ticker,
                source=f"user_trigger:{t.actor}",
                priority=10,
                requested_at=t.requested_at,
            )
            if t.id is not None:
                self._store.mark_user_trigger_processed(t.id)
        # 2. Screener picks
        if self._screener is not None:
            yield from self._screener.stream()
        # 3. Watchlist
        yield from self._watchlist.stream()
