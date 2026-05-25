from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent
from quanterback.domain.persisted import PersistedNotification

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Fire-and-forget Telegram notifier. Persists every event for retry."""

    def __init__(self, *, token: str, chat_ids: tuple[str, ...], store: SqliteStore) -> None:
        self._token = token
        self._chat_ids = chat_ids
        self._store = store
        self._endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    def push(self, event: NotificationEvent) -> None:
        nid = self._store.insert_notification(PersistedNotification(
            event_kind=event.kind, payload_json=json.dumps(event.payload),
        ))
        text = self._render(event)
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
                log.warning("Telegram push failed: %s", e)

        existing = self._store.query_pending_notifications()
        match = next((p for p in existing if p.id == nid), None)
        if match is None:
            return
        match.sent_at = datetime.now(tz=timezone.utc)
        match.sent_ok = all_ok
        match.error = None if all_ok else last_error
        match.retry_count = 0 if all_ok else 1
        self._store.update_notification(match)

    @staticmethod
    def _render(event: NotificationEvent) -> str:
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        payload = event.payload or {}
        kind = event.kind

        if kind == "decision":
            ticker = payload.get("ticker", "?")
            action = payload.get("action", "?")
            emoji = {"BUY": "🟢", "PASS": "⚪", "REJECTED": "🚫"}.get(action, "❔")
            rationale = (payload.get("rationale") or "")
            rationale_short = rationale if len(rationale) <= 400 else rationale[:400] + "..."
            body = f"{emoji} *{ticker}* — {action}\n_{ts}_\n\n{rationale_short}"
            reason = payload.get("reason")
            if reason:
                body += f"\n\n*Reason:* {reason}"
            return body

        if kind == "backtest":
            ticker = payload.get("ticker", "?")
            passed = payload.get("passed")
            emoji = "✅" if passed else "❌"
            failed = payload.get("failed_checks") or []
            body = f"{emoji} Backtest — *{ticker}*\n_{ts}_"
            if not passed and failed:
                body += f"\nFailed: `{', '.join(failed)}`"
            return body

        if kind == "order":
            ticker = payload.get("ticker", "?")
            submitted = payload.get("submitted")
            dry = payload.get("dry_run", False)
            oid = payload.get("order_id") or "—"
            if dry:
                emoji = "🟡"
                label = "Dry-run (frozen mode)"
            elif submitted:
                emoji = "📤"
                label = f"Submitted: `{oid}`"
            else:
                emoji = "⚠️"
                label = "Not submitted"
            return f"{emoji} Order — *{ticker}*\n_{ts}_\n{label}"

        if kind == "fill":
            ticker = payload.get("ticker", "?")
            json_text = json.dumps(payload, ensure_ascii=False, indent=2)[:600]
            return f"💰 Fill — *{ticker}*\n_{ts}_\n```\n{json_text}\n```"

        if kind == "scan_summary":
            processed = payload.get("processed", 0)
            errors = payload.get("errors", 0)
            dry = payload.get("dry_run", False)
            mode = "🟡 dry-run" if dry else "🟢 live"
            return (f"📋 Scan summary\n_{ts}_\n"
                    f"{mode} · processed *{processed}* · errors *{errors}*")

        if kind == "error":
            ticker = payload.get("ticker", "?")
            err = (payload.get("error") or "")[:500]
            return f"⚠️ *{ticker}* — error\n_{ts}_\n\n```\n{err}\n```"

        # Unknown kind — fall back to compact JSON
        json_text = json.dumps(payload, ensure_ascii=False, indent=2)[:800]
        return f"*{kind}*\n_{ts}_\n```\n{json_text}\n```"
