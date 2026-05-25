"""Stress test — replay across multiple regimes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from quanterback.replay import ReplayConfig, ReplayEngine, ReplayResult

log = logging.getLogger(__name__)


class StressWindow(NamedTuple):
    name: str
    start: date
    end: date


DEFAULT_WINDOWS: list[StressWindow] = [
    StressWindow("Apr 2024 correction",   date(2024, 4, 1),  date(2024, 4, 26)),
    StressWindow("Aug 2024 carry unwind", date(2024, 7, 15), date(2024, 8, 9)),
    StressWindow("Jan 2025 DeepSeek",     date(2025, 1, 13), date(2025, 2, 7)),
    StressWindow("Apr 2025 tariff",       date(2025, 4, 1),  date(2025, 4, 30)),
    StressWindow("Mar 2026 bull",         date(2026, 3, 9),  date(2026, 4, 3)),
]


@dataclass
class StressRow:
    window_name: str
    n_decisions: int
    n_buys: int
    n_trades: int
    win_rate_pct: float
    total_pnl_usd: float
    trade_sharpe: float
    max_drawdown_usd: float


def run_stress(
    *,
    engine: ReplayEngine,
    windows: list[StressWindow],
    tickers: list[str],
    max_llm_calls_per_window: int,
) -> list[tuple[StressWindow, ReplayResult]]:
    out = []
    for w in windows:
        log.info("Stress window: %s (%s → %s)", w.name, w.start, w.end)
        cfg = ReplayConfig(
            start=w.start, end=w.end, tickers=tickers,
            max_llm_calls=max_llm_calls_per_window,
        )
        result = engine.run(cfg)
        out.append((w, result))
    return out


def summarize_stress(rows: list[tuple[StressWindow, ReplayResult]]) -> list[StressRow]:
    summarized = []
    for w, r in rows:
        n = len(r.synthetic_trades)
        if n == 0:
            summarized.append(StressRow(
                window_name=w.name, n_decisions=len(r.decisions),
                n_buys=sum(1 for d in r.decisions if d["action"] == "BUY"),
                n_trades=0, win_rate_pct=0.0, total_pnl_usd=0.0,
                trade_sharpe=0.0, max_drawdown_usd=0.0,
            ))
            continue
        # Compute headline metrics directly from synthetic trades
        h = _compute_headline(r.synthetic_trades)
        ra = _compute_risk_adjusted(r.synthetic_trades)
        summarized.append(StressRow(
            window_name=w.name,
            n_decisions=len(r.decisions),
            n_buys=sum(1 for d in r.decisions if d["action"] == "BUY"),
            n_trades=n,
            win_rate_pct=h.get("win_rate_pct", 0.0),
            total_pnl_usd=h.get("total_pnl_usd", 0.0),
            trade_sharpe=ra.get("trade_sharpe", 0.0),
            max_drawdown_usd=ra.get("max_drawdown_usd", 0.0),
        ))
    return summarized


def _compute_headline(trades: list) -> dict:
    """Compute headline metrics from Trade objects (same as perf._headline)."""
    if not trades:
        return {"n": 0, "win_rate_pct": 0.0, "total_pnl_usd": 0.0}
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    total_pnl_usd = sum(t.pnl_usd for t in trades)
    return {
        "n": len(trades),
        "wins": wins,
        "win_rate_pct": round(wins / len(trades) * 100, 1),
        "total_pnl_usd": round(total_pnl_usd, 2),
    }


def _compute_risk_adjusted(trades: list) -> dict:
    """Compute risk-adjusted metrics from Trade objects (same as perf._risk_adjusted)."""
    import statistics
    if len(trades) < 2:
        return {
            "trade_sharpe": 0.0,
            "max_drawdown_usd": 0.0,
        }
    returns = [t.pnl_pct for t in trades]
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    trade_sharpe = mean / stdev if stdev > 0 else 0.0
    # Max drawdown over equity curve
    cum = 0.0
    peak = 0.0
    max_dd_usd = 0.0
    for t in trades:
        cum += t.pnl_usd
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd_usd:
            max_dd_usd = dd
    return {
        "trade_sharpe": round(trade_sharpe, 3),
        "max_drawdown_usd": round(max_dd_usd, 2),
    }


def generate_stress_report(rows: list[StressRow], i18n: object) -> str:
    return str(i18n.render("stress", rows=rows, n_windows=len(rows)))  # type: ignore
