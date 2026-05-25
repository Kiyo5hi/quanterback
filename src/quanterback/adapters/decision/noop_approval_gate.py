from __future__ import annotations

from quanterback.domain.decision import StrategyDecision
from quanterback.interfaces.decision import ApprovalResult


class NoOpApprovalGate:
    """v0 ApprovalGate that always approves. v1 replaces with TelegramApprovalGate."""

    def review(self, decision: StrategyDecision) -> ApprovalResult:
        return ApprovalResult(approved=True, reason="noop", approver=None)
