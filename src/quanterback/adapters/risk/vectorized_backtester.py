from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import atr_wilder
from quanterback.domain.backtest import BacktestReport, BacktestRequest, TradeRecord
from quanterback.domain.market import PriceWindow
from quanterback.interfaces.data import HistoricalDataProvider

ExitReason = Literal["stop_loss", "take_profit", "timeout"]

SL_ATR_MULT_BT = 1.5
TP_ATR_MULT_BT = 3.0
TIMEOUT_BARS = 20


@dataclass
class SimResult:
    """Result of a single-trade simulation."""
    entry_price: float
    exit_price: float
    bars_held: int
    exit_reason: str


class VectorizedBacktester:
    """Pandas + numpy momentum backtest. No look-ahead bias: enter next bar open."""

    def __init__(self, hist: HistoricalDataProvider) -> None:
        self._hist = hist

    def run(self, request: BacktestRequest) -> BacktestReport:
        df = self._hist.fetch_historical(request.ticker, request.lookback_years)
        lookback = int(request.params["lookback_days"])

        if request.strategy == "MOMENTUM":
            threshold = float(request.params["momentum_threshold"])
            signals = self._entry_signals(df["close"], lookback, threshold)
        elif request.strategy == "MEAN_REVERSION":
            z = float(request.params["entry_z_score"])
            signals = self._mean_reversion_entry_signals(df["close"], lookback, z)
        else:
            raise ValueError(f"Unsupported strategy: {request.strategy}")

        trades = self._simulate_with_signals(df, signals, lookback)
        return self._build_report(request, df, trades, lookback=lookback)

    def simulate(
        self,
        *,
        window: PriceWindow,
        entry_price: float,
        entry_atr: float,
        sl_atr: float,
        tp_atr: float,
        trail_pct: float,
        timeout_bars: int,
    ) -> SimResult | None:
        """Simulate a single trade on a forward window.

        Args:
            window: PriceWindow with daily data already sliced to the forward period
            entry_price: Price at which we enter (e.g., open of the day after signal)
            entry_atr: ATR computed at entry time (from historical bars, NOT forward window).
                       This is the true volatility measure available at entry.
            sl_atr: ATR multiplier for stop loss
            tp_atr: ATR multiplier for take profit
            trail_pct: Trailing stop percentage (not used in basic version)
            timeout_bars: Max bars to hold before timeout

        Returns:
            SimResult with entry/exit prices and bars held, or None if invalid.
        """
        import logging
        log = logging.getLogger(__name__)

        df = window.daily
        if df is None or len(df) < 2:
            return None

        # Guard against NaN or non-finite ATR values
        if not np.isfinite(entry_atr) or entry_atr <= 0:
            log.info("Skipping sim: entry_atr=%s is not finite or <= 0", entry_atr)
            return None

        atr_val = float(entry_atr)

        # Compute SL and TP
        sl = entry_price - sl_atr * atr_val
        tp = entry_price + tp_atr * atr_val

        # Find exit on the forward window
        highs = df["high"]
        lows = df["low"]
        closes = df["close"]

        # Start from bar 0 (entry is at open of bar 0)
        exit_idx, exit_price, reason = self._find_exit(
            highs, lows, closes, 0, sl, tp, timeout_bars
        )

        # Info logging for replay diagnostics
        log.info(
            "sim: entry=%.2f atr=%.2f sl=%.2f tp=%.2f bars_held=%d exit=%s exit_price=%.2f",
            entry_price, atr_val, sl, tp, exit_idx, reason, exit_price,
        )

        return SimResult(
            entry_price=entry_price,
            exit_price=exit_price,
            bars_held=exit_idx,
            exit_reason=reason,
        )

    # ------- simulation -------

    @staticmethod
    def _entry_signals(closes: pd.Series, lookback: int, threshold: float) -> pd.Series:
        rolling_ret = closes / closes.shift(lookback) - 1
        cond = rolling_ret > threshold
        # Trigger only on transition False -> True
        prev = cond.shift(1).fillna(False).astype(bool)
        return (cond & ~prev).astype(bool)

    @staticmethod
    def _mean_reversion_entry_signals(
        closes: pd.Series, lookback: int, entry_z_score: float,
    ) -> pd.Series:
        mean = closes.rolling(window=lookback, min_periods=lookback).mean()
        std = closes.rolling(window=lookback, min_periods=lookback).std()
        z = (closes - mean) / std.replace(0, float("nan"))
        cond = z < -entry_z_score
        prev = cond.shift(1).fillna(False).astype(bool)
        return (cond & ~prev).fillna(False).astype(bool)

    def _simulate_with_signals(
        self, df: pd.DataFrame, signals: pd.Series, lookback: int,
    ) -> list[TradeRecord]:
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        opens = df["open"]
        atr = atr_wilder(df, 14)

        trades: list[TradeRecord] = []
        i = lookback + 14   # warmup: need lookback for strategy + 14 for ATR
        n = len(df)
        while i < n - 1:
            if signals.iloc[i] and not np.isnan(atr.iloc[i]):
                entry_idx = i + 1
                if entry_idx >= n:
                    break
                entry_price = float(opens.iloc[entry_idx])
                atr_val = float(atr.iloc[i])
                sl = entry_price - SL_ATR_MULT_BT * atr_val
                tp = entry_price + TP_ATR_MULT_BT * atr_val
                exit_idx, exit_price, reason = self._find_exit(
                    highs, lows, closes, entry_idx, sl, tp, timeout_bars=TIMEOUT_BARS,
                )
                trades.append(TradeRecord(
                    entry_date=_to_date(df.index[entry_idx]),
                    exit_date=_to_date(df.index[exit_idx]),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    return_pct=exit_price / entry_price - 1,
                    bars_held=exit_idx - entry_idx,
                    exit_reason=reason,
                ))
                i = exit_idx + 1
            else:
                i += 1
        return trades

    @staticmethod
    def _find_exit(
        highs: pd.Series, lows: pd.Series, closes: pd.Series,
        entry_idx: int, sl: float, tp: float, timeout_bars: int = TIMEOUT_BARS,
    ) -> tuple[int, float, ExitReason]:
        n = len(highs)
        end = min(entry_idx + timeout_bars, n - 1)
        for j in range(entry_idx, end + 1):
            if lows.iloc[j] <= sl:
                return j, sl, "stop_loss"
            if highs.iloc[j] >= tp:
                return j, tp, "take_profit"
        # timeout — exit at close on last bar in window
        return end, float(closes.iloc[end]), "timeout"

    # ------- metrics -------

    @staticmethod
    def _compute_oos_metrics(
        df: pd.DataFrame, oos_trades: list[TradeRecord], split_idx: int
    ) -> dict:
        """Compute metrics on out-of-sample subset of trades."""
        if not oos_trades:
            return dict(
                oos_num_trades=0, oos_win_rate=0.0, oos_max_drawdown=0.0,
                oos_sharpe=0.0, oos_cumulative_return=0.0, oos_excess_return=0.0,
            )
        returns = np.array([t.return_pct for t in oos_trades])
        wins = returns[returns > 0]
        equity = (1 + returns).cumprod()
        max_dd = float((1 - equity / np.maximum.accumulate(equity)).max())
        mean_r = float(returns.mean())
        std_r = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        avg_bars = float(np.mean([t.bars_held for t in oos_trades]))
        trades_per_year = 252 / max(avg_bars, 1.0)
        sharpe = (mean_r / std_r) * np.sqrt(trades_per_year) if std_r > 0 else 0.0
        cum_return = float(equity[-1] - 1)
        # OOS B&H
        bh_oos = df["close"].iloc[split_idx:]
        bh_ret = (
            float(bh_oos.iloc[-1] / bh_oos.iloc[0] - 1) if len(bh_oos) > 1 else 0.0
        )
        return dict(
            oos_num_trades=len(oos_trades),
            oos_win_rate=float(len(wins) / len(oos_trades)),
            oos_max_drawdown=max_dd,
            oos_sharpe=sharpe,
            oos_cumulative_return=cum_return,
            oos_excess_return=cum_return - bh_ret,
        )

    @staticmethod
    def _build_report(
        request: BacktestRequest, df: pd.DataFrame, trades: list[TradeRecord],
        *, lookback: int,
    ) -> BacktestReport:
        warmup_idx = lookback + 14
        if warmup_idx >= len(df):
            warmup_idx = max(0, len(df) - 1)

        # Buy-and-hold over backtest period
        bh_window = df["close"].iloc[warmup_idx:]
        if len(bh_window) > 1:
            bh_return = float(bh_window.iloc[-1] / bh_window.iloc[0] - 1)
            bh_running_max = bh_window.cummax()
            bh_max_dd = float((1 - bh_window / bh_running_max).max())
        else:
            bh_return = 0.0
            bh_max_dd = 0.0

        # Walk-forward OOS split (last 33%)
        if len(df) > warmup_idx:
            split_idx = warmup_idx + int((len(df) - warmup_idx) * 0.67)
        else:
            split_idx = len(df)
        split_date = df.index[split_idx].date() if split_idx < len(df) else df.index[-1].date()

        oos_trades = [t for t in trades if t.entry_date >= split_date]
        oos_metrics = VectorizedBacktester._compute_oos_metrics(df, oos_trades, split_idx)

        if not trades:
            return BacktestReport(
                ticker=request.ticker, strategy=request.strategy,
                params=request.params,
                period_start=_to_date(df.index[0]),
                period_end=_to_date(df.index[-1]),
                num_trades=0, win_rate=0.0, max_drawdown=0.0, sharpe=0.0,
                profit_factor=0.0, cumulative_return=0.0,
                avg_trade_return=0.0, avg_bars_held=0.0, trades=[],
                buy_and_hold_return=bh_return,
                buy_and_hold_max_drawdown=bh_max_dd,
                excess_return=0.0 - bh_return,
                drawdown_ratio=0.0,
                **oos_metrics,
            )

        returns = np.array([t.return_pct for t in trades])
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        equity = (1 + returns).cumprod()
        drawdown = 1 - equity / np.maximum.accumulate(equity)
        max_dd = float(drawdown.max()) if len(drawdown) else 0.0

        # Sharpe: per-trade Sharpe, annualized by avg bars held
        mean_ret = float(returns.mean())
        std_ret = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        avg_bars = float(np.mean([t.bars_held for t in trades])) if trades else 1.0
        trades_per_year = 252 / max(avg_bars, 1.0)
        sharpe = (
            (mean_ret / std_ret) * np.sqrt(trades_per_year)
            if std_ret > 0 else 0.0
        )

        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(-losses.sum()) if len(losses) else 0.0
        pf = gross_profit / gross_loss if gross_loss > 0 else 0.0

        cum_ret = float(equity[-1] - 1)
        excess_ret = cum_ret - bh_return
        dd_ratio = max_dd / max(bh_max_dd, 0.01)

        return BacktestReport(
            ticker=request.ticker, strategy=request.strategy, params=request.params,
            period_start=_to_date(df.index[0]),
            period_end=_to_date(df.index[-1]),
            num_trades=len(trades),
            win_rate=float(len(wins) / len(trades)),
            max_drawdown=max(max_dd, 0.0),
            sharpe=float(sharpe),
            profit_factor=float(pf),
            cumulative_return=cum_ret,
            avg_trade_return=float(mean_ret),
            avg_bars_held=avg_bars,
            trades=trades,
            buy_and_hold_return=bh_return,
            buy_and_hold_max_drawdown=bh_max_dd,
            excess_return=excess_ret,
            drawdown_ratio=dd_ratio,
            **oos_metrics,
        )


def _to_date(ts: str | int | float | date) -> date:
    return pd.Timestamp(ts).date()
