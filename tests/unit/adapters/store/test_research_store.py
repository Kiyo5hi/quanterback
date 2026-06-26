from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.research import ResearchAuditEvent


def test_research_watchlist_is_per_user(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    alice = store.research_upsert_user(
        provider="telegram", external_user_id="1", display_name="Alice",
    )
    bob = store.research_upsert_user(
        provider="telegram", external_user_id="2", display_name="Bob",
    )

    assert alice.id is not None
    assert bob.id is not None
    assert store.research_add_watchlist_item(alice.id, "nvda") is True
    assert store.research_add_watchlist_item(bob.id, "nvda") is True
    assert store.research_add_watchlist_item(alice.id, "NVDA") is False

    alice_items = store.research_list_watchlist_items(alice.id)
    bob_items = store.research_list_watchlist_items(bob.id)

    assert [i.ticker for i in alice_items] == ["NVDA"]
    assert [i.ticker for i in bob_items] == ["NVDA"]

    assert store.research_remove_watchlist_item(alice.id, "NVDA") is True
    assert store.research_remove_watchlist_item(alice.id, "NVDA") is False
    assert store.research_list_watchlist_items(alice.id) == []
    assert [i.ticker for i in store.research_list_watchlist_items(bob.id)] == ["NVDA"]


def test_research_watchlist_does_not_touch_trader_watchlist(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    user = store.research_upsert_user(provider="telegram", external_user_id="1")
    assert user.id is not None

    store.research_add_watchlist_item(user.id, "SPCX")

    assert store.list_watchlist() == []


def test_research_due_jobs_and_audit(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    user = store.research_upsert_user(provider="telegram", external_user_id="1")
    assert user.id is not None
    now = datetime.now(tz=timezone.utc)

    due_id = store.research_create_scheduled_job(
        user_id=user.id,
        job_type="digest",
        schedule_kind="daily",
        schedule_spec='{"hour": 8}',
        timezone_name="America/Los_Angeles",
        delivery_channel="telegram",
        delivery_target="chat-1",
        next_run_at=now - timedelta(minutes=1),
    )
    store.research_create_scheduled_job(
        user_id=user.id,
        job_type="digest",
        schedule_kind="daily",
        schedule_spec='{"hour": 9}',
        timezone_name="America/Los_Angeles",
        delivery_channel="telegram",
        delivery_target="chat-1",
        next_run_at=now + timedelta(hours=1),
    )

    due = store.research_list_due_scheduled_jobs(now)
    audit_id = store.research_insert_audit_event(
        ResearchAuditEvent(
            occurred_at=now,
            user_id=user.id,
            actor_provider="telegram",
            actor_external_id="1",
            action="research.job.create",
            entity_type="research_scheduled_job",
            entity_id=str(due_id),
            request_json='{"job_type": "digest"}',
            result_json='{"ok": true}',
        ),
    )

    assert [j.id for j in due] == [due_id]
    assert audit_id > 0

