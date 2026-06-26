from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResearchUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    provider: str
    external_user_id: str
    external_chat_id: str | None = None
    display_name: str | None = None
    timezone: str = "UTC"
    locale: str = "en"
    created_at: datetime
    updated_at: datetime


class ResearchWatchlistItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    user_id: int
    ticker: str
    source: str = "user"
    notes: str = ""
    enabled: bool = True
    added_at: datetime
    updated_at: datetime


class ResearchScheduledJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    user_id: int
    job_type: str
    schedule_kind: str
    schedule_spec: str
    timezone: str
    delivery_channel: str
    delivery_target: str
    enabled: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ResearchAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    occurred_at: datetime
    user_id: int | None = None
    actor_provider: str | None = None
    actor_external_id: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    ticker: str | None = None
    request_json: str | None = None
    result_json: str | None = None

