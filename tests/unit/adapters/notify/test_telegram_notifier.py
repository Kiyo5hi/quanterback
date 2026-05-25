from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.notify.telegram_notifier import TelegramNotifier
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _ev() -> NotificationEvent:
    return NotificationEvent(
        kind="decision",
        payload={"ticker": "AAPL", "action": "BUY"},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )


def test_push_calls_telegram_api(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    def fake_post(url: str, json: dict, timeout: float) -> object:
        calls.append((url, json))
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post",
                         fake_post)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())
    assert len(calls) == 1
    assert "/bott/" in calls[0][0]


def test_push_failure_does_not_raise(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("network down")
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", boom)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())  # MUST NOT raise


def test_failure_increments_retry_count(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("network down")
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", boom)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())
    pending = store.query_pending_notifications()
    assert len(pending) == 1
    assert pending[0].retry_count == 1


def test_fan_out_to_multiple_chat_ids(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    def fake_post(url: str, json: dict, timeout: float) -> object:
        calls.append(json["chat_id"])
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", fake_post)
    n = TelegramNotifier(token="t", chat_ids=("a", "b", "c"), store=store)
    n.push(_ev())
    assert calls == ["a", "b", "c"]


def test_render_decision_is_formatted_not_raw_json(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    def fake_post(url: str, json: dict, timeout: float) -> object:
        captured.append(json)
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post",
                         fake_post)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(NotificationEvent(
        kind="decision",
        payload={"ticker": "AAPL", "action": "BUY", "rationale": "trend confirmed"},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    text = captured[0]["text"]
    assert "AAPL" in text
    assert "BUY" in text
    assert "🟢" in text  # emoji for BUY
    assert "trend confirmed" in text
    # No raw braces formatting (the old format wrapped payload in code fence)
    assert '"ticker": "AAPL"' not in text


def test_render_order_dry_run_distinct_emoji(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    def fake_post(url, json, timeout):
        captured.append(json)
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post",
                         fake_post)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(NotificationEvent(
        kind="order",
        payload={"ticker": "AAPL", "submitted": False, "dry_run": True},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    assert "🟡" in captured[0]["text"]
    assert "Dry-run" in captured[0]["text"]


def test_render_scan_summary_compact(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    def fake_post(url, json, timeout):
        captured.append(json)
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post",
                         fake_post)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(NotificationEvent(
        kind="scan_summary",
        payload={"processed": 5, "errors": 0, "dry_run": False},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    ))
    text = captured[0]["text"]
    assert "5" in text
    assert "errors" in text
    assert "scan summary" in text.lower()
