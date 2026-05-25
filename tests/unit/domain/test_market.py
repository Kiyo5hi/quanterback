from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    IntradaySignals,
    MomentumSignals,
    MovingAverages,
    PriceSnapshot,
    PriceWindow,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)


def _summary() -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL",
        as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(
            last_close=185.42, return_1d=0.008, return_5d=0.032,
            return_20d=0.085, return_60d=-0.021,
            pct_from_52w_high=-0.042, pct_from_52w_low=0.351,
        ),
        moving_averages=MovingAverages(
            sma_20=181.6, sma_50=177.7, sma_200=164.4,
            pct_above_sma_20=0.021, pct_above_sma_50=0.043, pct_above_sma_200=0.128,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.22, atr_14=3.40,
            atr_pct_of_price=0.0183, regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(
            last_volume=80_000_000, avg_volume_20d=50_000_000,
            volume_ratio=1.6, regime=VolumeRegime.ELEVATED,
        ),
        technicals=TechnicalIndicators(rsi_14=58.3, macd_signal="bullish_cross"),
        fundamentals=FundamentalLite(
            days_to_next_earnings=38, market_cap_bucket="large",
        ),
        trend_regime=TrendRegime.UPTREND,
        momentum_signals=MomentumSignals(
            gap_up_today_pct=0.012,
            is_near_52w_high=True,
            is_breakout_20d_high=False,
            relative_strength_vs_spy_20d=0.053,
            consecutive_up_days=3,
        ),
        intraday=IntradaySignals(
            return_today_pct=0.01,
            return_last_hour_pct=0.002,
            pct_from_intraday_high=-0.01,
            is_above_yesterday_high=True,
            intraday_range_pct_of_atr=1.0,
            consecutive_up_hours=2,
        ),
    )


def test_condensed_summary_roundtrip_json() -> None:
    s = _summary()
    data = s.model_dump_json()
    s2 = CondensedSummary.model_validate_json(data)
    assert s2 == s


def test_condensed_summary_to_prompt_text_contains_key_facts() -> None:
    s = _summary()
    text = s.to_prompt_text()
    assert "AAPL" in text
    assert "UPTREND" in text
    assert "RSI(14): 58.3" in text or "RSI(14): 58.30" in text


def test_price_window_validates_dataframes() -> None:
    daily = pd.DataFrame({
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [100],
    })
    hourly = pd.DataFrame({
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [100],
    })
    pw = PriceWindow(
        ticker="AAPL", daily=daily, hourly=hourly,
        as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert len(pw.daily) == 1


def test_rsi_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        TechnicalIndicators(rsi_14=120.0, macd_signal="none")
