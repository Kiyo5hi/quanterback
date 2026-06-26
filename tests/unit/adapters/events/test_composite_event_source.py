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


def test_exposes_screener_for_dynamic_top_n(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("")
    screener = FakeScreener(tickers=["MU"])
    composite = CompositeEventSource(
        watchlist=WatchlistEventSource(wl), store=store, screener=screener,
    )
    assert composite.screener is screener


def test_auto_adds_screener_picks_to_watchlist(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("AAPL\n")
    store.add_watchlist_ticker("AAPL", source="config")
    screener = FakeScreener(tickers=["MU", "AMD"])
    composite = CompositeEventSource(
        watchlist=WatchlistEventSource(wl),
        store=store,
        screener=screener,
        auto_add_screener_to_watchlist=True,
        auto_watchlist_max=10,
    )

    events = list(composite.stream())
    entries = store.list_watchlist()

    assert [e.ticker for e in events] == ["MU", "AMD", "AAPL"]
    assert {e.ticker: e.source for e in entries} == {
        "AAPL": "config",
        "AMD": "auto",
        "MU": "auto",
    }


def test_auto_watchlist_prunes_oldest_auto_entries(
    store: SqliteStore, tmp_path: Path,
) -> None:
    wl = tmp_path / "wl.txt"
    wl.write_text("")
    store.add_watchlist_ticker("OLD1", source="auto")
    store.add_watchlist_ticker("OLD2", source="auto")
    store.add_watchlist_ticker("KEEP", source="user")
    screener = FakeScreener(tickers=["NEW1", "NEW2"])
    composite = CompositeEventSource(
        watchlist=WatchlistEventSource(wl),
        store=store,
        screener=screener,
        auto_add_screener_to_watchlist=True,
        auto_watchlist_max=2,
    )

    list(composite.stream())
    entries = store.list_watchlist()

    assert {e.ticker for e in entries if e.source == "auto"} == {"NEW1", "NEW2"}
    assert any(e.ticker == "KEEP" and e.source == "user" for e in entries)
