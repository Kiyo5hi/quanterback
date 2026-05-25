from __future__ import annotations

from collections import deque

import pytest

from quanterback.adapters.decision.telegram_approval_gate import TelegramApprovalGate
from quanterback.domain.decision import MomentumParams, StrategyDecision


def _decision() -> StrategyDecision:
    return StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish alignment with elevated volume confirms momentum",
        confidence=0.7,
    )


def _setup_fakes(
    monkeypatch: pytest.MonkeyPatch,
    polled_updates: list[dict],
) -> list:
    """Make requests.post a no-op; requests.get returns scripted updates."""
    sent: list = []

    def fake_post(url, json, timeout):
        sent.append(json)
        class R:
            status_code = 200
        return R()

    queue = deque(polled_updates)
    def fake_get(url, params, timeout):
        class R:
            status_code = 200
            def json(self):
                if queue:
                    return queue.popleft()
                return {"ok": True, "result": []}
        return R()

    monkeypatch.setattr(
        "quanterback.adapters.decision.telegram_approval_gate.requests.post",
        fake_post,
    )
    monkeypatch.setattr(
        "quanterback.adapters.decision.telegram_approval_gate.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "quanterback.adapters.decision.telegram_approval_gate.time.sleep",
        lambda s: None,
    )
    return sent


def test_yes_response_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        # First call: latest update probe
        {"ok": True, "result": [{"update_id": 100}]},
        # Polling: receive /yes from chat_id "42"
        {"ok": True, "result": [
            {"update_id": 101, "message": {
                "text": "/yes", "chat": {"id": 42},
                "from": {"id": 42, "username": "alice"},
            }},
        ]},
    ]
    _setup_fakes(monkeypatch, updates)
    gate = TelegramApprovalGate(
        token="t", chat_ids=("42",),
        timeout_seconds=2, poll_interval_seconds=0.1,
    )
    result = gate.review(_decision())
    assert result.approved
    assert result.approver == "42"


def test_no_response_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {"ok": True, "result": [{"update_id": 100}]},
        {"ok": True, "result": [
            {"update_id": 101, "message": {
                "text": "/no maybe later", "chat": {"id": 42},
            }},
        ]},
    ]
    _setup_fakes(monkeypatch, updates)
    gate = TelegramApprovalGate(
        token="t", chat_ids=("42",),
        timeout_seconds=2, poll_interval_seconds=0.1,
    )
    result = gate.review(_decision())
    assert not result.approved
    assert "rejected" in result.reason.lower()


def test_timeout_defaults_to_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {"ok": True, "result": [{"update_id": 100}]},
        # No subsequent matching messages
    ]
    _setup_fakes(monkeypatch, updates)
    gate = TelegramApprovalGate(
        token="t", chat_ids=("42",),
        timeout_seconds=1, poll_interval_seconds=0.1,
    )
    result = gate.review(_decision())
    assert not result.approved
    assert "timeout" in result.reason.lower()


def test_ignores_messages_from_non_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {"ok": True, "result": [{"update_id": 100}]},
        {"ok": True, "result": [
            # /yes from a chat NOT in approvers
            {"update_id": 101, "message": {
                "text": "/yes", "chat": {"id": 999},
            }},
        ]},
    ]
    _setup_fakes(monkeypatch, updates)
    gate = TelegramApprovalGate(
        token="t", chat_ids=("42",),
        timeout_seconds=1, poll_interval_seconds=0.1,
    )
    result = gate.review(_decision())
    # Should timeout because no approver responded
    assert not result.approved
    assert "timeout" in result.reason.lower()
