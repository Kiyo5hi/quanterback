"""Historical replay engine — point-in-time pipeline simulation."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import atr_wilder
from quanterback.domain.market import PriceWindow
from quanterback.domain.trade import Trade
from quanterback.interfaces.data import HistoricalDataProvider

log = logging.getLogger(__name__)


@dataclass
class ReplayConfig:
    start: date
    end: date
    tickers: list[str]
    max_llm_calls: int = 30
    lookback_bars: int = 200
    forward_bars: int = 30
    sl_atr: float = 1.5
    tp_atr: float = 3.0
    trail_pct: float = 0.08
    timeout_bars: int = 20


@dataclass
class ReplayResult:
    config: ReplayConfig
    # {date, ticker, action, strategy, rationale_short}
    decisions: list[dict] = field(default_factory=list)
    synthetic_trades: list[Trade] = field(default_factory=list)
    llm_calls_made: int = 0
    skipped_no_data: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


class ReplayEngine:
    def __init__(
        self,
        *,
        data_provider: HistoricalDataProvider,
        summarizer: object,  # RuleBasedSummarizer-like
        strategist: object,  # LLMStrategist-like
        backtester: object,  # VectorizedBacktester-like
        spy_provider: HistoricalDataProvider | None = None,
    ) -> None:
        self.data_provider = data_provider
        self.summarizer = summarizer
        self.strategist = strategist
        self.backtester = backtester
        self.spy_provider = spy_provider or data_provider

    def run(self, cfg: ReplayConfig) -> ReplayResult:
        result = ReplayResult(config=cfg)
        days = list(_iter_business_days(cfg.start, cfg.end))

        # Fetch SPY historical data once
        spy_df = self._fetch_spy_df(cfg)

        for ticker in cfg.tickers:
            try:
                ticker_df = self.data_provider.fetch_historical(
                    ticker, years=5
                )
            except Exception as e:
                log.warning("fetch_historical failed for %s: %s", ticker, e)
                result.skipped_no_data.append(ticker)
                continue

            if ticker_df is None or len(ticker_df) < cfg.lookback_bars + cfg.forward_bars:
                result.skipped_no_data.append(ticker)
                continue

            for d in days:
                if result.llm_calls_made >= cfg.max_llm_calls:
                    log.info("LLM call cap reached (%d), stopping", cfg.max_llm_calls)
                    return result

                # Find the index of the last bar on or before date d
                idx = _find_bar_index_at_or_before(ticker_df.index, d)
                if (
                    idx is None
                    or idx < cfg.lookback_bars
                    or idx + cfg.forward_bars >= len(ticker_df)
                ):
                    continue

                # Slice to lookback window (strictly at or before d)
                window = _build_price_window(
                    ticker, ticker_df, end_idx=idx, lookback=cfg.lookback_bars
                )

                # Get SPY closes for the same window
                spy_closes = None
                if spy_df is not None:
                    spy_closes = _get_spy_closes(
                        spy_df, end_idx=idx, lookback=cfg.lookback_bars
                    )

                try:
                    summary = self.summarizer.summarize(  # type: ignore
                        window, spy_closes=spy_closes, news=None
                    )
                except Exception as e:
                    err_msg = f"summarize: {e}"
                    result.errors.append({
                        "date": str(d),
                        "ticker": ticker,
                        "error": err_msg,
                    })
                    log.warning("summarize failed for %s on %s: %s", ticker, d, e)
                    continue

                try:
                    decision = self.strategist.decide(summary)  # type: ignore
                    result.llm_calls_made += 1
                except Exception as e:
                    err_msg = f"strategist: {e}"
                    result.errors.append({
                        "date": str(d),
                        "ticker": ticker,
                        "error": err_msg,
                    })
                    log.warning("strategist failed for %s on %s: %s", ticker, d, e)
                    continue

                # Extract summary snapshot for verbose output
                summary_snapshot = _extract_summary_snapshot(summary)

                result.decisions.append({
                    "date": str(d),
                    "ticker": ticker,
                    "action": decision.action,
                    "strategy": getattr(decision, "strategy", "") or "",
                    "rationale_short": (decision.rationale or "")[:120],
                    "confidence": getattr(decision, "confidence", None),
                    "summary_snapshot": summary_snapshot,
                })

                # Only simulate if action is BUY
                if decision.action != "BUY":
                    continue

                # Compute ATR from historical window (at and before entry)
                # This is the volatility measure available at entry time, no lookahead
                hist_atr = atr_wilder(window.daily, 14)
                if hist_atr is None or len(hist_atr) == 0 or not np.isfinite(hist_atr.iloc[-1]):
                    log.debug(
                        "Skipping sim for %s on %s: insufficient historical bars for ATR",
                        ticker, d
                    )
                    continue
                entry_atr_val = float(hist_atr.iloc[-1])

                # Simulate via backtester on forward window
                forward = _build_price_window(
                    ticker, ticker_df, end_idx=idx + cfg.forward_bars,
                    lookback=cfg.forward_bars + 1
                )

                try:
                    sim = self.backtester.simulate(  # type: ignore
                        window=forward,
                        entry_price=float(forward.daily["close"].iloc[0]),
                        entry_atr=entry_atr_val,
                        sl_atr=cfg.sl_atr,
                        tp_atr=cfg.tp_atr,
                        trail_pct=cfg.trail_pct,
                        timeout_bars=cfg.timeout_bars,
                    )
                except Exception as e:
                    err_msg = f"sim: {e}"
                    result.errors.append({
                        "date": str(d),
                        "ticker": ticker,
                        "error": err_msg,
                    })
                    log.warning("backtester failed for %s on %s: %s", ticker, d, e)
                    continue

                if sim is None:
                    continue

                trade = _build_trade(ticker, d, sim, cfg)
                if trade is not None:
                    result.synthetic_trades.append(trade)

        return result

    def _fetch_spy_df(self, cfg: ReplayConfig) -> pd.DataFrame | None:
        try:
            return self.spy_provider.fetch_historical("SPY", years=5)
        except Exception:
            log.warning("Could not fetch SPY historical data")
            return None


def _iter_business_days(start: date, end: date) -> Iterator[date]:
    """Iterate over all business days (Mon-Fri) in [start, end]."""
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)


def _find_bar_index_at_or_before(
    index: pd.Index,  # DatetimeIndex but mypy struggles with it
    target: date,
) -> int | None:
    """Returns latest index i such that index[i].date() <= target.

    Assumes index is sorted ascending.
    """
    last_ok: int | None = None
    for i, ts in enumerate(index):
        ts_date = ts.date() if hasattr(ts, "date") else ts
        if ts_date <= target:
            last_ok = i
        else:
            break
    return last_ok


def _build_price_window(
    ticker: str,
    df: pd.DataFrame,
    *,
    end_idx: int,
    lookback: int,
) -> PriceWindow:
    """Build a PriceWindow from a slice of daily data."""
    start = max(0, end_idx - lookback + 1)
    daily = df.iloc[start:end_idx + 1].copy()

    # For simplicity, hourly is same as daily (we don't have hourly in historical data)
    hourly = daily.copy()

    # Get the timestamp of the last bar
    as_of = daily.index[-1]
    if isinstance(as_of, pd.Timestamp):
        as_of_dt = as_of.to_pydatetime()
    else:
        as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)

    return PriceWindow(
        ticker=ticker,
        daily=daily,
        hourly=hourly,
        as_of=as_of_dt,
    )


def _get_spy_closes(
    spy_df: pd.DataFrame, *, end_idx: int, lookback: int
) -> pd.Series:
    """Get a slice of SPY closes as a pd.Series."""
    start = max(0, end_idx - lookback + 1)
    return spy_df["close"].iloc[start:end_idx + 1].copy()


def _build_trade(ticker: str, entry_date: date, sim: object, cfg: ReplayConfig) -> Trade | None:
    """Convert a sim result to a Trade row."""
    from datetime import datetime as dt
    from datetime import timezone as tz

    entry_price = float(getattr(sim, "entry_price", 0.0))
    exit_price = float(getattr(sim, "exit_price", 0.0))
    bars_held = int(getattr(sim, "bars_held", 0) or 0)

    if entry_price <= 0 or exit_price <= 0:
        return None

    entry_at = dt.combine(entry_date, dt.min.time(), tzinfo=tz.utc)
    exit_at = entry_at + timedelta(days=bars_held)
    pnl_pct = (exit_price / entry_price - 1.0) * 100.0
    pnl_usd = (exit_price - entry_price) * 10  # synthetic qty=10
    reason = _classify(sim)

    return Trade(
        ticker=ticker,
        side="LONG",
        qty=10,
        entry_price=round(entry_price, 4),
        entry_at=entry_at,
        exit_price=round(exit_price, 4),
        exit_at=exit_at,
        exit_reason=reason,  # type: ignore
        pnl_usd=round(pnl_usd, 2),
        pnl_pct=round(pnl_pct, 2),
        holding_hours=float(bars_held) * 24.0,
        notes=f"replay synthetic, sim.{reason}",
    )


def _classify(sim: object) -> str:
    """Map backtester sim outcome to exit reason."""
    reason = getattr(sim, "exit_reason", "") or ""
    reason_l = reason.lower()
    if "stop" in reason_l and "trail" in reason_l:
        return "TRAILING_STOP"
    if "stop" in reason_l or "sl" in reason_l:
        return "STOP_LOSS"
    if "target" in reason_l or "tp" in reason_l or "profit" in reason_l:
        return "TAKE_PROFIT"
    if "timeout" in reason_l or "expiry" in reason_l:
        return "TIMEOUT"
    return "UNKNOWN"


def _extract_summary_snapshot(summary: object) -> dict:
    """Extract key technical indicators from CondensedSummary for verbose output."""
    snapshot = {}
    try:
        # RSI
        if hasattr(summary, "technicals") and hasattr(summary.technicals, "rsi_14"):
            snapshot["rsi"] = summary.technicals.rsi_14
    except (AttributeError, TypeError):
        pass
    try:
        # Trend regime
        if hasattr(summary, "trend_regime"):
            snapshot["trend"] = str(summary.trend_regime).split(".")[-1].lower()
    except (AttributeError, TypeError):
        pass
    try:
        # MACD signal
        if hasattr(summary, "technicals") and hasattr(summary.technicals, "macd_signal"):
            snapshot["macd"] = summary.technicals.macd_signal
    except (AttributeError, TypeError):
        pass
    try:
        # Volume regime
        if hasattr(summary, "volume") and hasattr(summary.volume, "regime"):
            snapshot["vol_regime"] = str(summary.volume.regime).split(".")[-1].lower()
    except (AttributeError, TypeError):
        pass
    try:
        # Volatility regime
        if hasattr(summary, "volatility") and hasattr(summary.volatility, "regime"):
            snapshot["vol_hist_regime"] = str(summary.volatility.regime).split(".")[-1].lower()
    except (AttributeError, TypeError):
        pass
    return snapshot


def print_verbose_decisions(result: ReplayResult) -> None:
    """Print per-decision details for debugging (one line per decision, <200 chars)."""
    print("\n--- VERBOSE DECISIONS ---")
    for decision in result.decisions:
        date_str = decision["date"]
        ticker = decision["ticker"]
        action = decision["action"]
        strategy = decision.get("strategy", "")
        conf = decision.get("confidence")
        snapshot = decision.get("summary_snapshot", {})
        rationale = decision.get("rationale_short", "")

        # Build key=val pairs from snapshot
        indicators = []
        if "rsi" in snapshot:
            indicators.append(f"rsi={snapshot['rsi']:.0f}")
        if "trend" in snapshot:
            indicators.append(f"trend={snapshot['trend']}")
        if "macd" in snapshot:
            indicators.append(f"macd={snapshot['macd']}")
        if "vol_regime" in snapshot:
            indicators.append(f"vol={snapshot['vol_regime']}")
        if "vol_hist_regime" in snapshot:
            indicators.append(f"vol_hist={snapshot['vol_hist_regime']}")

        # Format: [date] ticker ACTION strategy conf=X rsi=X trend=X | rat: first 80 chars
        indicators_str = " ".join(indicators)
        if conf is not None:
            conf_str = f"conf={conf:.2f}"
            indicators_str = f"{conf_str} {indicators_str}" if indicators_str else conf_str

        rat_short = (rationale or "")[:80]
        if strategy:
            line = f"[{date_str}] {ticker} {action} {strategy} {indicators_str}"
        else:
            line = f"[{date_str}] {ticker} {action} {indicators_str}"

        if rat_short:
            line = f"{line} | rat: {rat_short}"
        print(line[:200])


def generate_replay_report(result: ReplayResult, i18n: object) -> str:
    """Render via i18n replay.j2 template."""
    from collections import Counter
    from datetime import datetime as dt
    from datetime import timezone as tz

    from quanterback.perf import (
        _by_exit_reason,
        _by_holding,
        _equity_curve,
        _headline,
        _risk_adjusted,
        _streaks,
        _top_by_total_pnl,
    )

    trades = result.synthetic_trades
    action_dist = Counter(d["action"] for d in result.decisions)
    return i18n.render(  # type: ignore
        "replay",
        now=dt.now(tz=tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        start=result.config.start.isoformat(),
        end=result.config.end.isoformat(),
        tickers=", ".join(result.config.tickers),
        llm_calls_made=result.llm_calls_made,
        llm_call_cap=result.config.max_llm_calls,
        n_decisions=len(result.decisions),
        action_dist=[{"action": a, "n": n} for a, n in action_dist.most_common()],
        skipped_no_data=result.skipped_no_data,
        errors_count=len(result.errors),
        headline=_headline(trades) if trades else {"n": 0},  # type: ignore
        by_exit_reason=_by_exit_reason(trades),  # type: ignore
        top_winners=_top_by_total_pnl(trades, n=5, reverse=True),  # type: ignore
        bottom_losers=_top_by_total_pnl(trades, n=5, reverse=False),  # type: ignore
        by_holding=_by_holding(trades),  # type: ignore
        equity_curve=_equity_curve(trades, last=30),  # type: ignore
        streaks=_streaks(trades),  # type: ignore
        risk_adjusted=_risk_adjusted(trades),  # type: ignore
        total_count=len(trades),
    )
