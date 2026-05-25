"""Aggregating TG notifier — buffer events during a scan, send one
formatted summary on scan_summary event.

Rationale: a 12-ticker scan emits ~18 NotificationEvents (decision per
ticker + backtest per BUY + order per pass + summary). Per-event TG
messages overwhelm the chat. This adapter collects all events and
flushes a single grouped message at the natural scan-completion point.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import requests

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent
from quanterback.domain.persisted import PersistedNotification
from quanterback.i18n import I18n

log = logging.getLogger(__name__)


class BufferedTelegramNotifier:
    """Buffers events; flushes one aggregated message on scan_summary.

    Implements the Notifier protocol (push). Internal:
    - non-summary events appended to buffer
    - scan_summary triggers flush (composes one message, sends, clears)
    """

    def __init__(
        self, *, token: str, chat_ids: tuple[str, ...], store: SqliteStore, i18n: I18n,
    ) -> None:
        self._token = token
        self._chat_ids = chat_ids
        self._store = store
        self._i18n = i18n
        self._endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        self._buffer: list[NotificationEvent] = []

    def push(self, event: NotificationEvent) -> None:
        self._buffer.append(event)
        if event.kind == "scan_summary":
            self._flush()

    def discard_buffer(self) -> int:
        """Clear buffered events without sending.

        Used by pipeline when source='user_trigger' — control-bot already
        sent the rich brief, so we don't want a duplicate scan_summary,
        but we also can't leave the per-event buffer accumulating across
        scans (would leak into next cron scan's flush).
        """
        n = len(self._buffer)
        self._buffer.clear()
        return n

    # ---------- internals ----------

    def _flush(self) -> None:
        try:
            text = self._compose(self._buffer)
        except Exception as e:
            log.warning("aggregate compose failed: %s", e)
            self._buffer.clear()
            return
        nid = self._store.insert_notification(PersistedNotification(
            event_kind="scan_aggregated",
            payload_json=json.dumps({"count": len(self._buffer)}),
        ))
        all_ok = True
        last_error: str | None = None
        for chat_id in self._chat_ids:
            try:
                r = requests.post(
                    self._endpoint,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
                if r.status_code >= 300:
                    all_ok = False
                    last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:
                all_ok = False
                last_error = str(e)
                log.warning("aggregated TG send failed: %s", e)
        existing = self._store.query_pending_notifications()
        match = next((p for p in existing if p.id == nid), None)
        if match is not None:
            match.sent_at = datetime.now(tz=timezone.utc)
            match.sent_ok = all_ok
            match.error = None if all_ok else last_error
            match.retry_count = 0 if all_ok else 1
            self._store.update_notification(match)
        self._buffer.clear()

    def _compose(self, buffer: list[NotificationEvent]) -> str:
        """Group events into a single Markdown summary."""
        # Find scan_summary for header
        summary = next((e for e in buffer if e.kind == "scan_summary"), None)
        ts = (summary.timestamp if summary else datetime.now(tz=timezone.utc)
              ).strftime("%Y-%m-%d %H:%M UTC")

        by_kind: dict[str, list[NotificationEvent]] = defaultdict(list)
        for e in buffer:
            by_kind[e.kind].append(e)

        # Build context for template
        mode_text = "🟡 dry-run" if (summary and (summary.payload or {}).get("dry_run")) else "🟢 live"

        orders_submitted = [
            {"ticker": (e.payload or {}).get("ticker", "?"),
             "order_id": (e.payload or {}).get("order_id") or "—"}
            for e in by_kind.get("order", [])
            if (e.payload or {}).get("submitted")
        ]
        dry_run_orders = [
            {"ticker": (e.payload or {}).get("ticker", "?")}
            for e in by_kind.get("order", [])
            if (e.payload or {}).get("dry_run")
        ]

        backtests = by_kind.get("backtest", [])
        backtests_passed = [
            (e.payload or {}).get("ticker", "?")
            for e in backtests
            if (e.payload or {}).get("passed")
        ]
        backtests_failed = [
            {"ticker": (e.payload or {}).get("ticker", "?"),
             "failed_checks": (e.payload or {}).get("failed_checks") or []}
            for e in backtests
            if not (e.payload or {}).get("passed")
        ]

        decisions = by_kind.get("decision", [])
        buys = []
        passes = []
        rejected = []
        for e in decisions:
            p = e.payload or {}
            action = p.get("action", "?")
            t = p.get("ticker", "?")
            if action == "BUY":
                buys.append(t)
            elif action == "PASS":
                passes.append(t)
            else:
                rejected.append({"ticker": t, "reason": str(p.get("reason", "—"))})

        errors_list = [
            {"ticker": (e.payload or {}).get("ticker", "?"),
             "error": str((e.payload or {}).get("error", ""))}
            for e in by_kind.get("error", [])
        ]

        p = summary.payload or {} if summary else {}
        processed = p.get("processed", 0)
        errors = p.get("errors", 0)

        return self._i18n.render(
            "scan_summary",
            timestamp=ts, processed=processed,
            errors=errors, mode_text=mode_text,
            orders_submitted=orders_submitted,
            dry_run_orders=dry_run_orders,
            backtests_passed=backtests_passed,
            backtests_failed=backtests_failed,
            buys=buys, passes=passes, rejected=rejected,
            errors_list=errors_list,
        )
