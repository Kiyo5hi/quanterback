"""Watchlist domain model."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

WatchlistSource = Literal["config", "user", "auto"]


class WatchlistEntry(BaseModel):
    """A ticker in the watchlist."""
    ticker: str
    source: WatchlistSource
    added_at: datetime
    notes: str = ""
