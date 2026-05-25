from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BracketOrderSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    side: Literal["buy"]
    qty: int = Field(ge=1)
    entry_type: Literal["market", "limit"]
    limit_price: float | None = None
    stop_loss_price: float = Field(gt=0)
    take_profit_price: float = Field(gt=0)
    trail_percent: float | None = Field(default=None, ge=0.0, le=50.0,
        description="If set, also submit a trailing stop sell at this trail %.")

    @model_validator(mode="after")
    def _limit_requires_price(self) -> BracketOrderSpec:
        if self.entry_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required when entry_type='limit'")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    submitted: bool
    order_id: str | None
    error: str | None
    raw_response: dict
