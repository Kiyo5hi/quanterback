from __future__ import annotations

from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.domain.decision import MomentumParams, StrategyDecision


def test_noop_gate_always_approves() -> None:
    gate = NoOpApprovalGate()
    decision = StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish setup with confirming volume profile signal",
        confidence=0.7,
    )
    result = gate.review(decision)
    assert result.approved
    assert result.reason == "noop"
