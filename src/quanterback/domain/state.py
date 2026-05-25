from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SystemState(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: Literal["normal", "frozen", "halted"]
    updated_at: datetime
    updated_by: str
    reason: str | None = None
