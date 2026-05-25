from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TradeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    return_pct: float
    bars_held: int = Field(ge=0)
    exit_reason: Literal["stop_loss", "take_profit", "timeout"]


class BacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    strategy: str
    params: dict
    lookback_years: int = Field(default=3, ge=1, le=10)


class BacktestReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    strategy: str
    params: dict
    period_start: date
    period_end: date
    num_trades: int = Field(ge=0)
    win_rate: float = Field(ge=0, le=1)
    max_drawdown: float = Field(ge=0, le=1)
    sharpe: float
    profit_factor: float = Field(ge=0)
    cumulative_return: float
    avg_trade_return: float
    avg_bars_held: float = Field(ge=0)
    trades: list[TradeRecord]
    # Relative-to-buy-and-hold
    buy_and_hold_return: float = 0.0
    buy_and_hold_max_drawdown: float = 0.0
    excess_return: float = 0.0
    drawdown_ratio: float = 0.0
    # Walk-forward OOS metrics (last 33% of bars)
    oos_num_trades: int = 0
    oos_win_rate: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_sharpe: float = 0.0
    oos_cumulative_return: float = 0.0
    oos_excess_return: float = 0.0
