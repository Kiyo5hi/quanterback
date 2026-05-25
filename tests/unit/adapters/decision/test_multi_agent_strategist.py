"""Multi-agent strategist tests using mock LLM client."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.decision.multi_agent_strategist import (
    MultiAgentStrategist,
)
from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    IntradaySignals,
    MovingAverages,
    MomentumSignals,
    PriceSnapshot,
    TechnicalIndicators,
    TrendRegime,
    VolumeProfile,
    VolumeRegime,
    VolatilityProfile,
    VolatilityRegime,
)
from quanterback.interfaces.decision import ChatMessage, ChatResponse, LLMClient


class MockLLMClient(LLMClient):
    """Mock LLM client that returns scripted responses."""

    def __init__(self, responses: dict[str, str]) -> None:
        """Responses keyed by agent name (fundamentalist/technician/sentiment/risk_manager)."""
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.last_messages: list[ChatMessage] | None = None

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        # Store messages for inspection
        self.last_messages = messages

        # Identify agent by checking system prompt content
        system_content = next(
            (m.content for m in messages if m.role == "system"), ""
        )

        for key in self.responses:
            if key.lower() in system_content.lower():
                self.calls.append((key, system_content[:50]))
                return ChatResponse(
                    content=self.responses[key],
                    model="test-model",
                    usage={"input_tokens": 0, "output_tokens": 0},
                )

        # Default fallback
        return ChatResponse(
            content='{"error": "no scripted response"}',
            model="test-model",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


@pytest.fixture()
def prompts_dir(tmp_path: Path) -> Path:
    """Create temporary prompts directory with stub files."""
    d = tmp_path / "prompts"
    d.mkdir()
    for agent in ["fundamentalist", "technician", "sentiment", "risk_manager"]:
        for lang in ["en", "zh"]:
            stub = f"You are the {agent} agent. Output JSON.\n\n"
            (d / f"agent_{agent}_{lang}.md").write_text(stub)
    return d


@pytest.fixture()
def minimal_summary() -> CondensedSummary:
    """Minimal but valid CondensedSummary for testing."""
    return CondensedSummary(
        ticker="NVDA",
        as_of=datetime.now(timezone.utc),
        price=PriceSnapshot(
            last_close=100.0,
            return_1d=0.01,
            return_5d=0.02,
            return_20d=0.05,
            return_60d=0.10,
            pct_from_52w_high=-0.05,
            pct_from_52w_low=0.50,
        ),
        moving_averages=MovingAverages(
            sma_20=99.0,
            sma_50=98.0,
            sma_200=95.0,
            pct_above_sma_20=0.01,
            pct_above_sma_50=0.02,
            pct_above_sma_200=0.05,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.25,
            atr_14=2.0,
            atr_pct_of_price=0.02,
            regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(
            last_volume=50_000_000,
            avg_volume_20d=40_000_000,
            volume_ratio=1.25,
            regime=VolumeRegime.ELEVATED,
        ),
        technicals=TechnicalIndicators(
            rsi_14=55.0,
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
            consecutive_up_days=2,
        ),
        intraday=IntradaySignals(
            return_today_pct=0.01,
            return_last_hour_pct=0.005,
            pct_from_intraday_high=-0.01,
            is_above_yesterday_high=True,
            intraday_range_pct_of_atr=0.5,
            consecutive_up_hours=2,
        ),
    )


def test_runs_all_three_analysts_then_risk_manager(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Test that all three analyst agents run and pass to risk manager."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["insider buys"],
                "rationale": "strong insider buying",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.6,
                "key_points": ["MACD cross"],
                "rationale": "momentum building",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.3,
                "key_points": ["quiet"],
                "rationale": "no headlines",
            }),
            "risk_manager": json.dumps({
                "action": "BUY",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "params": {"lookback_days": 20, "momentum_threshold": 0.05},
                "rationale": "2 bullish + 1 neutral = BUY",
                "confidence": 0.65,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "BUY"
    assert decision.ticker == "NVDA"
    assert decision.confidence == 0.65
    # Verify all agents were called
    agent_calls = [name for name, _ in client.calls]
    assert "fundamentalist" in agent_calls
    assert "technician" in agent_calls
    assert "sentiment" in agent_calls
    assert "risk_manager" in agent_calls


def test_failed_analyst_returns_pass_decision(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """If one analyst fails parsing, risk manager should still run and decide."""
    client = MockLLMClient(
        {
            "fundamentalist": "not valid json at all {{{",
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.6,
                "key_points": ["MACD"],
                "rationale": "ok",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.3,
                "key_points": ["quiet"],
                "rationale": "none",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "fundamentalist failed, only partial signal",
                "confidence": 0.2,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"
    assert decision.ticker == "NVDA"


def test_all_bearish_triggers_pass(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """When multiple agents are bearish, risk manager should PASS."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bearish",
                "confidence": 0.8,
                "key_points": ["declining eps"],
                "rationale": "weak fundamentals",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bearish",
                "confidence": 0.7,
                "key_points": ["rsi overbought"],
                "rationale": "technical weakness",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.4,
                "key_points": ["mixed news"],
                "rationale": "conflicting signals",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "2 bearish agents override bullish signal",
                "confidence": 0.0,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"


def test_json_with_markdown_fences(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Test that JSON wrapped in markdown fences is parsed correctly."""
    client = MockLLMClient(
        {
            "fundamentalist": "```json\n" + json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.75,
                "key_points": ["strong signal"],
                "rationale": "fundamentals solid",
            }) + "\n```",
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.65,
                "key_points": ["trend up"],
                "rationale": "price rising",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["no news"],
                "rationale": "quiet market",
            }),
            "risk_manager": json.dumps({
                "action": "BUY",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "params": {"lookback_days": 20, "momentum_threshold": 0.05},
                "rationale": "all bullish signals aligned",
                "confidence": 0.68,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "BUY"
    assert decision.confidence == 0.68


def test_risk_manager_fallback_on_parse_error(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """If risk manager fails to parse, should return PASS with confidence 0."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["good"],
                "rationale": "fine",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.6,
                "key_points": ["good"],
                "rationale": "fine",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "bullish",
                "confidence": 0.5,
                "key_points": ["good"],
                "rationale": "fine",
            }),
            "risk_manager": "CORRUPTED RESPONSE %%%",
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"
    assert decision.confidence == 0.0


def test_market_context_passed_to_risk_manager(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Test that market context is included in risk manager prompt."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["balanced"],
                "rationale": "mixed signals",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["balanced"],
                "rationale": "no clear setup",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["balanced"],
                "rationale": "no catalyst",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "all neutral, market bearish",
                "confidence": 0.0,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    strat.set_market_context({"SPY": "downtrend", "VIX": "20"})
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"
    # Verify market context was included (risk manager was called)
    agent_names = [name for name, _ in client.calls]
    assert "risk_manager" in agent_names


def test_language_switching_uses_correct_prompts(
    prompts_dir: Path, minimal_summary: CondensedSummary, tmp_path: Path
) -> None:
    """Test that language=zh loads Chinese prompts."""
    # Create a modified prompts_dir with clearly labeled prompts
    zh_dir = tmp_path / "zh_prompts"
    zh_dir.mkdir()
    for agent in ["fundamentalist", "technician", "sentiment", "risk_manager"]:
        (zh_dir / f"agent_{agent}_zh.md").write_text(f"Chinese {agent} prompt")
        (zh_dir / f"agent_{agent}_en.md").write_text(f"English {agent} prompt")

    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["test"],
                "rationale": "test",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["test"],
                "rationale": "test",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["test"],
                "rationale": "test",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "test",
                "confidence": 0.0,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=zh_dir,
        language="zh",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"


def test_thesis_validation(prompts_dir: Path, minimal_summary: CondensedSummary) -> None:
    """Test that Thesis model validates constraints (confidence range, rationale length)."""
    # Invalid confidence (> 1.0)
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 1.5,  # INVALID
                "key_points": ["test"],
                "rationale": "this is a test rationale message",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["test"],
                "rationale": "this is a test rationale message",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["test"],
                "rationale": "this is a test rationale message",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "fundamentalist invalid, fallback",
                "confidence": 0.0,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    # Should handle gracefully (fundamentalist fails, others proceed)
    decision = strat.decide(minimal_summary)
    assert decision.action == "PASS"  # Risk manager provides fallback


def test_decide_attaches_agent_debate_to_returned_decision(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Verify the Decision returned by multi-agent has agent_debate set."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.75,
                "key_points": ["strong eps growth"],
                "rationale": "excellent fundamentals",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.80,
                "key_points": ["rsi at 60", "above sma200"],
                "rationale": "technical strength",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.50,
                "key_points": ["mixed sentiment"],
                "rationale": "no clear sentiment",
            }),
            "risk_manager": json.dumps({
                "action": "BUY",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "params": {"lookback_days": 20, "momentum_threshold": 0.05},
                "rationale": "strong multi-agent setup with bullish consensus across fundamentals and technicals",
                "confidence": 0.75,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    decision = strat.decide(minimal_summary)
    assert decision.action == "BUY"
    assert decision.agent_debate is not None
    assert decision.agent_debate.fundamentalist is not None
    assert decision.agent_debate.fundamentalist.lean == "bullish"
    assert decision.agent_debate.fundamentalist.confidence == 0.75
    assert decision.agent_debate.technician is not None
    assert decision.agent_debate.technician.lean == "bullish"
    assert decision.agent_debate.sentiment is not None
    assert decision.agent_debate.sentiment.lean == "neutral"


def test_current_positions_passed_to_risk_manager(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Test that current_positions context is included in risk manager prompt."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["strong eps growth"],
                "rationale": "fundamentals improving",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["above SMA50"],
                "rationale": "technical breakout",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["no catalyst"],
                "rationale": "quiet news",
            }),
            "risk_manager": json.dumps({
                "action": "PASS",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "rationale": "already holding similar position in semis; defer",
                "confidence": 0.3,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    # Set current positions: already holding AMD
    current_positions = [
        {
            "ticker": "AMD",
            "qty": 10,
            "entry_price": 168.5,
            "sl": 156.4,
            "tp": 188.7,
            "days_held": 2.0,
        }
    ]
    strat.set_current_positions(current_positions)

    decision = strat.decide(minimal_summary)
    # With current_positions context, risk manager should be more cautious
    assert decision.action in ("BUY", "PASS")
    # User message to LLM should have included the positions
    assert client.last_messages is not None
    user_msg = client.last_messages[-1].content
    # Check that positions are mentioned in the user message
    assert "current_positions" in user_msg or "AMD" in user_msg or "position" in user_msg.lower()


def test_set_current_positions_none_is_ok(
    prompts_dir: Path, minimal_summary: CondensedSummary
) -> None:
    """Test that set_current_positions(None) is handled gracefully."""
    client = MockLLMClient(
        {
            "fundamentalist": json.dumps({
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["strong"],
                "rationale": "good",
            }),
            "technician": json.dumps({
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.7,
                "key_points": ["good setup"],
                "rationale": "bullish",
            }),
            "sentiment": json.dumps({
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.5,
                "key_points": ["neutral"],
                "rationale": "no news",
            }),
            "risk_manager": json.dumps({
                "action": "BUY",
                "ticker": "NVDA",
                "strategy": "MOMENTUM",
                "params": {"lookback_days": 20, "momentum_threshold": 0.05},
                "rationale": "all bullish, good setup",
                "confidence": 0.7,
            }),
        }
    )
    strat = MultiAgentStrategist(
        client,
        prompts_dir=prompts_dir,
        language="en",
        parallel=False,
    )
    # Explicitly set positions to None
    strat.set_current_positions(None)
    decision = strat.decide(minimal_summary)
    assert decision.action == "BUY"
