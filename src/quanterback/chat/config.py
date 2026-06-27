from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from quanterback.config import _deep_merge, _validate_language
from quanterback.tools.capabilities import CapabilitySelection


@dataclass(frozen=True)
class ResearchChatConfig:
    tg_token: str
    tg_allowed_chat_ids: tuple[str, ...]
    tg_allowed_user_ids: tuple[str, ...]
    llm_provider: Literal["claude", "ark"]
    anthropic_key: str
    ark_api_key: str | None
    llm_model: str
    llm_temperature: float
    llm_thinking_effort: Literal["off", "low", "medium", "high"]
    agent_parallel: bool
    prompts_dir: Path
    cache_dir: Path
    cache_ttl_hours: int
    db_path: Path
    language: Literal["en", "zh"]
    display_timezone: str
    capabilities: CapabilitySelection

    @classmethod
    def load(cls, toml_paths: list[Path] | None = None) -> "ResearchChatConfig":
        merged: dict = {}
        for path in toml_paths or []:
            if path.exists():
                with path.open("rb") as f:
                    _deep_merge(merged, tomllib.load(f))

        llm = merged.get("llm", {})
        telegram = merged.get("telegram", {})
        data = merged.get("data", {})
        storage = merged.get("storage", {})
        i18n = merged.get("i18n", {})

        provider: Literal["claude", "ark"] = str(llm.get("provider", "claude"))  # type: ignore[assignment]
        if provider not in ("claude", "ark"):
            raise ValueError(f"Unknown llm.provider: {provider}")
        thinking_effort = str(llm.get("thinking_effort", "off"))
        if thinking_effort not in ("off", "low", "medium", "high"):
            raise ValueError("llm.thinking_effort must be one of off/low/medium/high")

        tg_token = str(telegram.get("research_bot_token") or telegram.get("bot_token") or "")
        if not tg_token:
            raise ValueError(
                "Missing secret: [telegram] research_bot_token or bot_token"
            )

        anthropic_key = str(llm.get("anthropic_api_key") or "")
        ark_api_key = str(llm.get("ark_api_key") or "") or None
        if provider == "claude" and not anthropic_key:
            raise ValueError("provider=claude requires [llm] anthropic_api_key")
        if provider == "ark" and not ark_api_key:
            raise ValueError("provider=ark requires [llm] ark_api_key")

        return cls(
            tg_token=tg_token,
            tg_allowed_chat_ids=tuple(
                str(c) for c in telegram.get("research_chat_ids", telegram.get("chat_ids", []))
            ),
            tg_allowed_user_ids=tuple(
                str(u) for u in telegram.get("research_allowed_user_ids", [])
            ),
            llm_provider=provider,
            anthropic_key=anthropic_key,
            ark_api_key=ark_api_key,
            llm_model=str(llm.get("model", "claude-sonnet-4-6")),
            llm_temperature=float(llm.get("temperature", 0.0)),
            llm_thinking_effort=thinking_effort,  # type: ignore[arg-type]
            agent_parallel=bool(llm.get("agent_parallel", True)),
            prompts_dir=Path(llm.get("prompts_dir", "/config/prompts")),
            cache_dir=Path(data.get("cache_dir", "/data/cache")),
            cache_ttl_hours=int(data.get("cache_ttl_hours", 4)),
            db_path=Path(storage.get("research_db_path", storage.get("db_path", "/data/quanterchat.sqlite"))),
            language=_validate_language(i18n.get("language", "en")),
            display_timezone=str(i18n.get("timezone") or "America/Los_Angeles"),
            capabilities=CapabilitySelection.from_toml(merged),
        )
