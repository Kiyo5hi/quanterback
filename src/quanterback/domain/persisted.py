"""Persistence-layer DTOs. These shapes are what StateStore reads/writes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScanRun(BaseModel):
    model_config = ConfigDict(frozen=False)  # mutable for end-time update
    id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    source: str
    trigger_label: str = ""
    tickers_processed: int = 0
    errors_count: int = 0


class PersistedDecision(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    scan_run_id: int
    ticker: str
    summary_json: str
    decision_json: str
    llm_model: str
    llm_usage_json: str | None = None
    rejected_reason: str | None = None
    agent_debate_json: str | None = None
    created_at: datetime


class PersistedBacktest(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    decision_id: int
    report_json: str
    passed: bool
    failed_checks: str | None = None
    created_at: datetime


class PersistedOrder(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    decision_id: int
    backtest_id: int
    bracket_spec_json: str
    alpaca_order_id: str | None = None
    submitted_at: datetime
    dry_run: bool = False
    raw_response_json: str | None = None


class PersistedPosition(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    ticker: str
    order_id: int
    state: Literal["pending", "filled", "bracket_active", "closed"]
    entry_price: float | None = None
    sl: float | None = None
    tp: float | None = None
    qty: int | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    exit_reason: str | None = None
    decision_id: int | None = None


class PersistedNotification(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    event_kind: str
    payload_json: str
    sent_at: datetime | None = None
    sent_ok: bool = False
    retry_count: int = 0
    error: str | None = None


class PersistedUserTrigger(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    ticker: str
    actor: str
    requested_at: datetime
    state: Literal["pending", "processed"] = "pending"
    processed_at: datetime | None = None


class PersistedTrade(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    exit_order_id: str
    ticker: str
    side: Literal["LONG", "SHORT"] = "LONG"
    qty: float
    entry_price: float
    entry_at: datetime
    exit_price: float
    exit_at: datetime
    exit_reason: str
    pnl_usd: float
    pnl_pct: float
    holding_hours: float
    decision_id: int | None = None
    created_at: datetime | None = None
