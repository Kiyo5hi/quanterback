from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.decision.prompted_strategist import (
    PromptedLLMStrategist,
)
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
from tests.fakes.llm_client import FakeLLMClient


def _summary() -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL", as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(last_close=185.42, return_1d=0.008, return_5d=0.032,
                            return_20d=0.085, return_60d=-0.021,
                            pct_from_52w_high=-0.042, pct_from_52w_low=0.351),
        moving_averages=MovingAverages(
            sma_20=181.6, sma_50=177.7, sma_200=164.4,
            pct_above_sma_20=0.021, pct_above_sma_50=0.043, pct_above_sma_200=0.128,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(realized_vol_20d_annualized=0.22, atr_14=3.40,
                                     atr_pct_of_price=0.0183,
                                     regime=VolatilityRegime.NORMAL),
        volume=VolumeProfile(last_volume=80_000_000, avg_volume_20d=50_000_000,
                              volume_ratio=1.6, regime=VolumeRegime.ELEVATED),
        technicals=TechnicalIndicators(rsi_14=58.3, macd_signal="bullish_cross"),
        fundamentals=FundamentalLite(days_to_next_earnings=38, market_cap_bucket="large"),
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


@pytest.fixture()
def tpl(tmp_path: Path) -> Path:
    p = tmp_path / "tpl.md"
    p.write_text("SYSTEM PROMPT\n--SUMMARY--")
    return p


def test_strategist_parses_buy_response(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"BUY","ticker":"AAPL","strategy":"MOMENTUM",'
        '"params":{"lookback_days":20,"momentum_threshold":0.05},'
        '"rationale":"bullish alignment with elevated volume confirms momentum",'
        '"confidence":0.7}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    out = s.decide(_summary())
    assert out.action == "BUY"
    assert out.params is not None
    assert out.params.lookback_days == 20


def test_strategist_parses_pass_response(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"PASS","ticker":"AAPL","strategy":"MOMENTUM","params":null,'
        '"rationale":"already extended above SMA200 by more than 12 percent",'
        '"confidence":0.4}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    out = s.decide(_summary())
    assert out.action == "PASS"
    assert out.params is None


def test_strategist_includes_summary_in_prompt(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"PASS","ticker":"AAPL","strategy":"MOMENTUM","params":null,'
        '"rationale":"too extended above moving averages, wait for pullback",'
        '"confidence":0.4}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    s.decide(_summary())
    assert fake.last_messages is not None
    user_msg = next(m for m in fake.last_messages if m.role == "user")
    assert "AAPL" in user_msg.content
    assert fake.last_schema is not None


def test_strategist_raises_on_invalid_json(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content="not json at all")
    s = PromptedLLMStrategist(fake, tpl)
    with pytest.raises(ValueError, match="LLM output"):
        s.decide(_summary())


def test_market_context_appears_in_user_message(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"PASS","ticker":"AAPL","strategy":"MOMENTUM","params":null,'
        '"rationale":"flat market with no setup at the moment",'
        '"confidence":0.4}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    s.set_market_context({"spy_trend": "downtrend", "spy_pct_from_52w_high": "-12.0%"})
    s.decide(_summary())
    user_msg = next(m for m in fake.last_messages if m.role == "user")
    assert "Market context:" in user_msg.content
    assert "spy_trend: downtrend" in user_msg.content
