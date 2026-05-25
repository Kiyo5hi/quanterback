from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.state import SystemState

VALID_MODES = {"normal", "frozen", "halted"}


class SqliteSystemStateService:
    """Tiny adapter using the `system_state` table. Latest row wins."""

    def __init__(self, store: SqliteStore) -> None:
        self._conn = store._conn   # tight coupling is fine within a process

    def get_current(self) -> SystemState:
        row = self._conn.execute(
            "SELECT mode, reason, actor, updated_at FROM system_state "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return SystemState(
                mode="normal", updated_at=datetime.now(tz=timezone.utc),
                updated_by="bootstrap", reason=None,
            )
        return SystemState(
            mode=row["mode"], reason=row["reason"], updated_by=row["actor"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def set(self, mode: str, reason: str, actor: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode!r}")
        self._conn.execute(
            "INSERT INTO system_state (mode, reason, actor, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (mode, reason, actor, datetime.now(tz=timezone.utc).isoformat()),
        )
