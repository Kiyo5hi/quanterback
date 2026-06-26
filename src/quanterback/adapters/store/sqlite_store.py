from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quanterback.adapters.store.schema import apply_schema, seed_watchlist_from_config_file
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedNotification,
    PersistedOrder,
    PersistedPosition,
    PersistedTrade,
    PersistedUserTrigger,
    ScanRun,
)
from quanterback.domain.position import OpenLifecycle
from quanterback.domain.research import (
    ResearchAuditEvent,
    ResearchScheduledJob,
    ResearchUser,
    ResearchWatchlistItem,
)
from quanterback.domain.watchlist import WatchlistEntry


class SqliteStore:
    """Concrete StateStore backed by a single SQLite file. WAL mode."""

    def __init__(self, db_path: Path, watchlist_path: Path | None = None) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage txns explicitly
        )
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)
        if watchlist_path is not None:
            seed_watchlist_from_config_file(self._conn, watchlist_path)

    def close(self) -> None:
        self._conn.close()

    def insert_scan_run(self, run: ScanRun) -> int:
        cur = self._conn.execute(
            "INSERT INTO scan_runs (started_at, source, trigger_label, tickers_processed, errors_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (run.started_at.isoformat(), run.source, run.trigger_label, run.tickers_processed, run.errors_count),
        )
        return int(cur.lastrowid or 0)

    def update_scan_run(self, run: ScanRun) -> None:
        assert run.id is not None
        self._conn.execute(
            "UPDATE scan_runs SET ended_at=?, tickers_processed=?, errors_count=?, trigger_label=? WHERE id=?",
            (
                run.ended_at.isoformat() if run.ended_at else None,
                run.tickers_processed,
                run.errors_count,
                run.trigger_label,
                run.id,
            ),
        )

    # --- decisions ---
    def insert_decision(self, d: "PersistedDecision") -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions "
            "(scan_run_id, ticker, summary_json, decision_json, llm_model, "
            " llm_usage_json, rejected_reason, agent_debate_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.scan_run_id, d.ticker, d.summary_json, d.decision_json,
             d.llm_model, d.llm_usage_json, d.rejected_reason,
             d.agent_debate_json, d.created_at.isoformat()),
        )
        return int(cur.lastrowid or 0)

    def query_recent_decisions(self, ticker: str, limit: int) -> list["PersistedDecision"]:
        rows = self._conn.execute(
            "SELECT id, scan_run_id, ticker, summary_json, decision_json, "
            "llm_model, llm_usage_json, rejected_reason, agent_debate_json, created_at "
            "FROM decisions WHERE ticker=? ORDER BY created_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [
            PersistedDecision(
                id=r["id"], scan_run_id=r["scan_run_id"], ticker=r["ticker"],
                summary_json=r["summary_json"], decision_json=r["decision_json"],
                llm_model=r["llm_model"], llm_usage_json=r["llm_usage_json"],
                rejected_reason=r["rejected_reason"],
                agent_debate_json=r["agent_debate_json"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # --- backtests ---
    def insert_backtest(self, b: "PersistedBacktest") -> int:
        cur = self._conn.execute(
            "INSERT INTO backtests (decision_id, report_json, passed, failed_checks, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (b.decision_id, b.report_json, 1 if b.passed else 0,
             b.failed_checks, b.created_at.isoformat()),
        )
        return int(cur.lastrowid or 0)

    # --- orders ---
    def insert_order(self, o: "PersistedOrder") -> int:
        cur = self._conn.execute(
            "INSERT INTO orders (decision_id, backtest_id, bracket_spec_json, "
            "alpaca_order_id, submitted_at, dry_run, raw_response_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (o.decision_id, o.backtest_id, o.bracket_spec_json,
             o.alpaca_order_id, o.submitted_at.isoformat(),
             1 if o.dry_run else 0, o.raw_response_json),
        )
        return int(cur.lastrowid or 0)

    # --- notifications ---
    def insert_notification(self, n: "PersistedNotification") -> int:
        cur = self._conn.execute(
            "INSERT INTO notifications "
            "(event_kind, payload_json, sent_at, sent_ok, retry_count, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (n.event_kind, n.payload_json,
             n.sent_at.isoformat() if n.sent_at else None,
             1 if n.sent_ok else 0, n.retry_count, n.error),
        )
        return int(cur.lastrowid or 0)

    def update_notification(self, n: "PersistedNotification") -> None:
        assert n.id is not None
        self._conn.execute(
            "UPDATE notifications SET sent_at=?, sent_ok=?, retry_count=?, error=? WHERE id=?",
            (n.sent_at.isoformat() if n.sent_at else None,
             1 if n.sent_ok else 0, n.retry_count, n.error, n.id),
        )

    def query_pending_notifications(self) -> list["PersistedNotification"]:
        rows = self._conn.execute(
            "SELECT id, event_kind, payload_json, sent_at, sent_ok, retry_count, error "
            "FROM notifications WHERE sent_ok=0 ORDER BY id ASC"
        ).fetchall()
        return [
            PersistedNotification(
                id=r["id"], event_kind=r["event_kind"], payload_json=r["payload_json"],
                sent_at=datetime.fromisoformat(r["sent_at"]) if r["sent_at"] else None,
                sent_ok=bool(r["sent_ok"]), retry_count=r["retry_count"], error=r["error"],
            )
            for r in rows
        ]

    # --- positions ---
    def upsert_position(self, p: "PersistedPosition", *, broker_cancel_stale: object | None = None) -> int:
        """Upsert a position. If broker_cancel_stale is provided, cancel stale Alpaca orders.

        Args:
            p: PersistedPosition to upsert
            broker_cancel_stale: If provided, a callable(order_id: str) -> bool to cancel
                                 stale orders before marking closed in DB.
        """
        if p.id is None:
            # Supersede any stale 'pending' row for this ticker — Alpaca
            # never confirmed the previous submission, so treat as abandoned.
            # The partial UNIQUE INDEX idx_one_active_per_ticker forbids two
            # non-closed rows; this keeps the invariant under retry/race.

            # First, cancel the old Alpaca order if broker is available
            if broker_cancel_stale is not None:
                old_order_ids = self._conn.execute(
                    "SELECT order_id FROM positions WHERE ticker = ? AND state = 'pending'",
                    (p.ticker,)
                ).fetchall()
                for row in old_order_ids:
                    if row["order_id"]:
                        try:
                            broker_cancel_stale(str(row["order_id"]))
                        except Exception as e:
                            import logging
                            log_instance = logging.getLogger(__name__)
                            log_instance.warning("Failed to cancel stale order %s: %s",
                                               row["order_id"], e)

            self._conn.execute(
                "UPDATE positions SET state='closed', closed_at=?, "
                "exit_reason='superseded_by_new_submit' "
                "WHERE ticker = ? AND state = 'pending'",
                (datetime.now(timezone.utc).isoformat(), p.ticker),
            )
            cur = self._conn.execute(
                "INSERT INTO positions (ticker, order_id, state, entry_price, sl, tp, qty, "
                "opened_at, closed_at, exit_reason, decision_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.ticker, p.order_id, p.state, p.entry_price, p.sl, p.tp, p.qty,
                 p.opened_at.isoformat(),
                 p.closed_at.isoformat() if p.closed_at else None,
                 p.exit_reason, p.decision_id),
            )
            return int(cur.lastrowid or 0)
        else:
            self._conn.execute(
                "UPDATE positions SET ticker=?, order_id=?, state=?, entry_price=?, "
                "sl=?, tp=?, qty=?, opened_at=?, closed_at=?, exit_reason=?, "
                "decision_id=? WHERE id=?",
                (p.ticker, p.order_id, p.state, p.entry_price, p.sl, p.tp, p.qty,
                 p.opened_at.isoformat(),
                 p.closed_at.isoformat() if p.closed_at else None,
                 p.exit_reason, p.decision_id, p.id),
            )
            return int(p.id)

    def update_position_qty(self, ticker: str, new_qty: float) -> int:
        """Set the qty of a ticker's open position (after a partial trim).

        Without this the local row keeps the pre-trim qty, so the next scan
        re-trims against a stale qty and eventually churns/re-buys.
        Returns rows updated.
        """
        cur = self._conn.execute(
            "UPDATE positions SET qty=? WHERE ticker=? AND state != 'closed'",
            (new_qty, ticker.upper()),
        )
        self._conn.commit()
        return cur.rowcount

    def promote_pending_to_active(
        self, ticker: str, *, entry_price: float, qty: float
    ) -> int:
        """Promote a 'pending' position to 'bracket_active' once Alpaca confirms
        the fill. Fills in the real entry_price + qty from the broker snapshot.

        Returns rows updated (0 if no pending row for ticker).
        """
        cur = self._conn.execute(
            "UPDATE positions SET state='bracket_active', entry_price=?, qty=? "
            "WHERE ticker=? AND state='pending'",
            (entry_price, qty, ticker.upper()),
        )
        self._conn.commit()
        return cur.rowcount

    def has_open_lifecycle(self, ticker: str) -> bool:
        """True if any non-closed position exists for ticker."""
        return self.get_open(ticker) is not None

    def get_open(self, ticker: str) -> OpenLifecycle | None:
        """Return the OpenLifecycle for ticker if a non-closed position exists."""
        ticker = ticker.upper()
        for lc in self.query_open_lifecycles():
            if lc.ticker == ticker:
                return lc
        return None

    def query_open_lifecycles(self) -> list[OpenLifecycle]:
        # All non-closed positions including 'pending'. This is what
        # has_open_lifecycle(ticker) uses to prevent dup submits.
        # Zombie pendings (Alpaca never filled) are auto-cleaned by
        # position_tracker.cleanup_stale_pendings() every 5 min.
        rows = self._conn.execute(
            "SELECT ticker, order_id, state, opened_at FROM positions "
            "WHERE state != 'closed' AND closed_at IS NULL"
        ).fetchall()
        return [
            OpenLifecycle(
                ticker=r["ticker"], order_id=str(r["order_id"]),
                state=r["state"], opened_at=datetime.fromisoformat(r["opened_at"]),
            )
            for r in rows
        ]

    def cleanup_stale_pendings(self, max_age_hours: float = 1.0) -> int:
        """Mark 'pending' positions older than max_age_hours as closed.

        Alpaca normally fills within minutes. A 'pending' state lingering
        > max_age_hours indicates Alpaca rejected, expired, or canceled the
        order silently. Position tracker should call this each tick.

        Returns count of rows cleaned.
        """
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=max_age_hours)).isoformat()
        cur = self._conn.execute(
            "UPDATE positions SET state='closed', closed_at=?, "
            "exit_reason='pending_timeout' "
            "WHERE state = 'pending' AND opened_at < ?",
            (datetime.now(timezone.utc).isoformat(), cutoff),
        )
        return int(cur.rowcount or 0)

    # --- user triggers ---
    def insert_user_trigger(self, t: "PersistedUserTrigger") -> int:
        cur = self._conn.execute(
            "INSERT INTO user_triggers (ticker, actor, requested_at, state, processed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (t.ticker.upper(), t.actor, t.requested_at.isoformat(),
             t.state, t.processed_at.isoformat() if t.processed_at else None),
        )
        return int(cur.lastrowid or 0)

    def query_pending_user_triggers(self) -> list["PersistedUserTrigger"]:
        rows = self._conn.execute(
            "SELECT id, ticker, actor, requested_at, state, processed_at "
            "FROM user_triggers WHERE state = 'pending' ORDER BY id ASC"
        ).fetchall()
        return [
            PersistedUserTrigger(
                id=r["id"], ticker=r["ticker"], actor=r["actor"],
                requested_at=datetime.fromisoformat(r["requested_at"]),
                state=r["state"],
                processed_at=(datetime.fromisoformat(r["processed_at"])
                              if r["processed_at"] else None),
            )
            for r in rows
        ]

    def mark_user_trigger_processed(self, trigger_id: int) -> None:
        self._conn.execute(
            "UPDATE user_triggers SET state='processed', processed_at=? WHERE id=?",
            (datetime.now(tz=timezone.utc).isoformat(), trigger_id),
        )

    # --- trades ---
    def insert_trade(self, t: "PersistedTrade") -> int:
        created_at = t.created_at or datetime.now(tz=timezone.utc)
        cur = self._conn.execute(
            "INSERT INTO trades (exit_order_id, ticker, side, qty, entry_price, "
            "entry_at, exit_price, exit_at, exit_reason, pnl_usd, pnl_pct, "
            "holding_hours, decision_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (t.exit_order_id, t.ticker, t.side, t.qty, t.entry_price,
             t.entry_at.isoformat(), t.exit_price, t.exit_at.isoformat(),
             t.exit_reason, t.pnl_usd, t.pnl_pct, t.holding_hours,
             t.decision_id, created_at.isoformat()),
        )
        return int(cur.lastrowid or 0)

    def trade_exists_for_order(self, order_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM trades WHERE exit_order_id = ? LIMIT 1",
            (order_id,),
        ).fetchone()
        return row is not None

    def list_recent_trades(self, limit: int = 20) -> list["PersistedTrade"]:
        rows = self._conn.execute(
            "SELECT id, exit_order_id, ticker, side, qty, entry_price, entry_at, "
            "exit_price, exit_at, exit_reason, pnl_usd, pnl_pct, holding_hours, "
            "decision_id, created_at "
            "FROM trades ORDER BY exit_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            PersistedTrade(
                id=r["id"], exit_order_id=r["exit_order_id"], ticker=r["ticker"],
                side=r["side"], qty=r["qty"], entry_price=r["entry_price"],
                entry_at=datetime.fromisoformat(r["entry_at"]),
                exit_price=r["exit_price"], exit_at=datetime.fromisoformat(r["exit_at"]),
                exit_reason=r["exit_reason"], pnl_usd=r["pnl_usd"],
                pnl_pct=r["pnl_pct"], holding_hours=r["holding_hours"],
                decision_id=r["decision_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def get_open_positions(self) -> list["PersistedPosition"]:
        """Get all open positions."""
        rows = self._conn.execute(
            "SELECT id, ticker, order_id, state, entry_price, sl, tp, qty, "
            "opened_at, closed_at, exit_reason, decision_id "
            "FROM positions WHERE state != 'closed' "
            "ORDER BY opened_at DESC"
        ).fetchall()
        return [
            PersistedPosition(
                id=r["id"], ticker=r["ticker"], order_id=r["order_id"],
                state=r["state"], entry_price=r["entry_price"],
                sl=r["sl"], tp=r["tp"], qty=r["qty"],
                opened_at=datetime.fromisoformat(r["opened_at"]),
                closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
                exit_reason=r["exit_reason"],
                decision_id=r["decision_id"],
            )
            for r in rows
        ]

    def mark_position_closed(
        self, ticker: str, closed_at: datetime, exit_price: float
    ) -> None:
        """Mark a position as closed."""
        self._conn.execute(
            "UPDATE positions SET state='closed', closed_at=? "
            "WHERE ticker=? AND state != 'closed'",
            (closed_at.isoformat(), ticker),
        )

    # --- research users/watchlists/jobs ---
    def research_upsert_user(
        self,
        *,
        provider: str,
        external_user_id: str,
        external_chat_id: str | None = None,
        display_name: str | None = None,
        timezone_name: str = "UTC",
        locale: str = "en",
    ) -> ResearchUser:
        now = datetime.now(tz=timezone.utc)
        self._conn.execute(
            """
            INSERT INTO research_users (
              provider, external_user_id, external_chat_id, display_name,
              timezone, locale, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_user_id) DO UPDATE SET
              external_chat_id=excluded.external_chat_id,
              display_name=excluded.display_name,
              timezone=excluded.timezone,
              locale=excluded.locale,
              updated_at=excluded.updated_at
            """,
            (
                provider,
                external_user_id,
                external_chat_id,
                display_name,
                timezone_name,
                locale,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        row = self._conn.execute(
            """
            SELECT id, provider, external_user_id, external_chat_id, display_name,
                   timezone, locale, created_at, updated_at
            FROM research_users
            WHERE provider=? AND external_user_id=?
            """,
            (provider, external_user_id),
        ).fetchone()
        assert row is not None
        return self._research_user_from_row(row)

    def research_add_watchlist_item(
        self,
        user_id: int,
        ticker: str,
        *,
        source: str = "user",
        notes: str = "",
    ) -> bool:
        ticker = ticker.strip().upper()
        now = datetime.now(tz=timezone.utc)
        try:
            self._conn.execute(
                """
                INSERT INTO research_watchlist_items (
                  user_id, ticker, source, notes, enabled, added_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (user_id, ticker, source, notes, now.isoformat(), now.isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            self._conn.execute(
                """
                UPDATE research_watchlist_items
                SET enabled=1, source=?, notes=?, updated_at=?
                WHERE user_id=? AND ticker=?
                """,
                (source, notes, now.isoformat(), user_id, ticker),
            )
            return False

    def research_remove_watchlist_item(self, user_id: int, ticker: str) -> bool:
        ticker = ticker.strip().upper()
        cur = self._conn.execute(
            """
            UPDATE research_watchlist_items
            SET enabled=0, updated_at=?
            WHERE user_id=? AND ticker=? AND enabled=1
            """,
            (datetime.now(tz=timezone.utc).isoformat(), user_id, ticker),
        )
        return int(cur.rowcount or 0) > 0

    def research_list_watchlist_items(
        self, user_id: int, *, enabled_only: bool = True,
    ) -> list[ResearchWatchlistItem]:
        sql = (
            "SELECT id, user_id, ticker, source, notes, enabled, added_at, updated_at "
            "FROM research_watchlist_items WHERE user_id=?"
        )
        params: tuple[object, ...] = (user_id,)
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY ticker"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._research_watchlist_item_from_row(r) for r in rows]

    def research_create_scheduled_job(
        self,
        *,
        user_id: int,
        job_type: str,
        schedule_kind: str,
        schedule_spec: str,
        timezone_name: str,
        delivery_channel: str,
        delivery_target: str,
        next_run_at: datetime | None = None,
    ) -> int:
        now = datetime.now(tz=timezone.utc)
        cur = self._conn.execute(
            """
            INSERT INTO research_scheduled_jobs (
              user_id, job_type, schedule_kind, schedule_spec, timezone,
              delivery_channel, delivery_target, enabled, next_run_at,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                user_id,
                job_type,
                schedule_kind,
                schedule_spec,
                timezone_name,
                delivery_channel,
                delivery_target,
                next_run_at.isoformat() if next_run_at else None,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        return int(cur.lastrowid or 0)

    def research_list_scheduled_jobs(
        self, user_id: int, *, enabled_only: bool = True,
    ) -> list[ResearchScheduledJob]:
        sql = (
            "SELECT id, user_id, job_type, schedule_kind, schedule_spec, timezone, "
            "delivery_channel, delivery_target, enabled, next_run_at, last_run_at, "
            "created_at, updated_at "
            "FROM research_scheduled_jobs WHERE user_id=?"
        )
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY enabled DESC, next_run_at ASC, id ASC"
        rows = self._conn.execute(sql, (user_id,)).fetchall()
        return [self._research_job_from_row(r) for r in rows]

    def research_list_due_scheduled_jobs(
        self, now: datetime, *, limit: int = 50,
    ) -> list[ResearchScheduledJob]:
        rows = self._conn.execute(
            """
            SELECT id, user_id, job_type, schedule_kind, schedule_spec, timezone,
                   delivery_channel, delivery_target, enabled, next_run_at,
                   last_run_at, created_at, updated_at
            FROM research_scheduled_jobs
            WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC, id ASC
            LIMIT ?
            """,
            (now.isoformat(), limit),
        ).fetchall()
        return [self._research_job_from_row(r) for r in rows]

    def research_cancel_scheduled_job(self, user_id: int, job_id: int) -> bool:
        cur = self._conn.execute(
            """
            UPDATE research_scheduled_jobs
            SET enabled=0, updated_at=?
            WHERE user_id=? AND id=? AND enabled=1
            """,
            (datetime.now(tz=timezone.utc).isoformat(), user_id, job_id),
        )
        return int(cur.rowcount or 0) > 0

    def research_insert_audit_event(self, event: ResearchAuditEvent) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO research_audit_log (
              occurred_at, user_id, actor_provider, actor_external_id, action,
              entity_type, entity_id, ticker, request_json, result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.occurred_at.isoformat(),
                event.user_id,
                event.actor_provider,
                event.actor_external_id,
                event.action,
                event.entity_type,
                event.entity_id,
                event.ticker,
                event.request_json,
                event.result_json,
            ),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _research_user_from_row(row: sqlite3.Row) -> ResearchUser:
        return ResearchUser(
            id=row["id"],
            provider=row["provider"],
            external_user_id=row["external_user_id"],
            external_chat_id=row["external_chat_id"],
            display_name=row["display_name"],
            timezone=row["timezone"],
            locale=row["locale"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _research_watchlist_item_from_row(row: sqlite3.Row) -> ResearchWatchlistItem:
        return ResearchWatchlistItem(
            id=row["id"],
            user_id=row["user_id"],
            ticker=row["ticker"],
            source=row["source"],
            notes=row["notes"] or "",
            enabled=bool(row["enabled"]),
            added_at=datetime.fromisoformat(row["added_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _research_job_from_row(row: sqlite3.Row) -> ResearchScheduledJob:
        return ResearchScheduledJob(
            id=row["id"],
            user_id=row["user_id"],
            job_type=row["job_type"],
            schedule_kind=row["schedule_kind"],
            schedule_spec=row["schedule_spec"],
            timezone=row["timezone"],
            delivery_channel=row["delivery_channel"],
            delivery_target=row["delivery_target"],
            enabled=bool(row["enabled"]),
            next_run_at=(
                datetime.fromisoformat(row["next_run_at"])
                if row["next_run_at"] else None
            ),
            last_run_at=(
                datetime.fromisoformat(row["last_run_at"])
                if row["last_run_at"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # --- watchlist ---
    def list_watchlist(self) -> list[WatchlistEntry]:
        """List all tickers in the watchlist, sorted by source then ticker."""
        rows = self._conn.execute(
            "SELECT ticker, source, added_at, notes FROM watchlist "
            "ORDER BY source, ticker"
        ).fetchall()
        return [
            WatchlistEntry(
                ticker=r["ticker"],
                source=r["source"],
                added_at=datetime.fromisoformat(r["added_at"]),
                notes=r["notes"],
            )
            for r in rows
        ]

    def add_watchlist_ticker(
        self, ticker: str, *, source: str, notes: str = ""
    ) -> bool:
        """Add a ticker to watchlist. Returns True if added; False if already present."""
        ticker = ticker.upper()
        try:
            self._conn.execute(
                "INSERT INTO watchlist (ticker, source, added_at, notes) "
                "VALUES (?, ?, ?, ?)",
                (ticker, source, datetime.now(tz=timezone.utc).isoformat(), notes),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_watchlist_ticker(self, ticker: str, *, force: bool = False) -> bool:
        """Remove a ticker from watchlist. Returns True if removed; False if not found.

        Does not remove source='config' unless force=True.
        """
        ticker = ticker.upper()
        row = self._conn.execute(
            "SELECT source FROM watchlist WHERE ticker=?", (ticker,)
        ).fetchone()
        if row is None:
            return False
        if row["source"] == "config" and not force:
            return False
        self._conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
        return True

    def set_watchlist_source(self, ticker: str, source: str) -> None:
        """Change the source of a watchlist entry."""
        ticker = ticker.upper()
        self._conn.execute(
            "UPDATE watchlist SET source=? WHERE ticker=?", (source, ticker)
        )

    def prune_auto_watchlist(self, max_entries: int) -> int:
        """Keep only the newest max_entries auto-selected watchlist rows."""
        if max_entries <= 0:
            return 0
        cur = self._conn.execute(
            """
            DELETE FROM watchlist
            WHERE source = 'auto'
              AND ticker NOT IN (
                SELECT ticker FROM watchlist
                WHERE source = 'auto'
                ORDER BY added_at DESC, ticker ASC
                LIMIT ?
              )
            """,
            (max_entries,),
        )
        return int(cur.rowcount or 0)

    # --- position management decisions ---
    def insert_position_management_decision(
        self,
        *,
        scan_run_id: int,
        ticker: str,
        action: str,
        new_sl_price: float | None,
        new_qty_pct: float | None,
        reasoning: str | None,
        confidence: float | None,
        applied: bool,
    ) -> int:
        """Persist a position management decision for audit + analytics."""
        cur = self._conn.execute(
            "INSERT INTO position_management_decisions "
            "(scan_run_id, ticker, action, new_sl_price, new_qty_pct, reasoning, "
            " confidence, applied, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_run_id, ticker, action, new_sl_price, new_qty_pct,
                reasoning, confidence, 1 if applied else 0,
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        return int(cur.lastrowid or 0)
