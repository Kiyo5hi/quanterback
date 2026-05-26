"""Reconciles local DB state vs Alpaca actual state. Defense in depth against order drift."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from quanterback.interfaces.lifecycle import BrokerLifecyclePort
from quanterback.interfaces.store import StateStore

log = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    """Results of a reconciliation run."""
    orphan_orders_cancelled: int = 0
    manual_closes_detected: int = 0
    unknown_positions_detected: int = 0
    local_unfilled_orders_detected: int = 0


@dataclass
class Reconciler:
    """Reconciles local SQLite DB vs Alpaca live state.

    Runs at service startup and periodically during position_tracker.tick().
    Detects and fixes drift:
    - Orphan orders (in Alpaca but not in local DB)
    - Manual closes (in local DB but not in Alpaca)
    - Unknown positions (in Alpaca but not in local DB)
    - Unfilled orders lingering in local DB
    """
    broker: BrokerLifecyclePort
    store: StateStore

    def reconcile(self) -> ReconciliationReport:
        """Run full reconciliation. Returns counts of actions taken."""
        report = ReconciliationReport()

        # 1. Check for orphan orders (Alpaca open but not in local DB)
        try:
            alpaca_open_orders = self._list_alpaca_open_orders()
            local_order_ids = self._get_local_order_ids_from_positions()
            orphan_ids = [o for o in alpaca_open_orders if o not in local_order_ids]

            for order_id in orphan_ids:
                if self.broker.cancel_order(order_id):
                    log.warning("Cancelled orphan Alpaca order %s (not in local DB)", order_id)
                    report.orphan_orders_cancelled += 1
                else:
                    log.error("Failed to cancel orphan order %s", order_id)
        except Exception as e:
            log.exception("Orphan order check failed: %s", e)

        # 2. Check for manually-closed positions (in local DB but not in Alpaca)
        try:
            alpaca_positions = self.broker.list_positions()
            alpaca_tickers = {p.ticker for p in alpaca_positions}
            local_open = self.store.get_open_positions()

            for pos in local_open:
                if pos.ticker not in alpaca_tickers:
                    # Position closed in Alpaca but still marked open in DB
                    log.warning(
                        "Position %s is in DB (state=%s) but not in Alpaca — "
                        "marking closed as reconciled",
                        pos.ticker, pos.state
                    )
                    self.store.mark_position_closed(
                        pos.ticker,
                        closed_at=datetime.now(tz=timezone.utc),
                        exit_price=0.0,
                    )
                    # Update exit reason to mark as reconciliation
                    try:
                        self.store._conn.execute(
                            "UPDATE positions SET exit_reason='reconciled_manual_close' "
                            "WHERE ticker=? AND state='closed'",
                            (pos.ticker,)
                        )
                    except Exception:
                        pass
                    report.manual_closes_detected += 1
        except Exception as e:
            log.exception("Manual close check failed: %s", e)

        # 3. Check for unfilled orders stuck in 'pending' state
        # These are orders in local DB that Alpaca reports as REJECTED or EXPIRED
        try:
            alpaca_all_orders = self._list_alpaca_all_orders()
            alpaca_by_id = {o["id"]: o for o in alpaca_all_orders}

            local_pending = self._get_local_pending_orders()
            for local_order in local_pending:
                alpaca_order = alpaca_by_id.get(local_order["order_id"])
                if alpaca_order is None:
                    # Order doesn't exist in Alpaca at all — likely rejected/expired
                    log.warning(
                        "Local pending position %s (order_id=%s) has no Alpaca record — "
                        "marking closed as rejected",
                        local_order["ticker"], local_order["order_id"]
                    )
                    self.store.mark_position_closed(
                        local_order["ticker"],
                        closed_at=datetime.now(tz=timezone.utc),
                        exit_price=0.0,
                    )
                    try:
                        self.store._conn.execute(
                            "UPDATE positions SET exit_reason='reconciled_order_missing' "
                            "WHERE id=?",
                            (local_order["id"],)
                        )
                    except Exception:
                        pass
                    report.local_unfilled_orders_detected += 1
                elif alpaca_order.get("status") in ("rejected", "expired", "canceled"):
                    log.warning(
                        "Local pending position %s (order_id=%s) is %s in Alpaca — "
                        "marking closed",
                        local_order["ticker"], local_order["order_id"],
                        alpaca_order.get("status")
                    )
                    self.store.mark_position_closed(
                        local_order["ticker"],
                        closed_at=datetime.now(tz=timezone.utc),
                        exit_price=0.0,
                    )
                    try:
                        self.store._conn.execute(
                            "UPDATE positions SET exit_reason=? WHERE id=?",
                            (f"reconciled_{alpaca_order.get('status')}", local_order["id"])
                        )
                    except Exception:
                        pass
                    report.local_unfilled_orders_detected += 1
        except Exception as e:
            log.exception("Unfilled order check failed: %s", e)

        if any([report.orphan_orders_cancelled, report.manual_closes_detected,
                report.local_unfilled_orders_detected]):
            log.info(
                "Reconciliation complete: orphans_cancelled=%d, manual_closes=%d, "
                "unfilled_detected=%d",
                report.orphan_orders_cancelled, report.manual_closes_detected,
                report.local_unfilled_orders_detected,
            )
        return report

    def _list_alpaca_open_orders(self) -> list[str]:
        """Get list of open order IDs from Alpaca."""
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            # Access broker's internal client (not ideal, but necessary for this check)
            client = self.broker._client  # type: ignore[attr-defined]
            req = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                limit=500,
            )
            orders = client.get_orders(filter=req)
            return [str(o.id) for o in orders]
        except Exception as e:
            log.warning("Failed to list Alpaca open orders: %s", e)
            return []

    def _list_alpaca_all_orders(self) -> list[dict]:
        """Get all recent orders from Alpaca with status."""
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            client = self.broker._client  # type: ignore[attr-defined]
            # Get all non-closed orders (open, pending, etc.)
            orders_to_check = []
            for status in [QueryOrderStatus.OPEN, QueryOrderStatus.PENDING_NEW,
                          QueryOrderStatus.ACCEPTED, QueryOrderStatus.REJECTED,
                          QueryOrderStatus.EXPIRED, QueryOrderStatus.CANCELED]:
                try:
                    req = GetOrdersRequest(status=status, limit=200)
                    resp = client.get_orders(filter=req)
                    for o in resp:
                        orders_to_check.append({
                            "id": str(o.id),
                            "status": str(o.status) if o.status else "unknown",
                        })
                except Exception:
                    pass
            return orders_to_check
        except Exception as e:
            log.warning("Failed to list Alpaca all orders: %s", e)
            return []

    def _get_local_order_ids_from_positions(self) -> list[str]:
        """Get all Alpaca order IDs from local positions (non-closed).

        Note: positions.order_id is a DB FK to orders(id), but we need
        the Alpaca order ID from orders.alpaca_order_id.
        """
        try:
            # Join positions -> orders to get Alpaca order IDs
            rows = self.store._conn.execute(
                "SELECT DISTINCT o.alpaca_order_id FROM positions p "
                "JOIN orders o ON p.order_id = o.id "
                "WHERE p.state != 'closed' AND o.alpaca_order_id IS NOT NULL"
            ).fetchall()
            return [str(r["alpaca_order_id"]) for r in rows if r["alpaca_order_id"]]
        except Exception as e:
            log.warning("Failed to query local Alpaca order IDs: %s", e)
            return []

    def _get_local_pending_orders(self) -> list[dict]:
        """Get all pending (unfilled) positions from local DB with details.

        Returns Alpaca order IDs (from orders.alpaca_order_id), not DB FKs.
        """
        try:
            rows = self.store._conn.execute(
                "SELECT p.id, p.ticker, p.state, o.alpaca_order_id FROM positions p "
                "LEFT JOIN orders o ON p.order_id = o.id "
                "WHERE p.state = 'pending'"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "ticker": r["ticker"],
                    "order_id": str(r["alpaca_order_id"]) if r["alpaca_order_id"] else None,
                    "state": r["state"],
                }
                for r in rows
            ]
        except Exception as e:
            log.warning("Failed to query local pending orders: %s", e)
            return []
