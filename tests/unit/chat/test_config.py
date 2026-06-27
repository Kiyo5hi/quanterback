from __future__ import annotations

from pathlib import Path

from quanterback.chat.config import ResearchChatConfig


def test_research_chat_config_does_not_require_alpaca(tmp_path: Path) -> None:
    toml = tmp_path / "chat.toml"
    toml.write_text(
        "[llm]\n"
        'provider = "ark"\n'
        'ark_api_key = "ark-x"\n'
        'model = "doubao-seed-evolving"\n'
        "[telegram]\n"
        'research_bot_token = "tg-research"\n'
        'research_chat_ids = ["1"]\n'
        'research_allowed_user_ids = ["u1"]\n'
        "[capabilities]\n"
        'enabled = ["research.watchlist"]\n'
        "[storage]\n"
        'research_db_path = "/tmp/research.sqlite"\n'
    )

    cfg = ResearchChatConfig.load([toml])

    assert cfg.tg_token == "tg-research"
    assert cfg.tg_allowed_chat_ids == ("1",)
    assert cfg.tg_allowed_user_ids == ("u1",)
    assert cfg.ark_api_key == "ark-x"
    assert cfg.capabilities.enabled == ("research.watchlist",)
    assert str(cfg.db_path) == "/tmp/research.sqlite"
