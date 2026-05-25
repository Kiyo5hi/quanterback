from __future__ import annotations

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.domain.order import BracketOrderSpec


class ATRBracketOrderBuilder:
    """Builds a Bracket Order spec from a StrategyDecision using ATR-based SL/TP."""

    def __init__(
        self, *, sl_atr_multiple: float, tp_atr_multiple: float,
        position_size_pct: float, trail_percent: float | None = None,
    ) -> None:
        if sl_atr_multiple <= 0 or tp_atr_multiple <= 0:
            raise ValueError("ATR multiples must be positive")
        if not 0 < position_size_pct <= 1:
            raise ValueError("position_size_pct must be in (0, 1]")
        self._sl_m = sl_atr_multiple
        self._tp_m = tp_atr_multiple
        self._size_pct = position_size_pct
        self._trail_percent = trail_percent

    def build(
        self,
        decision: StrategyDecision,
        summary: CondensedSummary,
        account_value: float,
        *,
        size_multiplier: float = 1.0,
    ) -> BracketOrderSpec:
        if decision.action != "BUY":
            raise ValueError("OrderBuilder called for non-BUY decision")
        entry = summary.price.last_close
        atr = summary.volatility.atr_14
        sl = max(entry - self._sl_m * atr, 0.01)
        tp = entry + self._tp_m * atr
        dollar_size = account_value * self._size_pct * size_multiplier
        qty = max(int(dollar_size // entry), 1)
        return BracketOrderSpec(
            ticker=decision.ticker, side="buy", qty=qty,
            entry_type="market", limit_price=None,
            stop_loss_price=round(sl, 2),
            take_profit_price=round(tp, 2),
            trail_percent=self._trail_percent,
        )
