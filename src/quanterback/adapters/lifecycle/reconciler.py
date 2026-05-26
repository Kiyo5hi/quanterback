"""Reconciles local DB state vs Alpaca actual state. Defense in depth against order drift."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
        # Only fires for positions in actively-held states (bracket_active / filled / open)
        # AND only if we successfully also queried open orders — partial data collection
        # must not cause aggressive closes (would race with brand-new submits that haven't
        # appeared in Alpaca's positions list yet).
        try:
            alpaca_positions = self.broker.list_positions()
            alpaca_tickers = {p.ticker for p in alpaca_positions}
            local_open = self.store.get_open_positions()
            # Active states only — pending positions are handled by step #3 (order-level)
            # and by cleanup_stale_pendings, not by this manual-close heuristic.
            HELD_STATES = ("bracket_active", "filled", "open")
            # Grace period: don't close positions opened less than N minutes ago — Alpaca
            # has a fill-propagation delay; a 30s-old pending might not be in positions yet.
            MIN_AGE_BEFORE_RECONCILE = timedelta(minutes=10)
            now = datetime.now(tz=timezone.utc)

            # If a ticker has a recent broker exit order, the position likely closed
            # via SL/TP — PositionTracker will record the real fill. Reconciler's
            # "manual_close" path would zero out exit_price, so skip those tickers.
            recent_exit_tickers: set[str] = set()
            try:
                lookback = now - timedelta(hours=24)
                for o in self.broker.list_orders_after(lookback):
                    side = getattr(o, "side", "")
                    filled_qty = getattr(o, "filled_qty", 0) or 0
                    ticker = getattr(o, "ticker", "")
                    if side == "sell" and filled_qty > 0 and ticker:
                        recent_exit_tickers.add(str(ticker).upper())
            except Exception:
                pass

            for pos in local_open:
                if pos.state not in HELD_STATES:
                    continue
                if pos.opened_at and (now - pos.opened_at) < MIN_AGE_BEFORE_RECONCILE:
                    continue
                if pos.ticker in alpaca_tickers:
                    continue
                if pos.ticker.upper() in recent_exit_tickers:
                    continue
                log.warning(
                    "Position %s is in DB (state=%s, opened %s ago) but not in "
                    "Alpaca — marking closed as reconciled",
                    pos.ticker, pos.state,
                    (now - pos.opened_at) if pos.opened_at else "unknown"
                )
                self.store.mark_position_closed(
                    pos.ticker,
                    closed_at=datetime.now(tz=timezone.utc),
                    exit_price=0.0,
                )
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

        # 3. Check for unfilled orders that Alpaca EXPLICITLY rejected/expired.
        # IMPORTANT: do NOT auto-close on "missing from Alpaca list" — Alpaca's
        # GET /orders has eventual consistency (a just-submitted order can take
        # several seconds to appear). The previous version of this check killed
        # legitimate just-submitted positions in <2s, causing duplicate orders
        # (real incident 2026-05-26: BB submitted twice, $10k vs $5k intended
        # exposure). Now only close on EXPLICIT terminal status from Alpaca.
        # Also: skip positions younger than a grace period.
        try:
            alpaca_all_orders = self._list_alpaca_all_orders()
            alpaca_by_id = {o["id"]: o for o in alpaca_all_orders}
            now = datetime.now(tz=timezone.utc)
            GRACE_PERIOD = timedelta(minutes=5)

            local_pending = self._get_local_pending_orders()
            for local_order in local_pending:
                # Skip if too young — Alpaca propagation window
                opened_at = local_order.get("opened_at")
                if opened_at and (now - opened_at) < GRACE_PERIOD:
                    continue
                alpaca_order = alpaca_by_id.get(local_order["order_id"])
                # Only act on EXPLICIT terminal statuses, NOT on missing.
                if alpaca_order is None:
                    continue  # eventual consistency: leave it for cleanup_stale_pendings
                if alpaca_order.get("status") in ("rejected", "expired", "canceled"):
                    log.warning(
                        "Local pending position %s (order_id=%s) is %s in Alpaca — "
                        "marking closed",
                        local_order["ticker"], local_order["order_id"],
                        alpaca_order.get("status")
                    )
                    self.store.mark_position_closed(
                        local_order["ticker"],
                        closed_at=now,
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
            orders = self.broker.list_all_orders(status="open")
            return [str(o["id"]) for o in orders]
        except Exception as e:
            log.warning("Failed to list Alpaca open orders: %s", e)
            return []

    def _list_alpaca_all_orders(self) -> list[dict]:
        """Get all recent orders from Alpaca with status."""
        try:
            # Call the Protocol method which handles Alpaca API details
            return self.broker.list_all_orders()
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
        Includes opened_at so callers can apply propagation grace period.
        """
        try:
            rows = self.store._conn.execute(
                "SELECT p.id, p.ticker, p.state, p.opened_at, o.alpaca_order_id "
                "FROM positions p "
                "LEFT JOIN orders o ON p.order_id = o.id "
                "WHERE p.state = 'pending'"
            ).fetchall()
            out = []
            for r in rows:
                opened_at = None
                if r["opened_at"]:
                    try:
                        opened_at = datetime.fromisoformat(r["opened_at"])
                    except Exception:
                        opened_at = None
                out.append({
                    "id": r["id"],
                    "ticker": r["ticker"],
                    "order_id": str(r["alpaca_order_id"]) if r["alpaca_order_id"] else None,
                    "state": r["state"],
                    "opened_at": opened_at,
                })
            return out
        except Exception as e:
            log.warning("Failed to query local pending orders: %s", e)
            return []
