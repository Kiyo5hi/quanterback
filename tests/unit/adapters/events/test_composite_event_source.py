from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.events.composite_event_source import CompositeEventSource
from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import ScanEvent
from quanterback.domain.persisted import PersistedUserTrigger


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def test_yields_user_triggers_first_then_watchlist(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("MSFT\nGOOGL\n")
    watch = WatchlistEventSource(wl)

    store.insert_user_trigger(PersistedUserTrigger(
        ticker="NVDA", actor="42",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    store.insert_user_trigger(PersistedUserTrigger(
        ticker="TSLA", actor="42",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))

    composite = CompositeEventSource(watchlist=watch, store=store)
    events = list(composite.stream())
    tickers = [e.ticker for e in events]
    sources = [e.source for e in events]
    assert tickers == ["NVDA", "TSLA", "MSFT", "GOOGL"]
    assert sources[0].startswith("user_trigger:")
    assert sources[2] == "watchlist"


def test_user_triggers_marked_processed_after_stream(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("MSFT\n")
    watch = WatchlistEventSource(wl)

    store.insert_user_trigger(PersistedUserTrigger(
        ticker="NVDA", actor="42",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))

    composite = CompositeEventSource(watchlist=watch, store=store)
    list(composite.stream())  # consume

    pending = store.query_pending_user_triggers()
    assert pending == []  # NVDA was marked processed


def test_no_user_triggers_yields_only_watchlist(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("MSFT\n")
    composite = CompositeEventSource(
        watchlist=WatchlistEventSource(wl), store=store,
    )
    events = list(composite.stream())
    assert [e.ticker for e in events] == ["MSFT"]
    assert events[0].source == "watchlist"


def test_user_trigger_priority_is_10(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("")
    store.insert_user_trigger(PersistedUserTrigger(
        ticker="NVDA", actor="42",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    composite = CompositeEventSource(
        watchlist=WatchlistEventSource(wl), store=store,
    )
    events = list(composite.stream())
    assert len(events) == 1
    assert events[0].priority == 10


@dataclass
class FakeScreener:
    tickers: list[str]

    def stream(self):
        now = datetime.now(tz=timezone.utc)
        for t in self.tickers:
            yield ScanEvent(ticker=t, source="screener",
                             priority=5, requested_at=now)


def test_screener_yields_between_user_trigger_and_watchlist(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("AAPL\n")
    watch = WatchlistEventSource(wl)
    store.insert_user_trigger(PersistedUserTrigger(
        ticker="NVDA", actor="42",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    screener = FakeScreener(tickers=["MU", "AMD"])
    composite = CompositeEventSource(
        watchlist=watch, store=store, screener=screener,
    )
    events = list(composite.stream())
    assert [e.ticker for e in events] == ["NVDA", "MU", "AMD", "AAPL"]
