"""Rich per-ticker scan brief output."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.config import AppConfig
from quanterback.i18n import I18n

log = logging.getLogger(__name__)


def render_scan_brief(
    scan_result: Any,
    i18n: I18n,
    store: SqliteStore,
    config: AppConfig,
    scan_run_id: int | None = None,
) -> str:
    """Render the rich brief from a specific scan_run (or latest if id omitted).

    Pass `scan_run_id` to avoid races with concurrent scans (cron + user trigger).
    """
    conn = store._conn
    if scan_run_id is not None:
        latest_run = conn.execute("""
            SELECT id, started_at, ended_at, tickers_processed, errors_count, source, trigger_label
            FROM scan_runs WHERE id = ?
        """, (scan_run_id,)).fetchone()
    else:
        latest_run = conn.execute("""
            SELECT id, started_at, ended_at, tickers_processed, errors_count, source, trigger_label
            FROM scan_runs ORDER BY id DESC LIMIT 1
        """).fetchone()
    if latest_run is None:
        return i18n.render(
            "scan_brief",
            error="no_scan",
            **_empty_ctx(),
        )

    decisions_rows = conn.execute("""
        SELECT id, ticker, summary_json, decision_json, rejected_reason, llm_model, agent_debate_json
        FROM decisions WHERE scan_run_id = ? ORDER BY id
    """, (latest_run["id"],)).fetchall()

    buys: list[dict] = []
    passes: list[dict] = []
    rejects: list[dict] = []

    for r in decisions_rows:
        entry = _build_decision_entry(r)
        if entry["bucket"] == "REJ":
            rejects.append(entry)
        elif entry["bucket"] == "BUY":
            buys.append(entry)
        elif entry["bucket"] == "PASS":
            passes.append(entry)

    # Watchlist info
    try:
        watchlist = store.list_watchlist()
        wl_counts = {"config": 0, "user": 0, "auto": 0}
        for w in watchlist:
            wl_counts[w.source] = wl_counts.get(w.source, 0) + 1
    except Exception as e:
        log.warning("Failed to fetch watchlist: %s", e)
        watchlist = []
        wl_counts = {"config": 0, "user": 0, "auto": 0}

    # SPY trend if available
    spy_trend = "unknown"

    # Compute current time
    now = datetime.now(tz=timezone.utc)

    # Build trigger label (latest_run is sqlite3.Row, convert to dict for access)
    try:
        trigger_label = latest_run["trigger_label"]
        if not trigger_label:  # if empty string, use source
            trigger_label = latest_run["source"]
    except (KeyError, TypeError):
        trigger_label = latest_run["source"] if "source" in latest_run else "unknown"

    # Translate special source values
    if trigger_label == "cron":
        trigger_label = "cron (定时扫描)" if config.language == "zh" else "cron (scheduled)"
    elif trigger_label == "user_trigger":
        trigger_label = "用户触发" if config.language == "zh" else "user trigger"

    is_dry_run = isinstance(trigger_label, str) and "[DRY]" in trigger_label

    return i18n.render(
        "scan_brief",
        error=None,
        now=i18n.format_dt(now, "%Y-%m-%d %H:%M %Z"),
        mode="dry-run" if is_dry_run else getattr(config, "mode", "live"),
        is_dry_run=is_dry_run,
        n_processed=latest_run["tickers_processed"] or 0,
        n_errors=latest_run["errors_count"] or 0,
        trigger_label=trigger_label,
        spy_trend=spy_trend,
        benchmark_ticker=getattr(config, "benchmark_ticker", "VOO"),
        wl_total=len(watchlist),
        wl_counts=wl_counts,
        buys=buys[:10],
        passes_notable=passes[:5],
        passes_count=len(passes),
        rejects=rejects[:10],
        rejects_count=len(rejects),
        auto_promoted=[],
        auto_demoted=[],
    )


def _build_decision_entry(row: Any) -> dict:
    """Convert a decisions row into a structured entry for the template."""
    try:
        summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
    except (json.JSONDecodeError, TypeError):
        summary = {}
    try:
        decision = json.loads(row["decision_json"]) if row["decision_json"] else {}
    except (json.JSONDecodeError, TypeError):
        decision = {}

    # Parse agent_debate_json if present
    agent_debate = None
    debate_raw = row.get("agent_debate_json") if row and hasattr(row, "get") else None
    if debate_raw is None and row and hasattr(row, "__getitem__"):
        try:
            debate_raw = row["agent_debate_json"]
        except (KeyError, TypeError):
            debate_raw = None
    if debate_raw:
        try:
            agent_debate = json.loads(debate_raw)
        except json.JSONDecodeError:
            pass

    if row["rejected_reason"]:
        return {
            "bucket": "REJ",
            "ticker": row["ticker"],
            "reason": row["rejected_reason"],
        }

    action = decision.get("action", "?")
    bucket = action if action in ("BUY", "PASS") else "UNKNOWN"

    # Extract signals from summary, handling nested objects defensively
    tech = summary.get("technicals", {}) or {}
    vol = summary.get("volatility", {}) or {}
    volume = summary.get("volume", {}) or {}
    funda = summary.get("fundamentals", {}) or {}
    insider = summary.get("insider_activity") or {}
    analysts = summary.get("recent_analyst_actions") or []
    short = summary.get("short_interest") or {}
    eps = summary.get("eps_trend") or {}
    news = summary.get("news") or []

    # Build order spec from decision if available
    size_info = ""
    if decision.get("params"):
        params = decision.get("params", {})
        qty = params.get("qty")
        entry_price = params.get("entry_price")
        if qty and entry_price:
            size_usd = qty * entry_price
            size_info = f"size ${size_usd:,.0f} ({qty} sh @ ${entry_price:.0f})"

    # Build risk info
    risk_info = ""
    if decision.get("params"):
        params = decision.get("params", {})
        entry = params.get("entry_price")
        sl = params.get("stop_loss_price")
        tp = params.get("take_profit_price")
        if entry and sl and tp:
            risk_pct = (sl - entry) / entry
            reward_pct = (tp - entry) / entry
            ratio = reward_pct / abs(risk_pct) if risk_pct != 0 else 0
            risk_info = (
                f"Risk: SL ${sl:.0f} ({risk_pct:+.1%})  "
                f"TP ${tp:.0f} ({reward_pct:+.1%})  R/R 1:{ratio:.2f}"
            )

    return {
        "bucket": bucket,
        "ticker": row["ticker"],
        "action": action,
        "strategy": decision.get("strategy", ""),
        "confidence": decision.get("confidence", 0),
        "rationale": (decision.get("rationale") or "")[:250],
        "news_sentiment": decision.get("news_sentiment"),
        "size_info": size_info,
        "risk_info": risk_info,
        # Signals
        "rsi": tech.get("rsi_14"),
        "trend": summary.get("trend_regime"),
        "macd_signal": tech.get("macd_signal"),
        "vol_regime": vol.get("regime"),
        "volume_regime": volume.get("regime"),
        # Enrichments
        "days_to_earnings": funda.get("days_to_next_earnings"),
        "insider_n_buys": (
            insider.get("n_buys") if isinstance(insider, dict) else None
        ),
        "insider_n_sells": (
            insider.get("n_sells") if isinstance(insider, dict) else None
        ),
        "insider_total_buy_usd": (
            insider.get("total_buy_usd") if isinstance(insider, dict) else None
        ),
        "analyst_actions_count": len(analysts) if analysts else 0,
        "analyst_summary": _summarize_analysts(analysts),
        "short_pct": (
            short.get("short_pct_of_float") if isinstance(short, dict) else None
        ),
        "eps_growth_q_yoy": (
            eps.get("growth_q_yoy") if isinstance(eps, dict) else None
        ),
        "top_news_title": (
            news[0].get("title") if news and isinstance(news[0], dict) else None
        ),
        "agent_debate": agent_debate,
    }


def _summarize_analysts(actions: list) -> str:
    if not actions:
        return ""
    ups = sum(
        1 for a in actions
        if isinstance(a, dict) and "upgrade" in (a.get("action") or "").lower()
    )
    downs = sum(
        1 for a in actions
        if isinstance(a, dict) and "downgrade" in (a.get("action") or "").lower()
    )
    return f"{ups} upgrades / {downs} downgrades"


def _empty_ctx() -> dict:
    return {
        "now": "",
        "mode": "",
        "n_processed": 0,
        "n_errors": 0,
        "trigger_label": "",
        "spy_trend": "",
        "wl_total": 0,
        "wl_counts": {},
        "buys": [],
        "passes_notable": [],
        "passes_count": 0,
        "rejects": [],
        "rejects_count": 0,
        "auto_promoted": [],
        "auto_demoted": [],
    }
