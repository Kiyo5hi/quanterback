from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from quanterback.domain.events import (
    ControlCommand,
    NotificationEvent,
    ScanEvent,
)


def test_scan_event_minimal() -> None:
    e = ScanEvent(
        ticker="AAPL",
        source="watchlist",
        requested_at=datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc),
    )
    assert e.ticker == "AAPL"
    assert e.priority == 0


def test_scan_event_ticker_uppercased() -> None:
    e = ScanEvent(
        ticker="aapl",
        source="watchlist",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert e.ticker == "AAPL"


def test_control_command_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ControlCommand(
            command="explode",
            actor="user1",
            received_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
        )


def test_notification_event_payload_is_dict() -> None:
    n = NotificationEvent(
        kind="decision",
        payload={"ticker": "AAPL", "action": "BUY"},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert n.payload["ticker"] == "AAPL"
