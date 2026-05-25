"""Read-only reporting over SqliteStore for the `quanterback report` CLI."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from io import StringIO

from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.failed_check_labels import humanize
from quanterback.i18n import I18n


def generate_report(store: SqliteStore, sys_state: SqliteSystemStateService, i18n: I18n) -> str:
    now = datetime.now(tz=timezone.utc)
    conn = store._conn

    # Gather all data for template
    s = sys_state.get_current()
    open_pos_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE state != 'closed'"
    ).fetchone()[0]
    bt_count = conn.execute("SELECT COUNT(*) FROM backtests").fetchone()[0]
    orders_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    # Scan runs
    scan_rows = conn.execute(
        "SELECT id, started_at, ended_at, source, tickers_processed, errors_count "
        "FROM scan_runs ORDER BY id DESC LIMIT 10"
    ).fetchall()
    scan_runs = []
    for r in scan_rows:
        started = datetime.fromisoformat(r["started_at"])
        if r["ended_at"]:
            ended = datetime.fromisoformat(r["ended_at"])
            duration_s = int((ended - started).total_seconds())
        else:
            duration_s = 0
        scan_runs.append({
            "id": r["id"],
            "started": started.strftime("%Y-%m-%d %H:%M:%S"),
            "ended": datetime.fromisoformat(r["ended_at"]).strftime("%Y-%m-%d %H:%M:%S") if r["ended_at"] else "(running)",
            "duration": duration_s,
            "source": r["source"],
            "tickers_processed": r["tickers_processed"],
            "errors_count": r["errors_count"],
        })

    # Decisions
    decision_rows = conn.execute(
        "SELECT created_at, ticker, rejected_reason, decision_json "
        "FROM decisions ORDER BY id DESC LIMIT 20"
    ).fetchall()
    decisions = []
    for r in decision_rows:
        ts = datetime.fromisoformat(r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        if r["rejected_reason"]:
            action = "REJ"
            conf = "--"
            rationale = r["rejected_reason"]
        else:
            try:
                d = json.loads(r["decision_json"])
                action = d.get("action", "?")
                conf_val = d.get("confidence")
                conf = f"{conf_val:.2f}" if isinstance(conf_val, (int, float)) else "--"
                rationale = d.get("rationale", "")
            except (json.JSONDecodeError, TypeError):
                action = "?"
                conf = "--"
                rationale = r["decision_json"] or ""
        rationale_short = _truncate(rationale.replace("\n", " "), 70)
        decisions.append({
            "ts": ts,
            "ticker": r["ticker"],
            "action": action,
            "conf": conf,
            "rationale_short": rationale_short,
        })

    # Action distribution
    action_rows = conn.execute(
        "SELECT rejected_reason, decision_json FROM decisions "
        "ORDER BY id DESC LIMIT 100"
    ).fetchall()
    counter: Counter[str] = Counter()
    for r in action_rows:
        if r["rejected_reason"]:
            counter["REJECTED"] += 1
            continue
        try:
            d = json.loads(r["decision_json"])
            counter[d.get("action", "?")] += 1
        except (json.JSONDecodeError, TypeError):
            counter["?"] += 1
    total = sum(counter.values())
    action_dist = []
    for action in ("PASS", "BUY", "REJECTED", "?"):
        if action not in counter:
            continue
        n = counter[action]
        pct = 100 * n / total if total else 0
        action_dist.append((action, n, int(pct)))

    # Rejection reasons (with humanization for risk_gate reasons)
    rej_rows = conn.execute(
        "SELECT rejected_reason FROM decisions "
        "WHERE rejected_reason IS NOT NULL "
        "ORDER BY id DESC LIMIT 100"
    ).fetchall()
    rej_counter: Counter[str] = Counter()
    for r in rej_rows:
        reason = r["rejected_reason"]
        humanized = _humanize_rejection(reason, i18n)
        short = _truncate(humanized.replace("\n", " "), 80)
        rej_counter[short] += 1
    rejection_reasons = [
        {"count": n, "reason": reason}
        for reason, n in rej_counter.most_common(5)
    ]

    # Open positions
    pos_rows = conn.execute(
        "SELECT ticker, state, entry_price, sl, tp, qty, opened_at "
        "FROM positions WHERE state != 'closed' ORDER BY id DESC"
    ).fetchall()
    open_positions = []
    for r in pos_rows:
        ts = datetime.fromisoformat(r["opened_at"]).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{r['entry_price']:.2f}" if r["entry_price"] is not None else "--"
        sl = f"{r['sl']:.2f}" if r["sl"] is not None else "--"
        tp = f"{r['tp']:.2f}" if r["tp"] is not None else "--"
        qty = r["qty"] if r["qty"] is not None else "--"
        open_positions.append({
            "ticker": r["ticker"],
            "shares": qty,
            "entry_price": entry,
            "current_price": "(live price not in DB)",
            "stop_loss": sl,
            "take_profit": tp,
        })

    # Notification failures
    notif_rows = conn.execute(
        "SELECT event_kind, sent_at, error FROM notifications "
        "WHERE sent_ok = 0 AND retry_count > 0 "
        "ORDER BY id DESC LIMIT 10"
    ).fetchall()
    notification_failures = []
    for r in notif_rows:
        ts = r["sent_at"] or "(never sent)"
        err = _truncate((r["error"] or "").replace("\n", " "), 80)
        notification_failures.append({
            "ts": ts,
            "channel": r["event_kind"],
            "error": err,
        })

    return i18n.render(
        "report",
        now_iso=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        system_mode=s.mode.upper(),
        mode_changed_at=s.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        mode_changed_by=s.updated_by,
        open_positions_count=open_pos_count,
        backtests_count=bt_count,
        orders_count=orders_count,
        scan_runs=scan_runs,
        decisions=decisions,
        action_dist=action_dist,
        rejection_reasons=rejection_reasons,
        open_positions=open_positions,
        notification_failures=notification_failures,
    )


def _humanize_rejection(reason: str, i18n: I18n) -> str:
    """Translate risk_gate rejection reasons to human-readable form."""
    if reason.startswith("risk_gate:"):
        # e.g., "risk_gate: ['max_drawdown', 'min_sharpe']"
        body = reason[len("risk_gate:") :].strip()
        # Extract check names from list literal
        names = re.findall(r"'([^']+)'", body)
        if names:
            lang = i18n.language
            translated = ", ".join(humanize(n, lang) for n in names)
            if lang == "zh":
                return f"风控拒绝: {translated}"
            else:
                return f"Risk gate: {translated}"
    return reason


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def generate_positions_report(store: SqliteStore) -> str:
    """Open positions detail view with backtest metrics."""
    out = StringIO()
    conn = store._conn

    rows = conn.execute(
        "SELECT p.id, p.ticker, p.state, p.entry_price, p.sl, p.tp, p.qty, p.opened_at, "
        "o.alpaca_order_id, b.report_json "
        "FROM positions p "
        "JOIN orders o ON o.id = p.order_id "
        "JOIN backtests b ON b.id = o.backtest_id "
        "WHERE p.state != 'closed' "
        "ORDER BY p.id DESC"
    ).fetchall()

    count = len(rows)
    out.write(f"QuanterBack — Open Positions ({count} active)\n")
    out.write("=" * 40 + "\n\n")

    if not rows:
        out.write("No open positions.\n")
        return out.getvalue()

    for r in rows:
        ticker = r["ticker"]
        state = r["state"]
        entry = f"{r['entry_price']:.2f}" if r["entry_price"] is not None else "--"
        sl = f"{r['sl']:.2f}" if r["sl"] is not None else "--"
        tp = f"{r['tp']:.2f}" if r["tp"] is not None else "--"
        qty = r["qty"] if r["qty"] is not None else "--"
        opened_at = datetime.fromisoformat(r["opened_at"]).strftime("%Y-%m-%d %H:%M:%S UTC")
        alpaca_id = r["alpaca_order_id"] or "pending"

        # Calculate percentage moves
        sl_pct = ""
        tp_pct = ""
        if r["entry_price"] and r["sl"]:
            sl_pct = f" ({100 * (r['sl'] - r['entry_price']) / r['entry_price']:.1f}%)"
        if r["entry_price"] and r["tp"]:
            tp_pct = f" ({100 * (r['tp'] - r['entry_price']) / r['entry_price']:.1f}%)"

        out.write(f"{ticker}  {state}\n")
        out.write(f"  Order: {alpaca_id:<32} Opened: {opened_at}\n")
        out.write(
            f"  Qty: {str(qty):<4}  Entry: {entry}  SL: {sl}{sl_pct}  TP: {tp}{tp_pct}\n"
        )

        # Parse backtest report_json for metrics
        try:
            report = json.loads(r["report_json"])
            max_dd = report.get("max_drawdown")
            sharpe = report.get("sharpe")
            win_rate = report.get("win_rate")
            num_trades = report.get("num_trades")

            metrics = []
            if max_dd is not None:
                metrics.append(f"max_dd={max_dd:.2f}")
            if sharpe is not None:
                metrics.append(f"sharpe={sharpe:.1f}")
            if win_rate is not None:
                metrics.append(f"win_rate={win_rate:.2f}")
            if num_trades is not None:
                metrics.append(f"num_trades={num_trades}")

            if metrics:
                out.write(f"  Backtest: {' '.join(metrics)}\n")
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

        out.write("\n")

    return out.getvalue()


def generate_trades_report(store: SqliteStore, limit: int = 20) -> str:
    """Recent orders history with SL/TP and alpaca_order_id."""
    out = StringIO()
    conn = store._conn

    rows = conn.execute(
        "SELECT o.submitted_at, d.ticker, o.bracket_spec_json, o.dry_run, o.alpaca_order_id "
        "FROM orders o "
        "JOIN decisions d ON d.id = o.decision_id "
        "ORDER BY o.id DESC "
        "LIMIT ?",
        (limit,)
    ).fetchall()

    out.write("QuanterBack — Recent Orders (last 20)\n")
    out.write("=" * 60 + "\n\n")

    if not rows:
        out.write("No orders submitted yet.\n")
        return out.getvalue()

    # Header with columns
    out.write(
        f"  {'submitted_at':19}  {'ticker':6}  {'qty':>3}  "
        f"{'entry':>7}  {'sl':>8}  {'tp':>8}  {'dry_run':>7}  alpaca_id\n"
    )

    for r in rows:
        submitted_at = datetime.fromisoformat(r["submitted_at"]).strftime("%Y-%m-%d %H:%M:%S")

        # Parse bracket_spec_json
        try:
            bracket = json.loads(r["bracket_spec_json"])
            qty = bracket.get("qty", "--")
            entry_type = bracket.get("entry_type", "?")
            limit_price = bracket.get("limit_price")
            sl = bracket.get("stop_loss_price")
            tp = bracket.get("take_profit_price")

            entry_str = entry_type if entry_type == "market" else (
                f"{limit_price:.2f}" if limit_price is not None else "--"
            )
            sl_str = f"{sl:.2f}" if sl is not None else "--"
            tp_str = f"{tp:.2f}" if tp is not None else "--"

        except (json.JSONDecodeError, TypeError):
            qty = "--"
            entry_str = "--"
            sl_str = "--"
            tp_str = "--"

        dry_run = "True" if r["dry_run"] else "False"
        alpaca_id = r["alpaca_order_id"] or "(pending)"

        out.write(
            f"  {submitted_at}  {r['ticker']:6}  {str(qty):>3}  "
            f"{entry_str:>7}  {sl_str:>8}  {tp_str:>8}  {dry_run:>7}  {alpaca_id}\n"
        )

    out.write("\n")
    return out.getvalue()
