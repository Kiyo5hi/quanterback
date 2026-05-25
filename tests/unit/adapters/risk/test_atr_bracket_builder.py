from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.domain.decision import MomentumParams, StrategyDecision
from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    IntradaySignals,
    MomentumSignals,
    MovingAverages,
    PriceSnapshot,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)


def _summary(last_close: float = 100.0, atr_14: float = 2.0) -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL", as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(
            last_close=last_close, return_1d=0.0, return_5d=0.0, return_20d=0.0,
            return_60d=0.0, pct_from_52w_high=0.0, pct_from_52w_low=0.0,
        ),
        moving_averages=MovingAverages(
            sma_20=100, sma_50=100, sma_200=100,
            pct_above_sma_20=0, pct_above_sma_50=0, pct_above_sma_200=0,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.2, atr_14=atr_14,
            atr_pct_of_price=atr_14 / last_close, regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(last_volume=1_000_000, avg_volume_20d=1_000_000,
                             volume_ratio=1.0, regime=VolumeRegime.NORMAL),
        technicals=TechnicalIndicators(rsi_14=50, macd_signal="none"),
        fundamentals=FundamentalLite(days_to_next_earnings=None, market_cap_bucket="large"),
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


def _decision() -> StrategyDecision:
    return StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish setup with elevated volume confirming the trend strength",
        confidence=0.7,
    )


def test_sl_tp_uses_atr_multiples() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=10_000.0)
    assert spec.stop_loss_price == 96.0     # 100 - 2*2
    assert spec.take_profit_price == 108.0  # 100 + 4*2


def test_qty_uses_position_size_pct() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=10_000.0)
    # 5% of 10000 = 500; 500 / 100 = 5
    assert spec.qty == 5


def test_qty_at_least_one() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.005,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=1_000.0)
    assert spec.qty >= 1


def test_size_multiplier_scales_qty() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0, position_size_pct=0.05,
    )
    spec_full = builder.build(_decision(), _summary(last_close=100, atr_14=2),
                                 account_value=10_000, size_multiplier=1.0)
    spec_half = builder.build(_decision(), _summary(last_close=100, atr_14=2),
                                 account_value=10_000, size_multiplier=0.5)
    assert spec_full.qty == 5
    assert spec_half.qty == 2


def test_size_multiplier_keeps_qty_at_least_one() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100, atr_14=2),
                          account_value=1_000, size_multiplier=0.25)
    assert spec.qty == 1


def test_trail_percent_propagates_to_spec() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0,
        position_size_pct=0.05, trail_percent=8.0,
    )
    spec = builder.build(_decision(), _summary(last_close=100, atr_14=2),
                         account_value=10_000)
    assert spec.trail_percent == 8.0


def test_no_trail_percent_when_unset() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100, atr_14=2),
                         account_value=10_000)
    assert spec.trail_percent is None
