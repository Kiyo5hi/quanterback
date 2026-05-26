"""Position lifecycle tracker.

Each tick:
1. Fetch positions + recent filled orders from Alpaca.
2. Diff against last known persisted state.
3. Classify each closed position's exit reason from the closing order's type.
4. Compute P&L (USD + %), holding hours.
5. Persist Trade row, mark position closed, emit notification.

Idempotent: same orders can be processed across reruns; we skip ones whose
trade row already exists (uniqueness keyed by exit order id).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from quanterback.domain.persisted import PersistedTrade
from quanterback.domain.trade import ExitReason
from quanterback.i18n import I18n
from quanterback.interfaces.lifecycle import (
    BrokerLifecyclePort,
    OrderSnapshot,
)
from quanterback.interfaces.notify import Notifier
from quanterback.interfaces.store import StateStore

log = logging.getLogger(__name__)


@dataclass
class PositionTracker:
    broker: BrokerLifecyclePort
    store: StateStore
    notifier: Notifier
    i18n: I18n
    lookback_hours: int = 48

    def tick(self) -> dict:
        """Run one lifecycle tracking cycle. Idempotent."""
        # Run reconciliation first to catch any drift from previous runs
        from quanterback.adapters.lifecycle.reconciler import Reconciler
        reconciler = Reconciler(broker=self.broker, store=self.store)
        recon_report = reconciler.reconcile()

        now = datetime.now(tz=timezone.utc)
        after = now - timedelta(hours=self.lookback_hours)

        # Reap zombie pendings (Alpaca never confirmed fill within 1h).
        # Now we cancel Alpaca-side orders before marking closed.
        cleaned = self._cleanup_stale_pendings_with_cancel(max_age_hours=1.0)
        if cleaned:
            log.info("Reaped %d stale pending positions (>1h, no fill)", cleaned)

        positions = self.broker.list_positions()
        orders = self.broker.list_orders_after(after)

        open_now = {p.ticker: p for p in positions}
        prior_open = {p.ticker: p for p in self.store.get_open_positions()}

        opens_detected = self._detect_opens(open_now, prior_open, orders)
        closes_detected = self._detect_closes(open_now, prior_open, orders)

        for op in opens_detected:
            self._handle_open(op)
        for cl in closes_detected:
            self._handle_close(cl)

        return {
            "opens": len(opens_detected),
            "closes": len(closes_detected),
            "open_positions": len(open_now),
            "pendings_reaped": cleaned,
            "reconciliation": {
                "orphans_cancelled": recon_report.orphan_orders_cancelled,
                "manual_closes": recon_report.manual_closes_detected,
                "unfilled_detected": recon_report.local_unfilled_orders_detected,
            },
        }

    def _detect_opens(
        self, now_pos: dict, prior_pos: dict, orders: list[OrderSnapshot]
    ) -> list[dict]:
        """Tickers in now_pos but not in prior_pos = newly opened."""
        new_tickers = set(now_pos.keys()) - set(prior_pos.keys())
        opens = []
        for ticker in new_tickers:
            pos = now_pos[ticker]
            entry_order = self._find_entry_order(ticker, orders)
            opens.append({
                "ticker": ticker,
                "qty": pos.qty,
                "entry_price": pos.avg_entry_price,
                "entry_at": entry_order.filled_at if entry_order else datetime.now(tz=timezone.utc),
            })
        return opens

    def _detect_closes(
        self, now_pos: dict, prior_pos: dict, orders: list[OrderSnapshot]
    ) -> list[dict]:
        """Tickers in prior_pos but not in now_pos = closed."""
        gone_tickers = set(prior_pos.keys()) - set(now_pos.keys())
        closes = []
        for ticker in gone_tickers:
            prior = prior_pos[ticker]
            # Check if this is an administrative close (pending timeout or superseded).
            # Administrative close — Alpaca never filled the order (or it was
            # superseded by a newer submit), so there's NO real trade to record.
            # The DB position row exists but is purely book-keeping. Skipping both
            # the warning AND insert_trade() is correct: nothing to attribute P&L to.
            admin_exit_reasons = ("pending_timeout", "superseded_by_new_submit")
            if prior.exit_reason and prior.exit_reason in admin_exit_reasons:
                continue
            exit_order = self._find_exit_order(ticker, orders)
            if exit_order is None or exit_order.filled_avg_price is None:
                log.warning("Position %s closed but no exit order found", ticker)
                continue
            if self.store.trade_exists_for_order(exit_order.order_id):
                continue
            exit_reason = self._classify_exit_reason(exit_order)
            entry_price = prior.entry_price if prior.entry_price else 0.0
            entry_at = prior.opened_at
            qty = prior.qty if prior.qty else exit_order.filled_qty
            exit_price = exit_order.filled_avg_price
            pnl_usd = (exit_price - entry_price) * qty if entry_price > 0 else 0.0
            pnl_pct = ((exit_price / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0
            exit_at = exit_order.filled_at or datetime.now(tz=timezone.utc)
            holding_hours = (exit_at - entry_at).total_seconds() / 3600.0
            closes.append({
                "ticker": ticker,
                "qty": qty,
                "entry_price": entry_price,
                "entry_at": entry_at,
                "exit_price": exit_price,
                "exit_at": exit_at,
                "exit_reason": exit_reason,
                "exit_order_id": exit_order.order_id,
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "holding_hours": round(holding_hours, 1),
                "decision_id": prior.decision_id,
            })
        return closes

    def _find_entry_order(
        self, ticker: str, orders: list[OrderSnapshot]
    ) -> OrderSnapshot | None:
        """Find most recent filled BUY order for ticker."""
        candidates = [
            o for o in orders
            if o.ticker == ticker and o.side == "buy" and o.filled_qty > 0
        ]
        candidates.sort(
            key=lambda o: o.filled_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _find_exit_order(
        self, ticker: str, orders: list[OrderSnapshot]
    ) -> OrderSnapshot | None:
        """Find most recent filled SELL order for ticker."""
        candidates = [
            o for o in orders
            if o.ticker == ticker and o.side == "sell" and o.filled_qty > 0
        ]
        candidates.sort(
            key=lambda o: o.filled_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _classify_exit_reason(self, order: OrderSnapshot) -> ExitReason:
        """Classify exit reason from order type."""
        ot = (order.order_type or "").lower()
        if "trailing" in ot:
            return "TRAILING_STOP"
        if ot in ("stop", "stop_limit"):
            return "STOP_LOSS"
        if ot in ("limit",):
            return "TAKE_PROFIT"
        if ot in ("market",):
            return "MANUAL_CLOSE"
        return "UNKNOWN"

    def _handle_open(self, op: dict) -> None:
        """Send notification for position open."""
        try:
            msg = self.i18n.render("position_opened", **op)
            from quanterback.domain.events import NotificationEvent
            evt = NotificationEvent(
                kind="position.opened",
                payload=op,
                timestamp=datetime.now(tz=timezone.utc),
            )
            # Notifier protocol expects evt, but we have string msg
            # We'll use it as payload for now
            self.notifier.push(evt)
        except Exception as e:
            log.warning("Failed to send open notification: %s", e)

    def _handle_close(self, cl: dict) -> None:
        """Persist trade and send notification."""
        now = datetime.now(tz=timezone.utc)
        trade = PersistedTrade(
            exit_order_id=cl["exit_order_id"],
            ticker=cl["ticker"],
            qty=cl["qty"],
            entry_price=cl["entry_price"],
            entry_at=cl["entry_at"],
            exit_price=cl["exit_price"],
            exit_at=cl["exit_at"],
            exit_reason=cl["exit_reason"],
            pnl_usd=cl["pnl_usd"],
            pnl_pct=cl["pnl_pct"],
            holding_hours=cl["holding_hours"],
            decision_id=cl.get("decision_id"),
            created_at=now,
        )
        self.store.insert_trade(trade)
        self.store.mark_position_closed(cl["ticker"], cl["exit_at"], cl["exit_price"])
        # Reconcile: verify the exit order actually exists in Alpaca (defense in depth)
        # If somehow missing, log but don't fail the close
        try:
            if not self.broker.list_orders_after(cl["exit_at"] - timedelta(hours=1)):
                log.warning(
                    "Exit order %s for %s may not exist in Alpaca; marked closed anyway",
                    cl["exit_order_id"], cl["ticker"]
                )
        except Exception as e:
            log.warning("Exit order reconciliation check failed: %s", e)
        try:
            msg = self.i18n.render("position_closed", **cl)
            from quanterback.domain.events import NotificationEvent
            evt = NotificationEvent(
                kind="position.closed",
                payload=cl,
                timestamp=now,
            )
            self.notifier.push(evt)
        except Exception as e:
            log.warning("Failed to send close notification: %s", e)

    def _cleanup_stale_pendings_with_cancel(self, max_age_hours: float = 1.0) -> int:
        """Mark pending positions as closed AND cancel Alpaca orders.

        This is the safe version of cleanup_stale_pendings() that cancels
        Alpaca-side orders before marking local DB as closed.

        Returns count of rows cleaned.
        """
        from datetime import timedelta
        cutoff = (datetime.now(tz=timezone.utc)
                  - timedelta(hours=max_age_hours)).isoformat()

        # Fetch pending positions older than cutoff, with Alpaca order IDs
        # (positions.order_id is DB FK to orders(id), need orders.alpaca_order_id)
        pending = self.store._conn.execute(
            "SELECT p.id, o.alpaca_order_id FROM positions p "
            "LEFT JOIN orders o ON p.order_id = o.id "
            "WHERE p.state = 'pending' AND p.opened_at < ?",
            (cutoff,)
        ).fetchall()

        cancelled_count = 0
        for row in pending:
            alpaca_order_id = row["alpaca_order_id"]
            if alpaca_order_id and self.broker.cancel_order(str(alpaca_order_id)):
                cancelled_count += 1
            elif alpaca_order_id:
                log.warning("Failed to cancel stale order %s", alpaca_order_id)

        # Now mark them closed in DB
        cur = self.store._conn.execute(
            "UPDATE positions SET state='closed', closed_at=?, "
            "exit_reason='pending_timeout' "
            "WHERE state = 'pending' AND opened_at < ?",
            (datetime.now(tz=timezone.utc).isoformat(), cutoff),
        )
        return int(cur.rowcount or 0)
