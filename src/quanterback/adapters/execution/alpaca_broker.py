"""Alpaca Paper Trading broker — unified interface for execution + lifecycle."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)

# Alpaca order statuses that still hold shares (block a new SELL).
_NONTERMINAL_STATUSES = frozenset({
    "new", "accepted", "held", "partially_filled",
    "pending_new", "accepted_for_bidding", "pending_replace", "pending_cancel",
})

from quanterback.domain.order import BracketOrderSpec, ExecutionResult
from quanterback.interfaces.lifecycle import OrderSnapshot, PositionSnapshot

log = logging.getLogger(__name__)

# Module-level flag to warn once per process about trailing stop limitation.
_trail_warned_global: bool = False


@dataclass(frozen=True)
class _PositionView:
    """Concrete position snapshot from Alpaca."""
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float | None = None
    market_value: float | None = None


@dataclass(frozen=True)
class _OrderView:
    """Concrete order snapshot from Alpaca."""
    order_id: str
    ticker: str
    side: str
    qty: float
    filled_qty: float
    filled_avg_price: float | None
    status: str
    order_type: str
    submitted_at: datetime
    filled_at: datetime | None
    legs: list[dict] | None = None


class AlpacaPaperBroker:
    """Broker adapter for Alpaca Paper Trading. Bracket orders + lifecycle."""

    def __init__(self, *, api_key: str, secret: str) -> None:
        self._client: Any = TradingClient(api_key, secret, paper=True)

    def submit(self, spec: BracketOrderSpec, *, dry_run: bool, decision_id: int | None = None) -> ExecutionResult:
        if dry_run:
            return ExecutionResult(
                submitted=False, order_id=None, error=None,
                raw_response={"dry_run": True, "spec": spec.model_dump()},
            )
        order_request = self._build_request(spec, decision_id=decision_id)
        try:
            order = self._client.submit_order(order_request)

            # Check order status post-submit for immediate rejection
            status = str(getattr(order, "status", "unknown")).lower()
            if status in ("rejected", "expired", "canceled"):
                log.warning(
                    "Order %s submitted but rejected with status=%s; treating as failed",
                    order.id, status
                )
                return ExecutionResult(
                    submitted=False, order_id=None,
                    error=f"order rejected: status={status}",
                    raw_response={"id": str(order.id), "status": status},
                )

            # Note: trailing stop NOT submitted separately when bracket has stop_loss.
            # Bracket's stop_loss leg is sufficient; Alpaca rejects separate SELL orders
            # while bracket's child SELLs are open (conflicts with atomic order semantics).
            # If trailing behavior is desired, tighten SL via position_management agent instead.
            global _trail_warned_global
            if spec.trail_percent is not None and not _trail_warned_global:
                log.warning(
                    "trail_percent configured but ignored: bracket orders use "
                    "static stop_loss. Use position_management agent to tighten SL dynamically."
                )
                _trail_warned_global = True
            return ExecutionResult(
                submitted=True, order_id=str(order.id), error=None,
                raw_response={"id": str(order.id), "status": status},
            )
        except Exception as e:
            return ExecutionResult(
                submitted=False, order_id=None, error=str(e), raw_response={},
            )

    def get_account_value(self) -> float:
        acct = self._client.get_account()
        return float(acct.equity)

    def get_day_trade_count(self) -> int:
        acct = self._client.get_account()
        # Alpaca attribute name is `daytrade_count` (no underscore between day/trade).
        return int(getattr(acct, "daytrade_count", 0) or 0)

    def list_positions(self) -> list[PositionSnapshot]:
        """Fetch all open positions from Alpaca."""
        raw = self._client.get_all_positions()
        out: list[PositionSnapshot] = []
        for p in raw:
            out.append(_PositionView(  # type: ignore[arg-type]
                ticker=str(p.symbol),
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price) if p.current_price else None,
                market_value=float(p.market_value) if p.market_value else None,
            ))
        return out

    def list_orders_after(self, after: datetime) -> list[OrderSnapshot]:
        """Fetch closed orders since a given timestamp."""
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=after,
            limit=200,
        )
        raw = self._client.get_orders(filter=req)
        out: list[OrderSnapshot] = []
        for o in raw:
            side = str(o.side) if o.side else "unknown"
            # Strip enum prefix if present
            if "." in side:
                side = side.split(".")[-1]
            side = side.lower()

            order_type = str(o.order_type) if o.order_type else ""
            if "." in order_type:
                order_type = order_type.split(".")[-1]
            order_type = order_type.lower()

            out.append(_OrderView(  # type: ignore[arg-type]
                order_id=str(o.id),
                ticker=str(o.symbol),
                side=side,
                qty=float(o.qty) if o.qty else 0.0,
                filled_qty=float(o.filled_qty) if o.filled_qty else 0.0,
                filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
                status=str(o.status) if o.status else "",
                order_type=order_type,
                submitted_at=o.submitted_at,
                filled_at=o.filled_at,
                legs=None,
            ))
        return out

    def list_all_orders(
        self, status: str | None = None, after: datetime | None = None
    ) -> list[dict]:
        """Get all orders with their fine-grained status.

        Alpaca's QueryOrderStatus only supports OPEN / CLOSED / ALL — the
        fine-grained states (rejected/expired/canceled/filled/...) live on each
        order's own `.status` field. Previously this method referenced
        QueryOrderStatus.PENDING_NEW etc. which don't exist → AttributeError →
        always returned [] (silently broke reconciler order-level checks).

        Returns dicts with id, status, symbol, side keys.
        """
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
            if after:
                req.after = after
            resp = self._client.get_orders(filter=req)
            out = []
            for o in resp:
                st = str(o.status).split(".")[-1].lower() if o.status else "unknown"
                # Optional client-side filter by fine-grained status
                if status and st != status.lower():
                    continue
                side = str(o.side).split(".")[-1].lower() if o.side else "unknown"
                out.append({
                    "id": str(o.id),
                    "status": st,
                    "symbol": str(o.symbol) if o.symbol else "",
                    "side": side,
                })
            return out
        except Exception as e:
            log.warning("Failed to list all Alpaca orders: %s", e)
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order by ID. Returns True if successful."""
        try:
            self._client.cancel_order_by_id(order_id)
            log.info("Cancelled order %s", order_id)
            return True
        except Exception as e:
            log.warning("Failed to cancel order %s: %s", order_id, e)
            return False

    def _cancel_exit_legs(self, ticker: str) -> float:
        """Cancel ALL non-terminal SELL orders for a ticker (bracket SL + TP legs)
        so the held shares are freed for a new SELL.

        Queries status=ALL because Alpaca's status=OPEN filter does NOT reliably
        return ACCEPTED-state stop legs (observed 2026-05-28: SL stops sat in
        'accepted' and were invisible to the OPEN query, so they were never
        cancelled and held the shares -> EXIT_NOW/trim got 'insufficient qty').

        Returns total qty freed.
        """
        freed = 0.0
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
            for o in self._client.get_orders(filter=req):
                if str(o.symbol) != ticker:
                    continue
                side = str(o.side).split(".")[-1].lower()
                status = str(o.status).split(".")[-1].lower()
                if side != "sell" or status not in _NONTERMINAL_STATUSES:
                    continue
                try:
                    self._client.cancel_order_by_id(str(o.id))
                    freed += float(o.qty or 0)
                    log.info("Cancelled exit leg %s (%s qty=%s) for %s",
                             str(o.id)[:8], status, o.qty, ticker)
                except Exception as e:
                    log.warning("Failed to cancel exit leg %s: %s", o.id, e)
        except Exception as e:
            log.warning("Failed to list orders while cancelling exit legs for %s: %s",
                        ticker, e)
        return freed

    def market_close(self, ticker: str, qty: float | None = None) -> bool:
        """Close a position with a market sell. Cancels the bracket SL/TP legs
        first (they hold the shares), then submits the SELL.
        """
        try:
            positions = self.list_positions()
            pos = next((p for p in positions if p.ticker == ticker), None)
            if not pos:
                log.warning("No open position for %s", ticker)
                return False
            close_qty = qty if qty is not None else pos.qty

            # Free the shares: cancel exit legs holding them, then settle.
            self._cancel_exit_legs(ticker)
            time.sleep(1.5)

            req = MarketOrderRequest(
                symbol=ticker, qty=close_qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
            )
            order = self._client.submit_order(req)
            log.info("Market close for %s: %s shares, order_id=%s", ticker, close_qty, order.id)
            return True
        except Exception as e:
            log.error("Failed to market close %s: %s", ticker, e)
            return False

    def replace_stop_loss(self, ticker: str, new_sl_price: float) -> bool:
        """Find the bracket's stop_loss leg and replace it with a new stop price.

        This is a simplified approach: fetch all open orders, find the SELL stop leg
        for this ticker, cancel it, and submit a new stop order.
        """
        try:
            # Get all open orders
            open_orders = self._client.get_orders(status=QueryOrderStatus.OPEN, limit=200)

            # Find bracket's stop_loss leg (SELL order with order_class or parent)
            sl_order = None
            for order in open_orders:
                if (str(order.symbol) == ticker and
                    str(order.side).endswith("SELL") and
                    getattr(order, "order_class", None) == "bracket"):
                    # This is a bracket order. Check if it has legs.
                    legs = getattr(order, "legs", []) or []
                    for leg in legs:
                        if getattr(leg, "order_type", "").lower() == "stop" or \
                           (hasattr(leg, "stop_price") and leg.stop_price is not None):
                            sl_order = leg
                            break

            if not sl_order:
                log.warning("No stop_loss leg found for bracket order on %s", ticker)
                return False

            # Cancel the old stop_loss leg
            self.cancel_order(str(sl_order.id))

            # Submit new stop order
            positions = self.list_positions()
            pos = next((p for p in positions if p.ticker == ticker), None)
            if not pos:
                log.warning("Position closed for %s during SL replace", ticker)
                return False

            new_sl_req = MarketOrderRequest(
                symbol=ticker, qty=pos.qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=new_sl_price,
            )
            order = self._client.submit_order(new_sl_req)
            log.info("Replaced stop_loss for %s: new_price=%.2f, order_id=%s",
                     ticker, new_sl_price, order.id)
            return True
        except Exception as e:
            log.error("Failed to replace_stop_loss for %s: %s", ticker, e)
            return False

    def trim_position(
        self, ticker: str, qty_to_sell: int, sl_price: float | None = None
    ) -> bool:
        """Partially close a position by qty.

        Cancels ALL bracket exit legs (to free shares), market-sells
        qty_to_sell, then RE-ESTABLISHES a stop-loss on the remaining shares
        at sl_price (if given). Without the re-attach the remainder would sit
        unprotected, which is what happened on 2026-05-28.

        Returns True if the market-sell was submitted, False otherwise.
        """
        if qty_to_sell <= 0:
            log.warning("trim_position called with non-positive qty=%s for %s",
                        qty_to_sell, ticker)
            return False
        try:
            # Snapshot current qty so we know the remainder after the sell.
            pos = next((p for p in self.list_positions() if p.ticker == ticker), None)
            if pos is None:
                log.warning("trim_position: no Alpaca position for %s", ticker)
                return False
            remaining = int(pos.qty) - qty_to_sell

            # Free shares: cancel ALL exit legs (status=ALL catches accepted stops).
            self._cancel_exit_legs(ticker)
            time.sleep(1.5)

            order_req = MarketOrderRequest(
                symbol=ticker, qty=qty_to_sell,
                side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
            )
            order = self._client.submit_order(order_req)
            log.info("Trim order %s submitted: %s %s shares (remaining %s)",
                     order.id, ticker, qty_to_sell, remaining)

            # Re-attach a protective stop on the remainder.
            if remaining > 0 and sl_price is not None:
                time.sleep(1.5)
                try:
                    sl_req = StopOrderRequest(
                        symbol=ticker, qty=remaining, side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC, stop_price=round(sl_price, 2),
                    )
                    sl_order = self._client.submit_order(sl_req)
                    log.info("Re-attached SL for %s: %s shares @ $%.2f, order %s",
                             ticker, remaining, sl_price, str(sl_order.id)[:8])
                except Exception as e:
                    log.warning("Failed to re-attach SL on %s remainder: %s", ticker, e)
            return True
        except Exception as e:
            log.exception("Failed to trim position %s: %s", ticker, e)
            return False

    def is_market_open(self) -> bool:
        """Check if market is currently open (respects holidays, early closes)."""
        try:
            clock = self._client.get_clock()
            return bool(clock.is_open)
        except Exception as e:
            log.warning("get_clock failed, assuming market closed: %s", e)
            return False

    def next_market_open(self) -> datetime | None:
        """Get the next market open time."""
        try:
            clock = self._client.get_clock()
            return clock.next_open
        except Exception as e:
            log.warning("Failed to fetch next_open: %s", e)
            return None

    @staticmethod
    def _build_request(spec: BracketOrderSpec, decision_id: int | None = None) -> Any:
        tp = TakeProfitRequest(limit_price=spec.take_profit_price)
        sl = StopLossRequest(stop_price=spec.stop_loss_price)
        common = dict(
            symbol=spec.ticker, qty=spec.qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class="bracket", take_profit=tp, stop_loss=sl,
        )
        # Set client_order_id for idempotency: if we retry after a network failure,
        # Alpaca will deduplicate based on this ID, not create a duplicate order.
        if decision_id is not None:
            common["client_order_id"] = f"qb-{decision_id}"

        if spec.entry_type == "market":
            return MarketOrderRequest(**common)
        return LimitOrderRequest(**common, limit_price=spec.limit_price)
