"""Pattern analysis over historical decisions/scan_runs.

Read-only. Built atop SqliteStore raw queries + Counter aggregation.
Renders via i18n template 'analyze.j2'.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.failed_check_labels import humanize
from quanterback.i18n import I18n


def generate_analyze_report(store: SqliteStore, i18n: I18n) -> str:
    conn = store._conn
    now = datetime.now(tz=timezone.utc)

    daily_volume = _decisions_per_day(conn, days=7)
    pass_top = _top_action_tickers(conn, action="PASS", limit=10)
    buy_top = _top_action_tickers(conn, action="BUY", limit=10)
    weekly_dist = _action_dist_by_week(conn, weeks=4)
    rejection_counts = _risk_gate_rejection_counts(conn, language=i18n.language)
    latency = _latency_stats(conn)

    return i18n.render(
        "analyze",
        now=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        daily_volume=daily_volume,
        pass_top=pass_top,
        buy_top=buy_top,
        weekly_dist=weekly_dist,
        rejection_counts=rejection_counts,
        latency=latency,
    )


def _decisions_per_day(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Returns list of {date, n} for the past `days`."""
    rows = conn.execute("""
        SELECT substr(created_at, 1, 10) as day, COUNT(*) as n
        FROM decisions
        WHERE created_at >= ?
        GROUP BY day ORDER BY day
    """, ((datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat(),)).fetchall()
    return [{"date": r["day"], "n": r["n"]} for r in rows]


def _top_action_tickers(
    conn: sqlite3.Connection, *, action: str, limit: int,
) -> list[dict]:
    """Top tickers with given action, with count and latest rationale snippet."""
    # rejected_reason IS NULL → decision_json had real action
    rows = conn.execute("""
        SELECT ticker, decision_json
        FROM decisions
        WHERE rejected_reason IS NULL AND length(decision_json) > 2
        ORDER BY id DESC
        LIMIT 1000
    """).fetchall()
    counter: Counter[str] = Counter()
    latest_rationale: dict[str, str] = {}
    for r in rows:
        try:
            d = json.loads(r["decision_json"])
        except json.JSONDecodeError:
            continue
        if d.get("action") != action:
            continue
        t = r["ticker"]
        counter[t] += 1
        if t not in latest_rationale:
            latest_rationale[t] = (d.get("rationale") or "")[:120]
    top = counter.most_common(limit)
    return [
        {"ticker": t, "n": n, "snippet": latest_rationale.get(t, "")}
        for t, n in top
    ]


def _action_dist_by_week(conn: sqlite3.Connection, weeks: int) -> list[dict]:
    """Weekly counts of PASS/BUY/REJ over the last `weeks` ISO weeks."""
    rows = conn.execute("""
        SELECT created_at, decision_json, rejected_reason
        FROM decisions
        WHERE created_at >= ?
        ORDER BY created_at
    """, ((datetime.now(tz=timezone.utc) - timedelta(weeks=weeks)).isoformat(),)).fetchall()
    by_week: dict[str, dict[str, int]] = {}
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["created_at"])
        except (ValueError, TypeError):
            continue
        iso = ts.isocalendar()
        wk_key = f"{iso.year}-W{iso.week:02d}"
        bucket = by_week.setdefault(wk_key, {"PASS": 0, "BUY": 0, "REJ": 0})
        if r["rejected_reason"]:
            bucket["REJ"] += 1
        else:
            try:
                d = json.loads(r["decision_json"])
                action = d.get("action", "?")
                if action in ("PASS", "BUY"):
                    bucket[action] += 1
            except (json.JSONDecodeError, TypeError):
                continue
    return [
        {"week": wk, **vals}
        for wk, vals in sorted(by_week.items())
    ]


def _risk_gate_rejection_counts(
    conn: sqlite3.Connection, *, language: str,
) -> list[dict]:
    """Count of each failed_check from recent backtests (humanized)."""
    rows = conn.execute("""
        SELECT failed_checks FROM backtests
        WHERE passed = 0 AND failed_checks IS NOT NULL
        ORDER BY id DESC LIMIT 200
    """).fetchall()
    counter: Counter[str] = Counter()
    for r in rows:
        for fc in (r["failed_checks"] or "").split(","):
            fc = fc.strip()
            if fc:
                counter[fc] += 1
    return [
        {"check": humanize(c, language), "raw": c, "count": n}
        for c, n in counter.most_common(20)
    ]


def _latency_stats(conn: sqlite3.Connection) -> dict:
    """Median + p95 scan duration (per-ticker)."""
    rows = conn.execute("""
        SELECT started_at, ended_at, tickers_processed
        FROM scan_runs
        WHERE ended_at IS NOT NULL AND tickers_processed > 0
        ORDER BY id DESC LIMIT 50
    """).fetchall()
    if not rows:
        return {"median_s": 0.0, "p95_s": 0.0, "n": 0}
    per_ticker: list[float] = []
    for r in rows:
        try:
            start = datetime.fromisoformat(r["started_at"])
            end = datetime.fromisoformat(r["ended_at"])
            secs = (end - start).total_seconds()
            per_ticker.append(secs / r["tickers_processed"])
        except (ValueError, TypeError):
            continue
    if not per_ticker:
        return {"median_s": 0.0, "p95_s": 0.0, "n": 0}
    per_ticker.sort()
    n = len(per_ticker)
    median = per_ticker[n // 2]
    p95_idx = max(0, int(n * 0.95) - 1)
    p95 = per_ticker[p95_idx]
    return {"median_s": round(median, 2), "p95_s": round(p95, 2), "n": n}
