from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class MarketDataQualityError(ValueError):
    """Raised when fetched market data cannot support trading analysis."""


class TrendRegime(str, Enum):  # noqa: UP042
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"


class VolatilityRegime(str, Enum):  # noqa: UP042
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class VolumeRegime(str, Enum):  # noqa: UP042
    BELOW_AVG = "below_avg"
    NORMAL = "normal"
    ELEVATED = "elevated"
    EXTREME = "extreme"


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_close: float
    return_1d: float
    return_5d: float
    return_20d: float
    return_60d: float
    pct_from_52w_high: float
    pct_from_52w_low: float


class MovingAverages(BaseModel):
    model_config = ConfigDict(frozen=True)
    sma_20: float
    sma_50: float
    sma_200: float
    pct_above_sma_20: float
    pct_above_sma_50: float
    pct_above_sma_200: float
    alignment: Literal["bullish", "bearish", "mixed"]


class VolatilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    realized_vol_20d_annualized: float = Field(ge=0)
    atr_14: float = Field(ge=0)
    atr_pct_of_price: float = Field(ge=0)
    regime: VolatilityRegime


class VolumeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_volume: int = Field(ge=0)
    avg_volume_20d: int = Field(ge=0)
    volume_ratio: float = Field(ge=0)
    regime: VolumeRegime


class TechnicalIndicators(BaseModel):
    model_config = ConfigDict(frozen=True)
    rsi_14: float = Field(ge=0, le=100)
    macd_signal: Literal["bullish_cross", "bearish_cross", "none"]


class NewsItem(BaseModel):
    """Single news headline + provenance."""
    model_config = ConfigDict(frozen=True)
    title: str
    publisher: str
    age_hours: float = Field(ge=0)
    link: str | None = None


class InsiderActivity(BaseModel):
    """Aggregated Form 4 activity in lookback window."""
    model_config = ConfigDict(frozen=True)
    n_buys: int = 0
    n_sells: int = 0
    total_buy_usd: float = 0.0
    total_sell_usd: float = 0.0
    notable_buyer: str | None = None
    lookback_days: int = 30


class AnalystAction(BaseModel):
    """Single analyst rating change."""
    model_config = ConfigDict(frozen=True)
    firm: str
    action: str
    from_grade: str = ""
    to_grade: str = ""
    date: date


class ShortInterestSnapshot(BaseModel):
    """Current short interest metrics."""
    model_config = ConfigDict(frozen=True)
    short_pct_of_float: float | None = None
    days_to_cover: float | None = None
    short_ratio: float | None = None


class EpsTrend(BaseModel):
    """EPS estimate and trend."""
    model_config = ConfigDict(frozen=True)
    current_estimate: float | None = None
    days_7_change_pct: float | None = None
    days_30_change_pct: float | None = None
    growth_q_yoy: float | None = None


class FundamentalLite(BaseModel):
    model_config = ConfigDict(frozen=True)
    days_to_next_earnings: int | None = None
    market_cap_bucket: Literal["large", "mid", "small", "unknown"]
    # NEW: Institutional-grade valuation and profitability ratios
    pe_ratio: float | None = None           # trailing P/E
    forward_pe: float | None = None         # forward P/E
    peg_ratio: float | None = None          # P/E / growth
    price_to_book: float | None = None
    fcf_yield: float | None = None          # free cash flow yield, as decimal (0.05 = 5%)
    roe: float | None = None                # return on equity, as decimal
    profit_margin: float | None = None      # net profit margin, as decimal
    debt_to_equity: float | None = None
    revenue_growth_yoy: float | None = None # decimal


class MomentumSignals(BaseModel):
    """Entry-timing signals — breakout, gap, relative strength, streaks."""
    model_config = ConfigDict(frozen=True)
    gap_up_today_pct: float
    is_near_52w_high: bool
    is_breakout_20d_high: bool
    relative_strength_vs_spy_20d: float = 0.0
    consecutive_up_days: int = Field(ge=0)


class IntradaySignals(BaseModel):
    """1h-bar signals for short-window timing.

    Helps the LLM distinguish 'broke out at open and held' (catalyst day)
    from 'drifted up slowly over a week' (technical setup).
    """
    model_config = ConfigDict(frozen=True)
    return_today_pct: float                    # today's open → last close
    return_last_hour_pct: float                # last completed 1h candle
    pct_from_intraday_high: float              # ≤ 0; 0 = at the highs
    is_above_yesterday_high: bool              # decisive break vs yest range
    intraday_range_pct_of_atr: float           # today's H-L ÷ ATR14 — busy/quiet day
    consecutive_up_hours: int = Field(ge=0)    # green-candle streak


class CondensedSummary(BaseModel):
    """LLM-facing compressed snapshot. See spec §4.1."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: datetime
    price: PriceSnapshot
    moving_averages: MovingAverages
    volatility: VolatilityProfile
    volume: VolumeProfile
    technicals: TechnicalIndicators
    fundamentals: FundamentalLite
    trend_regime: TrendRegime
    momentum_signals: MomentumSignals
    intraday: IntradaySignals
    news: list[NewsItem] = Field(default_factory=list)
    insider_activity: InsiderActivity | None = None
    recent_analyst_actions: list[AnalystAction] = Field(default_factory=list)
    short_interest: ShortInterestSnapshot | None = None
    eps_trend: EpsTrend | None = None

    def to_prompt_text(self) -> str:
        p = self.price
        ma = self.moving_averages
        v = self.volatility
        vol = self.volume
        t = self.technicals
        f = self.fundamentals
        ms = self.momentum_signals
        intra = self.intraday
        ts = self.as_of.strftime("%Y-%m-%d %H:%M %Z").strip()
        macd = (
            "bullish_cross within last 5 days"
            if t.macd_signal == "bullish_cross"
            else "bearish_cross within last 5 days"
            if t.macd_signal == "bearish_cross"
            else "no recent cross"
        )
        earnings = (
            f"{f.days_to_next_earnings} days away"
            if f.days_to_next_earnings is not None
            else "unknown"
        )
        intraday_block = (
            f"Intraday (1h bars):\n"
            f"  Today so far: {intra.return_today_pct:+.2%}\n"
            f"  Last hour:    {intra.return_last_hour_pct:+.2%}\n"
            f"  From day high: {intra.pct_from_intraday_high:+.2%}\n"
            f"  Above yesterday's high: {'yes' if intra.is_above_yesterday_high else 'no'}\n"
            f"  Intraday range / ATR14: {intra.intraday_range_pct_of_atr:.2f}\n"
            f"  Consecutive up hours: {intra.consecutive_up_hours}\n"
        )
        if self.news:
            news_lines = ["News (last 7 days, newest first):"]
            for n in self.news[:5]:
                news_lines.append(
                    f"  [{n.age_hours:.0f}h ago, {n.publisher}] {n.title[:120]}"
                )
            news_block = "\n".join(news_lines) + "\n"
        else:
            news_block = ""

        # Build enrichment blocks
        insider_block = ""
        if self.insider_activity:
            ia = self.insider_activity
            insider_block = (
                f"Insider activity (last {ia.lookback_days}d):\n"
                f"  Buys: {ia.n_buys} (${ia.total_buy_usd:,.0f})\n"
                f"  Sells: {ia.n_sells} (${ia.total_sell_usd:,.0f})\n"
            )
            if ia.notable_buyer:
                insider_block += f"  Notable: {ia.notable_buyer}\n"

        analyst_block = ""
        if self.recent_analyst_actions:
            analyst_lines = ["Analyst actions (last 14d):"]
            for action in self.recent_analyst_actions[:5]:
                grade_str = ""
                if action.from_grade and action.to_grade:
                    grade_str = f" {action.from_grade} → {action.to_grade}"
                elif action.to_grade:
                    grade_str = f" to {action.to_grade}"
                analyst_lines.append(
                    f"  - {action.firm}: {action.action}{grade_str} ({action.date})"
                )
            analyst_block = "\n".join(analyst_lines) + "\n"

        short_block = ""
        if self.short_interest is not None:
            si = self.short_interest
            if si.short_pct_of_float is not None:
                short_block = "Short interest:\n"
                short_block += f"  Short % of float: {si.short_pct_of_float * 100:.1f}%\n"
                if si.days_to_cover is not None:
                    short_block += f"  Days to cover: {si.days_to_cover:.1f}\n"

        eps_block = ""
        if self.eps_trend:
            eps = self.eps_trend
            eps_block = "EPS trend:\n"
            if eps.current_estimate is not None:
                eps_block += f"  Current quarter estimate: ${eps.current_estimate:.2f}\n"
            if eps.growth_q_yoy is not None:
                eps_block += f"  YoY growth: {eps.growth_q_yoy * 100:+.1f}%\n"

        # Build fundamentals ratio block
        ratio_parts = []
        if f.pe_ratio is not None:
            ratio_parts.append(f"P/E {f.pe_ratio:.1f}")
        if f.forward_pe is not None:
            ratio_parts.append(f"Fwd P/E {f.forward_pe:.1f}")
        if f.peg_ratio is not None:
            ratio_parts.append(f"PEG {f.peg_ratio:.2f}")
        if f.price_to_book is not None:
            ratio_parts.append(f"P/B {f.price_to_book:.1f}")
        if f.fcf_yield is not None:
            ratio_parts.append(f"FCF yield {f.fcf_yield * 100:.1f}%")
        if f.roe is not None:
            ratio_parts.append(f"ROE {f.roe * 100:.0f}%")
        if f.profit_margin is not None:
            ratio_parts.append(f"NPM {f.profit_margin * 100:.1f}%")
        if f.debt_to_equity is not None:
            ratio_parts.append(f"D/E {f.debt_to_equity:.2f}")
        if f.revenue_growth_yoy is not None:
            ratio_parts.append(f"Rev YoY {f.revenue_growth_yoy * 100:+.0f}%")
        ratio_block = ""
        if ratio_parts:
            ratio_block = f"Ratios: {' · '.join(ratio_parts)}\n"

        return (
            f"[{self.ticker} @ {ts}]\n"
            f"Price: ${p.last_close:.2f} "
            f"({p.return_1d:+.1%} 1d / {p.return_5d:+.1%} 5d / "
            f"{p.return_20d:+.1%} 20d / {p.return_60d:+.1%} 60d)\n"
            f"52w range: {p.pct_from_52w_high:+.1%} from high, "
            f"{p.pct_from_52w_low:+.1%} from low\n"
            f"Trend: {self.trend_regime.value.upper()}  "
            f"(price above SMA20 {ma.pct_above_sma_20:+.1%}, "
            f"SMA50 {ma.pct_above_sma_50:+.1%}, "
            f"SMA200 {ma.pct_above_sma_200:+.1%})\n"
            f"                SMA stack alignment: {ma.alignment}\n"
            f"Volatility: {v.regime.value.upper()}  "
            f"(20d realized {v.realized_vol_20d_annualized:.0%} ann.; "
            f"ATR14 = ${v.atr_14:.2f} = {v.atr_pct_of_price:.2%} of price)\n"
            f"Volume: {vol.regime.value.upper()}  "
            f"(today {vol.volume_ratio:.1f}x 20d avg)\n"
            f"RSI(14): {t.rsi_14:.1f}\n"
            f"MACD: {macd}\n"
            f"Momentum signals:\n"
            f"  Gap up today: {ms.gap_up_today_pct:+.2%}\n"
            f"  Near 52w high (within 5%): {'yes' if ms.is_near_52w_high else 'no'}\n"
            f"  20d breakout: {'yes' if ms.is_breakout_20d_high else 'no'}\n"
            f"  RS vs SPY (20d): {ms.relative_strength_vs_spy_20d:+.1%}\n"
            f"  Consecutive up days: {ms.consecutive_up_days}\n"
            f"{intraday_block}"
            f"{news_block}"
            f"{insider_block}"
            f"{analyst_block}"
            f"{short_block}"
            f"{eps_block}"
            f"{ratio_block}"
            f"Earnings: {earnings}\n"
            f"Market cap: {f.market_cap_bucket}\n"
        )


class PriceWindow(BaseModel):
    """Raw OHLCV bundle from a DataProvider."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    ticker: str
    daily: pd.DataFrame
    hourly: pd.DataFrame
    as_of: datetime
