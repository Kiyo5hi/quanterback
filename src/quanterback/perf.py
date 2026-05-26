"""Performance attribution analysis over closed trades."""
from __future__ import annotations  # noqa: I001

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import PersistedTrade
from quanterback.i18n import I18n


HOLDING_BUCKETS: list[tuple[str, float, float]] = [
    ("<1d", 0.0, 24.0),
    ("1-3d", 24.0, 72.0),
    ("3-7d", 72.0, 168.0),
    ("7d+", 168.0, float("inf")),
]


def generate_perf_report(
    store: SqliteStore,
    i18n: I18n,
    *,
    days: int | None = None,
    ticker: str | None = None,
) -> str:
    trades = _load_trades(store, days=days, ticker=ticker)
    return i18n.render(
        "perf",
        now=i18n.format_dt(datetime.now(tz=timezone.utc), "%Y-%m-%d %H:%M:%S %Z"),
        filter_days=days,
        filter_ticker=ticker,
        headline=_headline(trades),
        by_exit_reason=_by_exit_reason(trades),
        top_winners=_top_by_total_pnl(trades, n=5, reverse=True),
        bottom_losers=_top_by_total_pnl(trades, n=5, reverse=False),
        by_holding=_by_holding(trades),
        equity_curve=_equity_curve(trades, last=30),
        streaks=_streaks(trades),
        risk_adjusted=_risk_adjusted(trades),
        total_count=len(trades),
    )


def _load_trades(
    store: SqliteStore, *, days: int | None, ticker: str | None,
) -> list[PersistedTrade]:
    rows = store.list_recent_trades(limit=10000)
    if days is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        rows = [r for r in rows if r.exit_at >= cutoff]
    if ticker:
        rows = [r for r in rows if r.ticker.upper() == ticker.upper()]
    rows.sort(key=lambda r: r.exit_at)
    return rows


def _headline(trades: list[PersistedTrade]) -> dict:
    if not trades:
        return {"n": 0}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    total_pnl_usd = sum(t.pnl_usd for t in trades)
    avg_pnl_usd = total_pnl_usd / len(trades)
    avg_pnl_pct = sum(t.pnl_pct for t in trades) / len(trades)
    best = max(trades, key=lambda t: t.pnl_pct)
    worst = min(trades, key=lambda t: t.pnl_pct)
    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "avg_pnl_usd": round(avg_pnl_usd, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "best_ticker": best.ticker,
        "best_pnl_pct": round(best.pnl_pct, 2),
        "worst_ticker": worst.ticker,
        "worst_pnl_pct": round(worst.pnl_pct, 2),
    }


def _by_exit_reason(trades: list[PersistedTrade]) -> list[dict[str, object]]:
    grouped: dict[str, list[PersistedTrade]] = defaultdict(list)
    for t in trades:
        grouped[t.exit_reason].append(t)
    out: list[dict[str, object]] = []
    for reason, ts in grouped.items():
        wins = sum(1 for t in ts if t.pnl_usd > 0)
        total = sum(t.pnl_usd for t in ts)
        avg_pct = sum(t.pnl_pct for t in ts) / len(ts)
        out.append({
            "reason": reason,
            "n": len(ts),
            "win_rate_pct": round(wins / len(ts) * 100, 1),
            "avg_pnl_pct": round(avg_pct, 2),
            "total_pnl_usd": round(total, 2),
        })
    out.sort(key=lambda x: x["n"] if isinstance(x["n"], int) else 0, reverse=True)
    return out


def _top_by_total_pnl(
    trades: list[PersistedTrade], *, n: int, reverse: bool,
) -> list[dict[str, object]]:
    grouped: dict[str, list[PersistedTrade]] = defaultdict(list)
    for t in trades:
        grouped[t.ticker].append(t)
    summarized: list[dict[str, object]] = []
    for tk, ts in grouped.items():
        wins = sum(1 for t in ts if t.pnl_usd > 0)
        total = sum(t.pnl_usd for t in ts)
        avg_pct = sum(t.pnl_pct for t in ts) / len(ts)
        summarized.append({
            "ticker": tk,
            "n": len(ts),
            "win_rate_pct": round(wins / len(ts) * 100, 1),
            "total_pnl_usd": round(total, 2),
            "avg_pnl_pct": round(avg_pct, 2),
        })
    summarized.sort(
        key=lambda x: x["total_pnl_usd"] if isinstance(x["total_pnl_usd"], float) else 0.0,
        reverse=reverse,
    )
    return summarized[:n]


def _by_holding(trades: list[PersistedTrade]) -> list[dict]:
    out = []
    for label, lo, hi in HOLDING_BUCKETS:
        bucket = [t for t in trades if lo <= t.holding_hours < hi]
        if not bucket:
            continue
        wins = sum(1 for t in bucket if t.pnl_usd > 0)
        avg_pct = sum(t.pnl_pct for t in bucket) / len(bucket)
        out.append({
            "bucket": label,
            "n": len(bucket),
            "win_rate_pct": round(wins / len(bucket) * 100, 1),
            "avg_pnl_pct": round(avg_pct, 2),
        })
    return out


def _equity_curve(trades: list[PersistedTrade], *, last: int) -> list[dict]:
    if not trades:
        return []
    cum = 0.0
    rows = []
    for t in trades:
        cum += t.pnl_usd
        rows.append({
            "date": t.exit_at.strftime("%Y-%m-%d"),
            "ticker": t.ticker,
            "exit_reason": t.exit_reason,
            "pnl_pct": round(t.pnl_pct, 2),
            "cumulative_usd": round(cum, 2),
        })
    return rows[-last:]


def _streaks(trades: list[PersistedTrade]) -> dict:
    if not trades:
        return {"longest_win": 0, "longest_loss": 0, "current_kind": "—", "current_run": 0}
    longest_win = 0
    longest_loss = 0
    cur_win = 0
    cur_loss = 0
    for t in trades:
        if t.pnl_usd > 0:
            cur_win += 1
            cur_loss = 0
            longest_win = max(longest_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            longest_loss = max(longest_loss, cur_loss)
    last = trades[-1]
    if last.pnl_usd > 0:
        current_kind, current_run = "W", cur_win
    else:
        current_kind, current_run = "L", cur_loss
    return {
        "longest_win": longest_win,
        "longest_loss": longest_loss,
        "current_kind": current_kind,
        "current_run": current_run,
    }


def _risk_adjusted(trades: list[PersistedTrade]) -> dict:
    if len(trades) < 2:
        return {
            "trade_sharpe": 0.0,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "peak_equity_usd": 0.0,
            "dd_pct_is_capped": False,
        }
    returns = [t.pnl_pct for t in trades]
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    trade_sharpe = mean / stdev if stdev > 0 else 0.0
    # Max drawdown over equity curve
    cum = 0.0
    peak = 0.0
    max_dd_usd = 0.0
    peak_equity_at_max_dd = 0.0
    for t in trades:
        cum += t.pnl_usd
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd_usd:
            max_dd_usd = dd
            peak_equity_at_max_dd = peak

    # Compute pct, capping at 100% if peak is smaller than drawdown
    dd_pct_is_capped = False
    if peak_equity_at_max_dd > 0:
        max_dd_pct = (max_dd_usd / peak_equity_at_max_dd) * 100
        if max_dd_pct > 100.0:
            max_dd_pct = 100.0
            dd_pct_is_capped = True
    else:
        max_dd_pct = 0.0

    return {
        "trade_sharpe": round(trade_sharpe, 3),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "peak_equity_usd": round(peak_equity_at_max_dd, 2),
        "dd_pct_is_capped": dd_pct_is_capped,
    }
