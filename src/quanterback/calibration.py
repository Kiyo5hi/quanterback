"""LLM confidence calibration analysis."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.i18n import I18n

log = logging.getLogger(__name__)


@dataclass
class Bucket:
    lo: float
    hi: float
    n: int
    n_wins: int

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n if self.n > 0 else 0.0

    @property
    def gap(self) -> float:
        return self.win_rate - self.midpoint


def generate_calibration_report(
    store: SqliteStore,
    i18n: I18n,
    *,
    days: int | None = None,
    bucket_width: float = 0.1,
    source: str = "live",
) -> str:
    if source == "replay":
        return i18n.render("calibration", error="replay_not_implemented",
                          buckets=[], headline=None, recommendation=None,
                          diagram=[], n_buckets=0, bucket_width=bucket_width,
                          filter_days=days)

    conn = store._conn
    rows = _load_decisions_with_outcomes(conn, days=days)

    if not rows:
        return i18n.render("calibration", error="no_data",
                          buckets=[], headline=None, recommendation=None,
                          diagram=[], n_buckets=0, bucket_width=bucket_width,
                          filter_days=days)

    buckets = _compute_buckets(rows, bucket_width)
    headline = _headline(rows)
    diagram = _ascii_reliability_diagram(buckets)
    # diagram: list of bucket dicts (or empty list on error). Templates use
    # `{% for b in diagram %}` which is safe for both shapes.
    recommendation = _recommend(headline["brier_score"], i18n.language)

    return i18n.render(
        "calibration",
        error=None,
        buckets=buckets,
        headline=headline,
        diagram=diagram,
        recommendation=recommendation,
        n_buckets=len(buckets),
        bucket_width=bucket_width,
        filter_days=days,
    )


def _load_decisions_with_outcomes(
    conn: sqlite3.Connection, *, days: int | None,
) -> list[dict]:
    """Returns list of {confidence, outcome} for BUY decisions linked to closed trades."""
    cutoff = ""
    params: list = []
    if days is not None:
        cutoff_iso = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        cutoff = "AND d.created_at >= ?"
        params.append(cutoff_iso)

    # Trades have decision_id linking back to decisions
    query = f"""
        SELECT d.decision_json, t.pnl_usd
        FROM decisions d
        JOIN trades t ON t.decision_id = d.id
        WHERE d.rejected_reason IS NULL
        {cutoff}
    """
    rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        try:
            dec = json.loads(r["decision_json"])
        except json.JSONDecodeError:
            continue
        if dec.get("action") != "BUY":
            continue
        conf = dec.get("confidence")
        if conf is None:
            continue
        out.append({
            "confidence": float(conf),
            "outcome": 1 if r["pnl_usd"] > 0 else 0,
        })
    return out


def _compute_buckets(rows: list[dict], width: float) -> list[Bucket]:
    n_buckets = int(1.0 / width)
    buckets = [Bucket(lo=i * width, hi=(i + 1) * width, n=0, n_wins=0)
               for i in range(n_buckets)]
    for r in rows:
        c = r["confidence"]
        idx = min(int(c / width), n_buckets - 1)
        buckets[idx].n += 1
        buckets[idx].n_wins += r["outcome"]
    return buckets


def _headline(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    wins = sum(r["outcome"] for r in rows)
    avg_conf = sum(r["confidence"] for r in rows) / n
    win_rate = wins / n
    brier = sum((r["confidence"] - r["outcome"]) ** 2 for r in rows) / n
    return {
        "n": n,
        "wins": wins,
        "avg_confidence": round(avg_conf, 3),
        "empirical_win_rate": round(win_rate, 3),
        "calibration_gap": round(win_rate - avg_conf, 3),
        "brier_score": round(brier, 4),
    }


def _ascii_reliability_diagram(buckets: list[Bucket], width: int = 18) -> list[dict]:
    """One line per bucket, with █ for empirical and · for diagonal target."""
    out = []
    for b in buckets:
        bar_target = "·" * int(b.midpoint * width)
        if b.n == 0:
            bar = " " * width
            label = "no data"
        else:
            filled = int(b.win_rate * width)
            bar = "█" * filled + " " * (width - filled)
            label = f"empirical {b.win_rate*100:5.1f}%"
        # Overlay target on top of empirical for visualization
        out.append({
            "lo": round(b.lo, 2),
            "hi": round(b.hi, 2),
            "bar": bar,
            "target_bar": bar_target.ljust(width),
            "n": b.n,
            "label": label,
            "gap": round(b.gap, 3) if b.n else 0.0,
        })
    return out


def _recommend(brier: float, language: str) -> str:
    if brier > 0.30:
        return ("信心值基本是随机的 — 不要用作仓位调整输入" if language == "zh"
                else "Confidence is roughly random — don't use for sizing")
    if brier > 0.20:
        return ("有一定信号 — 可以作为软先验" if language == "zh"
                else "Some signal — could use as soft prior")
    return ("校准良好 — 可以作为仓位调整输入" if language == "zh"
            else "Well calibrated — use as position-sizing input")
