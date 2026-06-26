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
        *,
        auto_add_screener_to_watchlist: bool = False,
        auto_watchlist_max: int = 50,
    ) -> None:
        self._watchlist = watchlist
        self._store = store
        self._screener = screener
        self._auto_add_screener_to_watchlist = auto_add_screener_to_watchlist
        self._auto_watchlist_max = auto_watchlist_max

    @property
    def screener(self) -> EventSource | None:
        return self._screener

    def stream(self) -> Iterable[ScanEvent]:
        seen: set[str] = set()
        # 1. User triggers
        triggers = self._store.query_pending_user_triggers()
        for t in triggers:
            event = ScanEvent(
                ticker=t.ticker,
                source=f"user_trigger:{t.actor}",
                priority=10,
                requested_at=t.requested_at,
            )
            if event.ticker not in seen:
                seen.add(event.ticker)
                yield event
            if t.id is not None:
                self._store.mark_user_trigger_processed(t.id)
        # 2. Screener picks
        if self._screener is not None:
            for event in self._screener.stream():
                if self._auto_add_screener_to_watchlist:
                    self._store.add_watchlist_ticker(
                        event.ticker,
                        source="auto",
                        notes="auto-selected by universe screener",
                    )
                if event.ticker not in seen:
                    seen.add(event.ticker)
                    yield event
            if self._auto_add_screener_to_watchlist and self._auto_watchlist_max > 0:
                self._store.prune_auto_watchlist(self._auto_watchlist_max)
        # 3. Watchlist
        for event in self._watchlist.stream():
            if event.ticker in seen:
                continue
            seen.add(event.ticker)
            yield event
