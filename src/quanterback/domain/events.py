from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class ScanEvent(BaseModel):
    """One unit of work coming out of an EventSource."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    source: str
    priority: int = 0
    requested_at: datetime

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class ControlCommand(BaseModel):
    """Inbound command from a ControlChannel."""

    model_config = ConfigDict(frozen=True)

    command: Literal[
        "freeze", "unfreeze", "halt", "unhalt", "status", "scan", "rescan", "preview",
        "watchlist", "add", "remove",
    ]
    actor: str
    received_at: datetime
    args: tuple[str, ...] = ()  # used by /scan TICKER1 TICKER2 ... or /watchlist add AAPL
    chat_id: str = ""           # where the message arrived (for sendMessage target)
    message_id: int = 0         # original /command msg id (for reply_to_message_id)


class NotificationEvent(BaseModel):
    """Outbound notification to be pushed via Notifier."""

    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "decision", "backtest", "order", "fill", "scan_summary", "error",
        "position.opened", "position.closed",
    ]
    payload: dict
    timestamp: datetime
