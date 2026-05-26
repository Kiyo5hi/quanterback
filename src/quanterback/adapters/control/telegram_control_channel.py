from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal

import requests

from quanterback.domain.events import ControlCommand

log = logging.getLogger(__name__)

VALID_COMMANDS = {
    "freeze", "unfreeze", "halt", "unhalt", "status", "scan", "rescan", "preview",
    "watchlist", "add", "remove",
}
CommandType = Literal[
    "freeze", "unfreeze", "halt", "unhalt", "status", "scan", "rescan", "preview",
    "watchlist", "add", "remove",
]


def parse_command(update: dict[str, Any]) -> ControlCommand | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text") or ""
    if not text.startswith("/"):
        return None
    tokens = text.split()
    head = tokens[0][1:].lower()
    if not (isinstance(head, str) and head in VALID_COMMANDS):
        return None
    actor = str(msg.get("from", {}).get("id", "unknown"))
    message_id = int(msg.get("message_id", 0))
    chat_id = str(msg.get("chat", {}).get("id", actor))

    # Parse args: for scan/preview, uppercase; for watchlist/add/remove, keep as-is
    if head in ("scan", "preview", "watchlist", "add", "remove"):
        if head in ("scan", "preview"):
            args = tuple(a.upper() for a in tokens[1:])
        else:
            args = tuple(tokens[1:])
    else:
        args = ()

    # mypy needs explicit Literal dispatch for proper type narrowing
    cmd: CommandType
    if head == "freeze":
        cmd = "freeze"
    elif head == "unfreeze":
        cmd = "unfreeze"
    elif head == "halt":
        cmd = "halt"
    elif head == "unhalt":
        cmd = "unhalt"
    elif head == "scan":
        cmd = "scan"
    elif head == "rescan":
        cmd = "rescan"
    elif head == "preview":
        cmd = "preview"
    elif head == "watchlist":
        cmd = "watchlist"
    elif head == "add":
        cmd = "add"
    elif head == "remove":
        cmd = "remove"
    else:
        cmd = "status"

    return ControlCommand(
        command=cmd,
        actor=actor,
        received_at=datetime.now(tz=timezone.utc),
        args=args,
        chat_id=chat_id,
        message_id=message_id,
    )


class TelegramControlChannel:
    """Polls Telegram getUpdates; yields parsed control commands. Blocking."""

    def __init__(
        self, *, token: str, poll_timeout: int = 25, max_iterations: int | None = None,
    ) -> None:
        self._endpoint = f"https://api.telegram.org/bot{token}/getUpdates"
        self._poll_timeout = poll_timeout
        self._last_update_id = 0
        self._max_iterations = max_iterations

    def listen(self) -> Iterable[ControlCommand]:
        iters = 0
        while True:
            if self._max_iterations is not None and iters >= self._max_iterations:
                return
            iters += 1
            try:
                resp = requests.get(
                    self._endpoint,
                    params={"offset": self._last_update_id + 1,
                            "timeout": self._poll_timeout},
                    timeout=self._poll_timeout + 10,
                )
                payload = resp.json()
            except StopIteration:
                return
            except Exception as e:
                log.warning("TG getUpdates failed: %s", e)
                continue
            if not payload.get("ok"):
                continue
            for update in payload.get("result", []):
                uid = update.get("update_id", 0)
                if uid > self._last_update_id:
                    self._last_update_id = uid
                try:
                    cmd = parse_command(update)
                except Exception as e:
                    log.warning("parse_command failed (update_id=%s): %s", uid, e)
                    continue
                if cmd is not None:
                    yield cmd
