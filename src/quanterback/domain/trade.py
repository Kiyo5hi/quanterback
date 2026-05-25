"""Closed-trade record."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExitReason = Literal[
    "STOP_LOSS",       # SL leg of bracket filled
    "TAKE_PROFIT",     # TP leg of bracket filled
    "TRAILING_STOP",   # Trailing stop fired
    "MANUAL_CLOSE",    # User closed via Alpaca UI / API
    "TIMEOUT",         # Closed by our timeout policy (not yet implemented)
    "UNKNOWN",         # Could not classify
]


class Trade(BaseModel):
    ticker: str
    side: Literal["LONG", "SHORT"] = "LONG"
    qty: float
    entry_price: float
    entry_at: datetime
    exit_price: float
    exit_at: datetime
    exit_reason: ExitReason
    pnl_usd: float
    pnl_pct: float
    holding_hours: float = Field(ge=0)
    decision_id: int | None = None
    notes: str = ""
