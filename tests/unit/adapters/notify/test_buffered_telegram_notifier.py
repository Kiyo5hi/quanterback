from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.notify.buffered_telegram_notifier import (
    BufferedTelegramNotifier,
)
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent
from quanterback.i18n import I18n


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


@pytest.fixture()
def i18n_en(tmp_path: Path) -> I18n:
    """Create an I18n instance with English templates."""
    templates_dir = tmp_path / "templates"
    en_dir = templates_dir / "en"
    en_dir.mkdir(parents=True)
    # Create comprehensive scan_summary template
    scan_summary_template = """📋 *Scan complete* — {{ timestamp }}
Tickers: *{{ processed }}*  Errors: *{{ errors }}*  Mode: {{ mode_text }}

{% if orders_submitted %}
🟢 *Orders submitted* ({{ orders_submitted | length }}):
{% for o in orders_submitted %}
  • *{{ o.ticker }}* — `{{ o.order_id | string | truncate(12, True, '') }}…`
{% endfor %}

{% endif %}
{% if dry_run_orders %}
🟡 *Dry-run orders* ({{ dry_run_orders | length }}):
{% for o in dry_run_orders %}
  • {{ o.ticker }}
{% endfor %}

{% endif %}
{% if backtests_passed %}
✅ *Backtest passed* ({{ backtests_passed | length }}): {{ backtests_passed | join(", ") }}

{% endif %}
{% if backtests_failed %}
❌ *Backtest rejected* ({{ backtests_failed | length }}):
{% for b in backtests_failed %}
  • *{{ b.ticker }}* — {{ b.failed_checks | join(", ") if b.failed_checks else "—" }}
{% endfor %}

{% endif %}
{% if buys %}
📈 *LLM BUY* ({{ buys | length }}): {{ buys | join(", ") }}

{% endif %}
{% if passes %}
⚪ *LLM PASS* ({{ passes | length }}): {{ passes | join(", ") | truncate(200, True, '') }}

{% endif %}
{% if rejected %}
🚫 *Rejected* ({{ rejected | length }}):
{% for r in rejected %}
{% if loop.index <= 5 %}
  • {{ r.ticker }} — {{ r.reason | truncate(60, True, '') }}
{% endif %}
{% endfor %}

{% endif %}
{% if errors_list %}
⚠️ *Errors* ({{ errors_list | length }}):
{% for e in errors_list %}
{% if loop.index <= 5 %}
  • *{{ e.ticker }}*: {{ e.error | truncate(100, True, '') }}
{% endif %}
{% endfor %}
{% endif %}
"""
    (en_dir / "scan_summary.j2").write_text(scan_summary_template)
    return I18n(language="en", templates_dir=templates_dir)


def _ts() -> datetime:
    return datetime(2026, 5, 23, 4, 0, tzinfo=timezone.utc)


def _capture_post(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float) -> object:
        sent.append(json)

        class R:
            status_code = 200
            text = "ok"

        return R()

    monkeypatch.setattr(
        "quanterback.adapters.notify.buffered_telegram_notifier.requests.post",
        fake_post,
    )
    return sent


def test_non_summary_events_do_not_send(
    store: SqliteStore, i18n_en: I18n, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    n = BufferedTelegramNotifier(token="t", chat_ids=("1",), store=store, i18n=i18n_en)
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "AAPL", "action": "PASS"},
                              timestamp=_ts()))
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "MSFT", "action": "BUY"},
                              timestamp=_ts()))
    assert sent == []  # nothing sent yet


def test_scan_summary_flushes_aggregated(
    store: SqliteStore, i18n_en: I18n, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    n = BufferedTelegramNotifier(token="t", chat_ids=("1",), store=store, i18n=i18n_en)
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "AAPL", "action": "PASS"},
                              timestamp=_ts()))
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "MSFT", "action": "BUY"},
                              timestamp=_ts()))
    n.push(NotificationEvent(kind="order",
                              payload={"ticker": "MSFT", "submitted": True,
                                       "order_id": "abc-123-456",
                                       "dry_run": False},
                              timestamp=_ts()))
    n.push(NotificationEvent(kind="scan_summary",
                              payload={"processed": 2, "errors": 0,
                                       "dry_run": False},
                              timestamp=_ts()))
    # One aggregated send
    assert len(sent) == 1
    text = sent[0]["text"]
    assert "Scan complete" in text
    assert "AAPL" in text  # PASS group
    assert "MSFT" in text  # BUY + order
    assert "Orders submitted" in text
    assert "*2*" in text  # processed count


def test_buffer_clears_after_flush(
    store: SqliteStore, i18n_en: I18n, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    n = BufferedTelegramNotifier(token="t", chat_ids=("1",), store=store, i18n=i18n_en)
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "AAPL", "action": "PASS"},
                              timestamp=_ts()))
    n.push(NotificationEvent(kind="scan_summary",
                              payload={"processed": 1, "errors": 0,
                                       "dry_run": False},
                              timestamp=_ts()))
    assert len(sent) == 1
    # Next batch
    n.push(NotificationEvent(kind="decision",
                              payload={"ticker": "NVDA", "action": "BUY"},
                              timestamp=_ts()))
    # Buffer should NOT include old AAPL — only NVDA pending
    assert len(sent) == 1  # still 1, not flushed yet
    n.push(NotificationEvent(kind="scan_summary",
                              payload={"processed": 1, "errors": 0,
                                       "dry_run": False},
                              timestamp=_ts()))
    assert len(sent) == 2
    second = sent[1]["text"]
    assert "NVDA" in second
    assert "AAPL" not in second  # cleared from buffer


def test_fanout_to_multiple_chat_ids(
    store: SqliteStore, i18n_en: I18n, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    n = BufferedTelegramNotifier(token="t",
                                  chat_ids=("a", "b", "c"), store=store, i18n=i18n_en)
    n.push(NotificationEvent(kind="scan_summary",
                              payload={"processed": 0, "errors": 0,
                                       "dry_run": False},
                              timestamp=_ts()))
    assert [s["chat_id"] for s in sent] == ["a", "b", "c"]


def test_failure_does_not_raise(
    store: SqliteStore, i18n_en: I18n, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("net down")

    monkeypatch.setattr(
        "quanterback.adapters.notify.buffered_telegram_notifier.requests.post",
        boom,
    )
    n = BufferedTelegramNotifier(token="t", chat_ids=("1",), store=store, i18n=i18n_en)
    # Should not raise
    n.push(NotificationEvent(kind="scan_summary",
                              payload={"processed": 0, "errors": 0,
                                       "dry_run": False},
                              timestamp=_ts()))
    # Notification was retried-queued in SQLite
    pending = store.query_pending_notifications()
    assert any(p.event_kind == "scan_aggregated" and p.retry_count == 1
               for p in pending)
