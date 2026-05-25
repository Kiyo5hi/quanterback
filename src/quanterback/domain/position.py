from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class OpenLifecycle(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    order_id: str
    state: Literal["pending", "filled", "bracket_active"]
    opened_at: datetime
