"""Tests for scan brief rendering."""
from __future__ import annotations

import json

from quanterback.brief import _build_decision_entry


def test_build_decision_entry_buy() -> None:
    """Test _build_decision_entry for a BUY decision."""
    row = {
        "id": 1,
        "ticker": "NVDA",
        "summary_json": json.dumps({
            "technicals": {"rsi_14": 58, "macd_signal": "bullish_cross"},
            "trend_regime": "uptrend",
            "volatility": {"regime": "normal"},
            "volume": {"regime": "elevated"},
            "fundamentals": {"days_to_next_earnings": 18},
            "insider_activity": {
                "n_buys": 1,
                "n_sells": 0,
                "total_buy_usd": 1200000,
            },
            "recent_analyst_actions": [
                {"action": "Upgrade"},
                {"action": "Upgrade"},
            ],
            "short_interest": {"short_pct_of_float": 0.012},
            "eps_trend": {"growth_q_yoy": 2.1},
            "news": [{"title": "NVDA beats Q1"}],
        }),
        "decision_json": json.dumps({
            "action": "BUY",
            "strategy": "MOMENTUM",
            "rationale": "strong setup",
            "confidence": 0.75,
            "news_sentiment": 0.65,
            "params": {
                "qty": 3,
                "entry_price": 852,
                "stop_loss_price": 828,
                "take_profit_price": 920,
            },
        }),
        "rejected_reason": None,
        "llm_model": "claude",
    }
    entry = _build_decision_entry(row)
    assert entry["bucket"] == "BUY"
    assert entry["ticker"] == "NVDA"
    assert entry["strategy"] == "MOMENTUM"
    assert entry["confidence"] == 0.75
    assert entry["rsi"] == 58
    assert entry["macd_signal"] == "bullish_cross"
    assert entry["trend"] == "uptrend"
    assert entry["insider_n_buys"] == 1
    assert entry["insider_n_sells"] == 0
    assert entry["insider_total_buy_usd"] == 1200000
    assert entry["analyst_actions_count"] == 2
    assert "2 upgrades" in entry["analyst_summary"]
    assert entry["short_pct"] == 0.012
    assert entry["eps_growth_q_yoy"] == 2.1
    assert entry["days_to_earnings"] == 18
    assert entry["news_sentiment"] == 0.65
    assert entry["top_news_title"] == "NVDA beats Q1"
    assert "3 sh" in entry["size_info"]
    assert "$852" in entry["size_info"]
    assert "SL" in entry["risk_info"]


def test_build_decision_entry_pass() -> None:
    """Test _build_decision_entry for a PASS decision."""
    row = {
        "id": 2,
        "ticker": "AAPL",
        "summary_json": json.dumps({
            "technicals": {"rsi_14": 78, "macd_signal": "none"},
            "trend_regime": "uptrend",
        }),
        "decision_json": json.dumps({
            "action": "PASS",
            "strategy": "MOMENTUM",
            "rationale": "overbought",
            "confidence": 0.4,
        }),
        "rejected_reason": None,
        "llm_model": "claude",
    }
    entry = _build_decision_entry(row)
    assert entry["bucket"] == "PASS"
    assert entry["ticker"] == "AAPL"
    assert entry["confidence"] == 0.4


def test_build_decision_entry_reject() -> None:
    """Test _build_decision_entry for a rejection."""
    row = {
        "id": 3,
        "ticker": "META",
        "summary_json": "{}",
        "decision_json": "{}",
        "rejected_reason": "risk_gate: sharpe_ratio_below_threshold",
        "llm_model": "n/a",
    }
    entry = _build_decision_entry(row)
    assert entry["bucket"] == "REJ"
    assert entry["ticker"] == "META"
    assert "sharpe_ratio" in entry["reason"]


def test_build_decision_entry_with_agent_debate() -> None:
    """Test _build_decision_entry includes agent_debate when present."""
    row = {
        "id": 4,
        "ticker": "TSLA",
        "summary_json": json.dumps({
            "technicals": {"rsi_14": 55},
            "trend_regime": "uptrend",
        }),
        "decision_json": json.dumps({
            "action": "BUY",
            "strategy": "MOMENTUM",
            "rationale": "multi-agent consensus",
            "confidence": 0.80,
        }),
        "agent_debate_json": json.dumps({
            "fundamentalist": {
                "agent": "fundamentalist",
                "lean": "bullish",
                "confidence": 0.85,
                "key_points": ["revenue growth", "margin expansion"],
                "rationale": "strong fundamentals",
            },
            "technician": {
                "agent": "technician",
                "lean": "bullish",
                "confidence": 0.75,
                "key_points": ["bullish breakout"],
                "rationale": "technical setup solid",
            },
            "sentiment": {
                "agent": "sentiment",
                "lean": "neutral",
                "confidence": 0.50,
                "key_points": [],
                "rationale": "mixed news flow",
            },
        }),
        "rejected_reason": None,
        "llm_model": "claude",
    }
    entry = _build_decision_entry(row)
    assert entry["bucket"] == "BUY"
    assert entry["ticker"] == "TSLA"
    assert entry["agent_debate"] is not None
    assert entry["agent_debate"]["fundamentalist"]["lean"] == "bullish"
    assert entry["agent_debate"]["fundamentalist"]["confidence"] == 0.85
    assert entry["agent_debate"]["technician"]["lean"] == "bullish"
    assert entry["agent_debate"]["sentiment"]["lean"] == "neutral"
