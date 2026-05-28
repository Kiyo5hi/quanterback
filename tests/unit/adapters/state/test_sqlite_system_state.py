from __future__ import annotations

from pathlib import Path

import pytest

from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore


@pytest.fixture()
def svc(tmp_path: Path) -> SqliteSystemStateService:
    return SqliteSystemStateService(SqliteStore(tmp_path / "t.sqlite"))


def test_default_state_is_normal(svc: SqliteSystemStateService) -> None:
    s = svc.get_current()
    assert s.mode == "normal"
    assert s.updated_by == "bootstrap"


def test_set_persists_state(svc: SqliteSystemStateService) -> None:
    svc.set("frozen", "manual freeze", "tg-user-1")
    s = svc.get_current()
    assert s.mode == "frozen"
    assert s.reason == "manual freeze"
    assert s.updated_by == "tg-user-1"


def test_set_invalid_mode_rejected(svc: SqliteSystemStateService) -> None:
    with pytest.raises(ValueError):
        svc.set("paused", "x", "actor")


def test_get_current_tolerates_null_actor(svc: SqliteSystemStateService) -> None:
    """A row with NULL actor (e.g. from a manual SQL UPDATE) must not crash
    get_current() — SystemState.updated_by requires str, so NULL is coerced
    to 'system'. Regression for /status going silent on 2026-05-28."""
    svc._conn.execute(
        "INSERT INTO system_state (mode, reason, actor, updated_at) "
        "VALUES ('normal', NULL, NULL, '2026-05-28T00:00:00+00:00')"
    )
    svc._conn.commit()
    s = svc.get_current()
    assert s.mode == "normal"
    assert s.updated_by == "system"
