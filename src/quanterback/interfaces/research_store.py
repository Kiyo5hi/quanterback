from __future__ import annotations

from datetime import datetime
from typing import Protocol

from quanterback.domain.research import (
    ResearchAuditEvent,
    ResearchScheduledJob,
    ResearchUser,
    ResearchWatchlistItem,
)


class ResearchStore(Protocol):
    """Persistence contract for public research/chat surfaces."""

    def research_upsert_user(
        self,
        *,
        provider: str,
        external_user_id: str,
        external_chat_id: str | None = None,
        display_name: str | None = None,
        timezone_name: str = "UTC",
        locale: str = "en",
    ) -> ResearchUser: ...

    def research_add_watchlist_item(
        self,
        user_id: int,
        ticker: str,
        *,
        source: str = "user",
        notes: str = "",
    ) -> bool: ...

    def research_remove_watchlist_item(self, user_id: int, ticker: str) -> bool: ...

    def research_list_watchlist_items(
        self, user_id: int, *, enabled_only: bool = True,
    ) -> list[ResearchWatchlistItem]: ...

    def research_create_scheduled_job(
        self,
        *,
        user_id: int,
        job_type: str,
        schedule_kind: str,
        schedule_spec: str,
        timezone_name: str,
        delivery_channel: str,
        delivery_target: str,
        next_run_at: datetime | None = None,
    ) -> int: ...

    def research_list_due_scheduled_jobs(
        self, now: datetime, *, limit: int = 50,
    ) -> list[ResearchScheduledJob]: ...

    def research_insert_audit_event(self, event: ResearchAuditEvent) -> int: ...

