from __future__ import annotations

from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import (
    atr_wilder,
    macd_recent_cross,
    realized_vol_annualized,
    rsi_wilder,
    simple_moving_average,
)
from quanterback.domain.market import (
    AnalystAction,
    CondensedSummary,
    EpsTrend,
    FundamentalLite,
    InsiderActivity,
    IntradaySignals,
    MarketDataQualityError,
    MomentumSignals,
    MovingAverages,
    NewsItem,
    PriceSnapshot,
    PriceWindow,
    ShortInterestSnapshot,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)


class RuleBasedSummarizer:
    """Deterministic CondensedSummary builder from PriceWindow.

    All indicator math is in `indicators.py`; this module orchestrates the
    pipeline and classifies regimes via fixed thresholds.
    """

    def summarize(
        self, w: PriceWindow,
        spy_closes: pd.Series | None = None,
        news: list[NewsItem] | None = None,
        earnings_date: date | None = None,
        insider_activity: InsiderActivity | None = None,
        analyst_actions: list[AnalystAction] | None = None,
        short_interest: ShortInterestSnapshot | None = None,
        eps_trend: EpsTrend | None = None,
        fundamental_ratios: dict | None = None,
        allow_short_history: bool = False,
    ) -> CondensedSummary:
        daily = w.daily
        hourly = w.hourly
        closes = daily["close"]

        price = self._price_snapshot(closes)
        ma = self._moving_averages(closes, allow_short_history=allow_short_history)
        vol = self._vol_profile(daily, hourly=hourly, allow_short_history=allow_short_history)
        volprof = self._volume_profile(daily)
        tech = self._technicals(closes, allow_short_history=allow_short_history)

        # Compute days_to_next_earnings if earnings_date provided
        days_to_earnings = None
        if earnings_date is not None:
            from datetime import datetime, timezone
            today = datetime.now(tz=timezone.utc).date()
            days_to_earnings = (earnings_date - today).days

        # Extract fundamental ratios if provided
        ratios_dict = fundamental_ratios or {}
        funda = FundamentalLite(
            days_to_next_earnings=days_to_earnings,
            market_cap_bucket="unknown",
            pe_ratio=ratios_dict.get("pe_ratio"),
            forward_pe=ratios_dict.get("forward_pe"),
            peg_ratio=ratios_dict.get("peg_ratio"),
            price_to_book=ratios_dict.get("price_to_book"),
            fcf_yield=ratios_dict.get("fcf_yield"),
            roe=ratios_dict.get("roe"),
            profit_margin=ratios_dict.get("profit_margin"),
            debt_to_equity=ratios_dict.get("debt_to_equity"),
            revenue_growth_yoy=ratios_dict.get("revenue_growth_yoy"),
        )
        trend = self._trend_regime(ma)
        momentum = self._momentum_signals(daily, spy_closes)
        intraday = self._intraday_signals(daily, hourly, vol.atr_14)

        return CondensedSummary(
            ticker=w.ticker, as_of=w.as_of, price=price, moving_averages=ma,
            volatility=vol, volume=volprof, technicals=tech, fundamentals=funda,
            trend_regime=trend, momentum_signals=momentum,
            intraday=intraday,
            news=news or [],
            insider_activity=insider_activity,
            recent_analyst_actions=analyst_actions or [],
            short_interest=short_interest,
            eps_trend=eps_trend,
        )

    # --- pieces ---

    def _price_snapshot(self, closes: pd.Series) -> PriceSnapshot:
        last = float(closes.iloc[-1])
        def _ret(n: int) -> float:
            if len(closes) <= n:
                return 0.0
            return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1)
        win = closes.tail(252) if len(closes) >= 252 else closes
        hi = float(win.max())
        lo = float(win.min())
        return PriceSnapshot(
            last_close=last, return_1d=_ret(1), return_5d=_ret(5),
            return_20d=_ret(20), return_60d=_ret(60),
            pct_from_52w_high=(last / hi - 1) if hi > 0 else 0.0,
            pct_from_52w_low=(last / lo - 1) if lo > 0 else 0.0,
        )

    def _moving_averages(
        self, closes: pd.Series, *, allow_short_history: bool = False,
    ) -> MovingAverages:
        def _sma(window: int) -> float:
            value = float(simple_moving_average(closes, window).iloc[-1])
            if np.isfinite(value) or not allow_short_history:
                return value
            return float(closes.tail(min(window, len(closes))).mean())

        sma20 = _sma(20)
        sma50 = _sma(50)
        sma200 = _sma(200)
        last = float(closes.iloc[-1])
        if not np.isfinite(last) or last <= 0:
            raise MarketDataQualityError(
                "last close unavailable or non-positive; ticker has bad price data"
            )
        if allow_short_history and not all(
            np.isfinite(v) and v > 0 for v in (sma20, sma50, sma200)
        ):
            raise MarketDataQualityError(
                "moving averages unavailable; ticker has insufficient or bad price history"
            )
        alignment: Literal["bullish", "bearish", "mixed"]
        if sma20 > sma50 > sma200:
            alignment = "bullish"
        elif sma20 < sma50 < sma200:
            alignment = "bearish"
        else:
            alignment = "mixed"
        return MovingAverages(
            sma_20=sma20, sma_50=sma50, sma_200=sma200,
            pct_above_sma_20=last / sma20 - 1, pct_above_sma_50=last / sma50 - 1,
            pct_above_sma_200=last / sma200 - 1, alignment=alignment,
        )

    def _vol_profile(
        self,
        daily: pd.DataFrame,
        *,
        hourly: pd.DataFrame | None = None,
        allow_short_history: bool = False,
    ) -> VolatilityProfile:
        closes = daily["close"]
        rv_current = realized_vol_annualized(closes, 20)
        atr = float(atr_wilder(daily, 14).iloc[-1])
        last_close = float(closes.iloc[-1])
        if (not np.isfinite(atr) or atr <= 0) and allow_short_history:
            atr = self._short_history_atr(daily, hourly=hourly)
        if not np.isfinite(atr) or atr <= 0:
            raise MarketDataQualityError(
                "ATR14 unavailable or zero; ticker may be halted, stale, or have bad OHLC data"
            )
        if not np.isfinite(last_close) or last_close <= 0:
            raise MarketDataQualityError(
                "last close unavailable or non-positive; ticker has bad price data"
            )
        atr_pct = atr / last_close

        # Compute rolling 20-day annualized realized vol over the full window,
        # then classify the latest reading against the ticker's own distribution.
        daily_returns = closes.pct_change()
        rolling_std = daily_returns.rolling(window=20, min_periods=20).std()
        rolling_vol = rolling_std * np.sqrt(252)
        rolling_vol = rolling_vol.dropna()

        if len(rolling_vol) >= 60:
            # Use percentile of this ticker's own history
            percentile = float((rolling_vol < rv_current).mean())
            if percentile < 0.25:
                regime = VolatilityRegime.LOW
            elif percentile < 0.75:
                regime = VolatilityRegime.NORMAL
            elif percentile < 0.95:
                regime = VolatilityRegime.HIGH
            else:
                regime = VolatilityRegime.EXTREME
        else:
            # Fallback: absolute thresholds when not enough history
            if rv_current < 0.20:
                regime = VolatilityRegime.LOW
            elif rv_current < 0.45:
                regime = VolatilityRegime.NORMAL
            elif rv_current < 0.85:
                regime = VolatilityRegime.HIGH
            else:
                regime = VolatilityRegime.EXTREME

        return VolatilityProfile(
            realized_vol_20d_annualized=rv_current, atr_14=atr,
            atr_pct_of_price=atr_pct, regime=regime,
        )

    def _short_history_atr(
        self, daily: pd.DataFrame, *, hourly: pd.DataFrame | None = None,
    ) -> float:
        close = daily["close"]
        if len(daily) >= 2:
            high = daily["high"]
            low = daily["low"]
            prev_close = close.shift(1)
            tr = pd.concat(
                [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1).dropna()
            if len(tr) >= 2:
                value = float(tr.tail(min(14, len(tr))).mean())
                if np.isfinite(value) and value > 0:
                    return value

        if hourly is not None and len(hourly) >= 4:
            high = hourly["high"]
            low = hourly["low"]
            prev_close = hourly["close"].shift(1)
            tr = pd.concat(
                [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1).dropna()
            if len(tr) >= 2:
                # Roughly six and a half regular-session hours per trading day.
                value = float(tr.tail(min(14 * 7, len(tr))).mean() * 6.5)
                if np.isfinite(value) and value > 0:
                    return value
        return float("nan")

    def _volume_profile(self, daily: pd.DataFrame) -> VolumeProfile:
        vol = daily["volume"]
        last = int(vol.iloc[-1])
        avg20 = float(vol.tail(20).mean())
        ratio = last / avg20 if avg20 > 0 else 0.0
        if ratio < 0.7:
            regime = VolumeRegime.BELOW_AVG
        elif ratio < 1.3:
            regime = VolumeRegime.NORMAL
        elif ratio < 2.0:
            regime = VolumeRegime.ELEVATED
        else:
            regime = VolumeRegime.EXTREME
        return VolumeProfile(
            last_volume=last, avg_volume_20d=int(avg20),
            volume_ratio=ratio, regime=regime,
        )

    def _technicals(
        self, closes: pd.Series, *, allow_short_history: bool = False,
    ) -> TechnicalIndicators:
        rsi = float(rsi_wilder(closes, 14).iloc[-1])
        if allow_short_history and len(closes) < 15:
            deltas = closes.diff().dropna()
            gains = deltas.clip(lower=0.0).sum()
            losses = -deltas.clip(upper=0.0).sum()
            if losses == 0 and gains > 0:
                rsi = 100.0
            elif gains == 0 and losses > 0:
                rsi = 0.0
            elif gains > 0 and losses > 0:
                rs = gains / losses
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 50.0
        return TechnicalIndicators(
            rsi_14=rsi,
            macd_signal=macd_recent_cross(closes, window=5),
        )

    def _trend_regime(self, ma: MovingAverages) -> TrendRegime:
        if ma.alignment == "bullish" and ma.pct_above_sma_50 > 0.02:
            return TrendRegime.UPTREND
        if ma.alignment == "bearish" and ma.pct_above_sma_50 < -0.02:
            return TrendRegime.DOWNTREND
        return TrendRegime.SIDEWAYS

    def _momentum_signals(
        self, daily: pd.DataFrame, spy_closes: pd.Series | None,
    ) -> MomentumSignals:
        closes = daily["close"]
        opens = daily["open"]
        last = float(closes.iloc[-1])

        # Gap up today: today's open vs yesterday's close
        if len(opens) >= 2:
            prev_close = float(closes.iloc[-2])
            today_open = float(opens.iloc[-1])
            gap_up = (today_open / prev_close - 1) if prev_close > 0 else 0.0
        else:
            gap_up = 0.0

        # Near 52w high (within 5%)
        win = closes.tail(252) if len(closes) >= 252 else closes
        hi_52w = float(win.max()) if len(win) > 0 else last
        is_near_high = (last / hi_52w >= 0.95) if hi_52w > 0 else False

        # Breakout 20d high: today's close >= trailing-20d high (excluding today)
        if len(closes) >= 21:
            prior_20d_high = float(closes.iloc[-21:-1].max())
            is_breakout_20d = last >= prior_20d_high
        else:
            is_breakout_20d = False

        # Relative strength vs SPY (20d)
        rs = 0.0
        if spy_closes is not None and len(closes) >= 21 and len(spy_closes) >= 21:
            ticker_ret = float(closes.iloc[-1] / closes.iloc[-21] - 1)
            spy_ret = float(spy_closes.iloc[-1] / spy_closes.iloc[-21] - 1)
            rs = ticker_ret - spy_ret

        # Consecutive up days: streak ending today
        diffs = closes.diff().tail(30)  # cap streak counting at 30
        streak = 0
        for v in reversed(diffs.dropna().tolist()):
            if v > 0:
                streak += 1
            else:
                break

        return MomentumSignals(
            gap_up_today_pct=gap_up,
            is_near_52w_high=is_near_high,
            is_breakout_20d_high=is_breakout_20d,
            relative_strength_vs_spy_20d=rs,
            consecutive_up_days=streak,
        )

    def _intraday_signals(
        self, daily: pd.DataFrame, hourly: pd.DataFrame, atr_14: float,
    ) -> IntradaySignals:
        if len(hourly) < 2:
            # No usable hourly data — return zeros
            return IntradaySignals(
                return_today_pct=0.0, return_last_hour_pct=0.0,
                pct_from_intraday_high=0.0, is_above_yesterday_high=False,
                intraday_range_pct_of_atr=0.0, consecutive_up_hours=0,
            )

        # Determine 'today' = last available trading date in hourly index
        last_ts = hourly.index[-1]
        today_date = last_ts.date()

        # Subset hourly to today
        index_as_datetime = hourly.index
        date_mask = index_as_datetime.date == today_date  # type: ignore[attr-defined]
        today_bars = hourly.loc[date_mask]
        if len(today_bars) == 0:
            # Edge case: index isn't datetime-like; fall back to last 7 hourly bars
            today_bars = hourly.tail(7)

        # Yesterday: the day before today_date that has bars
        date_mask_yest = index_as_datetime.date < today_date  # type: ignore[attr-defined]
        yesterday_bars = hourly.loc[date_mask_yest]

        last_close = float(hourly["close"].iloc[-1])
        today_open = float(today_bars["open"].iloc[0])
        today_high = float(today_bars["high"].max())
        today_low = float(today_bars["low"].min())

        return_today = (last_close / today_open - 1) if today_open > 0 else 0.0

        # Last hour return: last 1h close vs prior 1h close
        if len(hourly) >= 2:
            prior = float(hourly["close"].iloc[-2])
            ret_last_h = (last_close / prior - 1) if prior > 0 else 0.0
        else:
            ret_last_h = 0.0

        pct_from_high = (last_close / today_high - 1) if today_high > 0 else 0.0

        if len(yesterday_bars) > 0:
            yest_high = float(yesterday_bars["high"].max())
            is_above_yhigh = last_close > yest_high
        else:
            is_above_yhigh = False

        today_range = today_high - today_low
        range_pct_of_atr = (today_range / atr_14) if atr_14 > 0 else 0.0

        # Streak of green 1h candles ending now, counted from today's bars
        if len(today_bars) >= 2:
            today_diffs = today_bars["close"].diff()
            streak = 0
            for v in reversed(today_diffs.dropna().tolist()):
                if v > 0:
                    streak += 1
                else:
                    break
        else:
            streak = 0

        return IntradaySignals(
            return_today_pct=return_today,
            return_last_hour_pct=ret_last_h,
            pct_from_intraday_high=pct_from_high,
            is_above_yesterday_high=is_above_yhigh,
            intraday_range_pct_of_atr=range_pct_of_atr,
            consecutive_up_hours=streak,
        )
