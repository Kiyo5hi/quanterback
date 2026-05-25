from __future__ import annotations

from datetime import datetime, timezone

from quanterback.domain.position import OpenLifecycle
from quanterback.domain.state import SystemState


def test_open_lifecycle_state_literal() -> None:
    lc = OpenLifecycle(
        ticker="AAPL", order_id="abc", state="pending",
        opened_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert lc.state == "pending"


def test_system_state_defaults_to_normal() -> None:
    s = SystemState(mode="normal", updated_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
                    updated_by="bootstrap")
    assert s.mode == "normal"
