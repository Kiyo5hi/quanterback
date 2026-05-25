from __future__ import annotations

from pathlib import Path

from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource


def test_stream_yields_one_event_per_line(tmp_path: Path) -> None:
    p = tmp_path / "wl.txt"
    p.write_text("AAPL\nmsft\n# comment\n\nGOOGL\n")
    src = WatchlistEventSource(p)
    events = list(src.stream())
    assert [e.ticker for e in events] == ["AAPL", "MSFT", "GOOGL"]
    assert all(e.source == "watchlist" for e in events)


def test_missing_file_yields_empty(tmp_path: Path) -> None:
    src = WatchlistEventSource(tmp_path / "missing.txt")
    assert list(src.stream()) == []
