"""Test the cmd_rescan CLI command."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quanterback.cli import cmd_rescan
from quanterback.domain.watchlist import WatchlistEntry


def test_cmd_rescan_calls_run_for_tickers(monkeypatch) -> None:
    """Verify rescan reads watchlist + calls run_for_tickers with those tickers."""
    mock_pipeline = MagicMock()
    mock_store = MagicMock()
    mock_i18n = MagicMock()
    mock_config = MagicMock()

    # Seeded watchlist with 3 entries
    now = datetime.now(tz=timezone.utc)
    watchlist = [
        WatchlistEntry(ticker="AAPL", source="user", added_at=now),
        WatchlistEntry(ticker="NVDA", source="user", added_at=now),
        WatchlistEntry(ticker="TSLA", source="auto", added_at=now),
    ]
    mock_store.list_watchlist.return_value = watchlist

    # Mock wire() to return the mocked pipeline
    monkeypatch.setattr(
        "quanterback.cli.wire",
        lambda config: (mock_pipeline, MagicMock(), MagicMock()),
    )

    # Mock _load_config
    monkeypatch.setattr("quanterback.cli._load_config", lambda: mock_config)

    # Mock SqliteStore constructor
    monkeypatch.setattr(
        "quanterback.cli.SqliteStore",
        lambda *args, **kwargs: mock_store,
    )

    # Mock I18n constructor
    monkeypatch.setattr(
        "quanterback.cli.I18n",
        lambda *args, **kwargs: mock_i18n,
    )

    # Mock render_scan_brief
    monkeypatch.setattr(
        "quanterback.brief.render_scan_brief",
        lambda *args, **kwargs: "brief output",
    )

    # Mock setup_logging
    monkeypatch.setattr("quanterback.cli._setup_logging", lambda: None)

    args = argparse.Namespace(format="brief", limit=50, dry_run=False)
    with patch("builtins.print") as mock_print:
        result = cmd_rescan(args)

    assert result == 0
    mock_pipeline.run_for_tickers.assert_called_once_with(
        ["AAPL", "NVDA", "TSLA"], trigger_label="/rescan", force_dry_run=False
    )
    mock_print.assert_called()


def test_cmd_rescan_empty_watchlist(monkeypatch) -> None:
    """Empty watchlist returns 0 + prints (watchlist is empty)."""
    mock_pipeline = MagicMock()
    mock_store = MagicMock()
    mock_store.list_watchlist.return_value = []

    monkeypatch.setattr(
        "quanterback.cli.wire",
        lambda config: (mock_pipeline, MagicMock(), MagicMock()),
    )
    monkeypatch.setattr("quanterback.cli._load_config", lambda: MagicMock())
    monkeypatch.setattr("quanterback.cli.SqliteStore", lambda *args, **kwargs: mock_store)
    monkeypatch.setattr("quanterback.cli.I18n", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr("quanterback.cli._setup_logging", lambda: None)

    args = argparse.Namespace(format="brief", limit=50, dry_run=False)
    with patch("builtins.print") as mock_print:
        result = cmd_rescan(args)

    assert result == 0
    mock_print.assert_called_with("(watchlist is empty)")
    mock_pipeline.run_for_tickers.assert_not_called()


def test_cmd_rescan_respects_limit(monkeypatch) -> None:
    """If watchlist has 100 entries but --limit 10, only 10 scanned."""
    mock_pipeline = MagicMock()
    mock_store = MagicMock()
    mock_i18n = MagicMock()
    mock_config = MagicMock()

    # Create 100 watchlist entries
    now = datetime.now(tz=timezone.utc)
    watchlist = [
        WatchlistEntry(ticker=f"SYM{i:03d}", source="user", added_at=now)
        for i in range(100)
    ]
    mock_store.list_watchlist.return_value = watchlist

    monkeypatch.setattr(
        "quanterback.cli.wire",
        lambda config: (mock_pipeline, MagicMock(), MagicMock()),
    )
    monkeypatch.setattr("quanterback.cli._load_config", lambda: mock_config)
    monkeypatch.setattr(
        "quanterback.cli.SqliteStore",
        lambda *args, **kwargs: mock_store,
    )
    monkeypatch.setattr(
        "quanterback.cli.I18n",
        lambda *args, **kwargs: mock_i18n,
    )
    monkeypatch.setattr(
        "quanterback.brief.render_scan_brief",
        lambda *args, **kwargs: "brief output",
    )
    monkeypatch.setattr("quanterback.cli._setup_logging", lambda: None)

    args = argparse.Namespace(format="brief", limit=10, dry_run=False)
    with patch("builtins.print"):
        result = cmd_rescan(args)

    assert result == 0
    # Should only call with first 10 tickers and trigger_label
    assert mock_pipeline.run_for_tickers.call_args[0][0] == [f"SYM{i:03d}" for i in range(10)]
    assert mock_pipeline.run_for_tickers.call_args[1]["trigger_label"] == "/rescan"
    assert mock_pipeline.run_for_tickers.call_args[1]["force_dry_run"] == False
