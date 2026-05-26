"""Tests for the position management agent (HOLD / TIGHTEN_SL / TRIM_HALF / EXIT_NOW)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.decision.position_management_agent import (
    PositionManagementAgent,
    PositionManagementDecision,
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
from quanterback.interfaces.decision import ChatMessage, ChatResponse, LLMClient


class ScriptedLLMClient(LLMClient):
    """LLM client returning a single scripted response. Records every chat() call."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.calls.append(list(messages))
        return ChatResponse(
            content=self.content,
            model="scripted",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


@pytest.fixture()
def prompts_dir(tmp_path: Path) -> Path:
    """Minimal prompts dir with a position_management_en.md file."""
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "position_management_en.md").write_text(
        "You are the position management agent. Output JSON only."
    )
    (d / "position_management_zh.md").write_text(
        "你是持仓管理代理。仅输出 JSON。"
    )
    return d


@pytest.fixture()
def held_summary() -> CondensedSummary:
    """Minimal CondensedSummary for a held position."""
    return CondensedSummary(
        ticker="AMD",
        as_of=datetime.now(timezone.utc),
        price=PriceSnapshot(
            last_close=172.3,
            return_1d=0.01,
            return_5d=0.03,
            return_20d=0.08,
            return_60d=0.15,
            pct_from_52w_high=-0.05,
            pct_from_52w_low=0.40,
        ),
        moving_averages=MovingAverages(
            sma_20=168.0,
            sma_50=160.0,
            sma_200=140.0,
            pct_above_sma_20=0.025,
            pct_above_sma_50=0.077,
            pct_above_sma_200=0.231,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.30,
            atr_14=4.0,
            atr_pct_of_price=0.023,
            regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(
            last_volume=60_000_000,
            avg_volume_20d=50_000_000,
            volume_ratio=1.2,
            regime=VolumeRegime.ELEVATED,
        ),
        technicals=TechnicalIndicators(
            rsi_14=72.0,
            macd_signal="none",
        ),
        fundamentals=FundamentalLite(
            days_to_next_earnings=30,
            market_cap_bucket="large",
        ),
        trend_regime=TrendRegime.UPTREND,
        momentum_signals=MomentumSignals(
            gap_up_today_pct=0.0,
            is_near_52w_high=False,
            is_breakout_20d_high=False,
            relative_strength_vs_spy_20d=0.02,
            consecutive_up_days=3,
        ),
        intraday=IntradaySignals(
            return_today_pct=0.005,
            return_last_hour_pct=0.001,
            pct_from_intraday_high=-0.005,
            is_above_yesterday_high=True,
            intraday_range_pct_of_atr=0.5,
            consecutive_up_hours=1,
        ),
    )


@pytest.fixture()
def position_context() -> dict:
    return {
        "order_id": 1,
        "ticker": "AMD",
        "qty": 10,
        "days_held": 1.5,
        "entry_price": 160.0,
        "current_price": 172.3,
        "unrealized_pnl_pct": 0.077,
        "current_sl": 152.0,
        "current_tp": 180.0,
    }


def test_trim_half_action_parses_correctly(
    prompts_dir: Path, held_summary: CondensedSummary, position_context: dict
) -> None:
    """Agent should successfully parse a TRIM_HALF response with new_qty_pct."""
    client = ScriptedLLMClient(
        json.dumps({
            "action": "TRIM_HALF",
            "ticker": "AMD",
            "new_sl_price": None,
            "new_qty_pct": 0.5,
            "reasoning": "Up 7.7% from entry; RSI 72 (extended). Lock 50% of gain.",
            "confidence": 0.65,
        })
    )
    agent = PositionManagementAgent(
        llm_client=client, prompts_dir=prompts_dir, language="en"
    )
    decision = agent.evaluate(held_summary, position_context)

    assert decision.action == "TRIM_HALF"
    assert decision.ticker == "AMD"
    assert decision.new_qty_pct == 0.5
    assert decision.new_sl_price is None
    assert decision.confidence == 0.65
    # Verify the agent actually called the LLM (proves the .chat() path is wired)
    assert len(client.calls) == 1


def test_hold_action_parses_without_new_qty_pct(
    prompts_dir: Path, held_summary: CondensedSummary, position_context: dict
) -> None:
    """HOLD response with no new_qty_pct field still parses (defaults to None)."""
    client = ScriptedLLMClient(
        json.dumps({
            "action": "HOLD",
            "ticker": "AMD",
            "new_sl_price": None,
            "reasoning": "Thesis intact; let bracket play out.",
            "confidence": 0.7,
        })
    )
    agent = PositionManagementAgent(
        llm_client=client, prompts_dir=prompts_dir, language="en"
    )
    decision = agent.evaluate(held_summary, position_context)

    assert decision.action == "HOLD"
    assert decision.new_qty_pct is None
    assert decision.new_sl_price is None


def test_tighten_sl_action_parses(
    prompts_dir: Path, held_summary: CondensedSummary, position_context: dict
) -> None:
    """TIGHTEN_SL response with new_sl_price still parses correctly."""
    client = ScriptedLLMClient(
        json.dumps({
            "action": "TIGHTEN_SL",
            "ticker": "AMD",
            "new_sl_price": 168.0,
            "new_qty_pct": None,
            "reasoning": "Up 7.7%; trail SL to 168 to lock half the gain.",
            "confidence": 0.8,
        })
    )
    agent = PositionManagementAgent(
        llm_client=client, prompts_dir=prompts_dir, language="en"
    )
    decision = agent.evaluate(held_summary, position_context)

    assert decision.action == "TIGHTEN_SL"
    assert decision.new_sl_price == 168.0
    assert decision.new_qty_pct is None


def test_agent_calls_chat_on_llm_client(
    prompts_dir: Path, held_summary: CondensedSummary, position_context: dict
) -> None:
    """Regression: agent must call llm_client.chat(), NOT something else.

    This test guards against the silent-fail bug where the strategist was
    passed in as llm_client (strategist has no .chat() -> AttributeError ->
    swallowed -> always HOLD).
    """
    client = ScriptedLLMClient(
        json.dumps({
            "action": "EXIT_NOW",
            "ticker": "AMD",
            "new_sl_price": None,
            "new_qty_pct": None,
            "reasoning": "Thesis broken; exit now.",
            "confidence": 0.9,
        })
    )
    agent = PositionManagementAgent(
        llm_client=client, prompts_dir=prompts_dir, language="en"
    )
    decision = agent.evaluate(held_summary, position_context)

    # The LLM was actually called — silent-fail path was NOT taken.
    assert len(client.calls) == 1
    # And the decision came from the LLM, not from the fail-safe HOLD branch.
    assert decision.action == "EXIT_NOW"
    assert decision.confidence == 0.9
    # The user message must include the position context (entry, current price, P&L).
    user_msg = next(m for m in client.calls[0] if m.role == "user")
    assert "172.3" in user_msg.content  # current price
    assert "160" in user_msg.content    # entry price


def test_invalid_json_falls_back_to_hold(
    prompts_dir: Path, held_summary: CondensedSummary, position_context: dict
) -> None:
    """Garbled LLM output -> fail-safe HOLD with confidence 0."""
    client = ScriptedLLMClient("this is not json at all {{{")
    agent = PositionManagementAgent(
        llm_client=client, prompts_dir=prompts_dir, language="en"
    )
    decision = agent.evaluate(held_summary, position_context)

    assert decision.action == "HOLD"
    assert decision.confidence == 0.0
    assert "Parse error" in decision.reasoning


def test_decision_model_accepts_trim_half_directly() -> None:
    """PositionManagementDecision should accept TRIM_HALF with new_qty_pct."""
    d = PositionManagementDecision(
        action="TRIM_HALF",
        ticker="AMD",
        new_sl_price=None,
        new_qty_pct=0.5,
        reasoning="trim",
        confidence=0.6,
    )
    assert d.action == "TRIM_HALF"
    assert d.new_qty_pct == 0.5
    # new_qty_pct should default to None for back-compat
    d2 = PositionManagementDecision(
        action="HOLD", ticker="AMD", reasoning="r", confidence=0.5,
    )
    assert d2.new_qty_pct is None
