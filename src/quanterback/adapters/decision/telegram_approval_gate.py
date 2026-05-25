from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

from quanterback.domain.decision import StrategyDecision
from quanterback.interfaces.decision import ApprovalResult

log = logging.getLogger(__name__)


class TelegramApprovalGate:
    """Sends a Telegram message describing the decision and waits for /yes or /no.

    Blocking. Adds the configured timeout to scan latency in the worst case.
    Default behaviour on timeout = REJECT.
    """

    def __init__(
        self,
        *,
        token: str,
        chat_ids: tuple[str, ...],
        timeout_seconds: int = 60,
        poll_interval_seconds: float = 2.0,
        approver_chat_ids: tuple[str, ...] | None = None,
    ) -> None:
        self._token = token
        self._chat_ids = chat_ids
        # Approvers may be a strict subset of chat_ids (e.g. notify the group,
        # but only accept replies from the operator). Defaults to all chat_ids.
        self._approvers = approver_chat_ids or chat_ids
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._send_endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        self._get_endpoint = f"https://api.telegram.org/bot{token}/getUpdates"

    def review(self, decision: StrategyDecision) -> ApprovalResult:
        last_update_id = self._latest_update_id()
        self._send_request(decision)
        result = self._poll_for_decision(after_update_id=last_update_id)
        return result

    # ---------- helpers ----------

    def _send_request(self, decision: StrategyDecision) -> None:
        text = self._render(decision)
        for chat_id in self._chat_ids:
            try:
                requests.post(
                    self._send_endpoint,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
            except Exception as e:
                log.warning("ApprovalGate send to %s failed: %s", chat_id, e)

    def _latest_update_id(self) -> int:
        """Read current head of getUpdates so we ignore old messages."""
        try:
            r = requests.get(
                self._get_endpoint,
                params={"limit": 1, "offset": -1},
                timeout=10,
            )
            data = r.json()
            updates = data.get("result", []) if data.get("ok") else []
            if updates:
                return int(updates[-1].get("update_id", 0))
        except Exception as e:
            log.warning("ApprovalGate getUpdates probe failed: %s", e)
        return 0

    def _poll_for_decision(self, *, after_update_id: int) -> ApprovalResult:
        deadline = time.monotonic() + self._timeout
        offset = after_update_id + 1
        while time.monotonic() < deadline:
            try:
                r = requests.get(
                    self._get_endpoint,
                    params={"offset": offset, "timeout": int(self._poll_interval)},
                    timeout=int(self._poll_interval) + 5,
                )
                data = r.json()
            except Exception as e:
                log.warning("ApprovalGate poll failed: %s", e)
                time.sleep(self._poll_interval)
                continue
            if not data.get("ok"):
                time.sleep(self._poll_interval)
                continue
            for update in data.get("result", []):
                uid = update.get("update_id", 0)
                if uid >= offset:
                    offset = uid + 1
                msg = update.get("message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id not in self._approvers:
                    continue
                text = (msg.get("text") or "").strip().lower()
                if text.startswith("/yes"):
                    return ApprovalResult(
                        approved=True, reason="user approved via Telegram",
                        approver=chat_id,
                    )
                if text.startswith("/no"):
                    return ApprovalResult(
                        approved=False, reason="user rejected via Telegram",
                        approver=chat_id,
                    )
            # No matching update yet; loop again
        return ApprovalResult(
            approved=False, reason="timeout — no /yes or /no received",
            approver=None,
        )

    @staticmethod
    def _render(decision: StrategyDecision) -> str:
        params: dict | None = None
        if decision.params is not None:
            params = decision.params.model_dump()
        body = {
            "action": decision.action,
            "ticker": decision.ticker,
            "strategy": decision.strategy,
            "params": params,
            "rationale": decision.rationale,
            "confidence": decision.confidence,
        }
        ts = datetime.now(timezone.utc).isoformat()
        return (
            f"*Approval required* @ {ts}\n"
            "```\n"
            f"{json.dumps(body, indent=2, ensure_ascii=False)[:3500]}\n"
            "```\n"
            "Reply `/yes` to approve, `/no` to reject. "
            f"Default = REJECT after 60s timeout."
        )
