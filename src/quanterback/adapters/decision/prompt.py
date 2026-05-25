from __future__ import annotations

from pathlib import Path

DECISION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "PASS"]},
        "ticker": {"type": "string"},
        "strategy": {"type": "string", "enum": ["MOMENTUM", "MEAN_REVERSION"]},
        "params": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "lookback_days": {"type": "integer", "minimum": 5, "maximum": 60},
                        "momentum_threshold": {"type": "number", "minimum": 0.0, "maximum": 0.30},
                    },
                    "required": ["lookback_days", "momentum_threshold"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "lookback_days": {"type": "integer", "minimum": 5, "maximum": 60},
                        "entry_z_score": {"type": "number", "minimum": 1.0, "maximum": 4.0},
                    },
                    "required": ["lookback_days", "entry_z_score"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
        "rationale": {"type": "string", "minLength": 20, "maxLength": 2000},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "news_sentiment": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    },
    "required": ["action", "ticker", "strategy", "rationale", "confidence"],
    "additionalProperties": False,
}


def render_prompt(template_path: Path, summary_text: str) -> str:
    template = template_path.read_text()
    if "--SUMMARY--" in template:
        return template.replace("--SUMMARY--", summary_text)
    return template + "\n\n" + summary_text
