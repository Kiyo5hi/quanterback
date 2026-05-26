from __future__ import annotations

from pathlib import Path

import pytest

from quanterback.config import AppConfig

SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample_config.toml"


def _minimal_secrets_toml() -> str:
    """Minimal TOML with all required secrets for tests."""
    return (
        "[llm]\n"
        'anthropic_api_key = "sk-ant-x"\n'
        "[alpaca]\n"
        'api_key = "ak-x"\n'
        'secret = "as-x"\n'
        "[telegram]\n"
        'bot_token = "tg-x"\n'
    )


def test_load_defaults_only(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.position_size_pct == 0.05
    assert cfg.max_total_exposure_pct == 0.30
    assert cfg.max_sector_exposure_pct == 0.10
    assert cfg.sl_atr_multiple == 1.5
    assert cfg.tp_atr_multiple == 3.0
    assert cfg.risk_thresholds.max_drawdown == 0.5
    assert cfg.anthropic_key == "sk-ant-x"


def test_toml_overrides_defaults(tmp_path: Path) -> None:
    # Create a TOML file with secrets + non-secret overrides
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        "[position]\n"
        "position_size_pct = 0.03\n"
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.position_size_pct == 0.03  # overridden
    assert cfg.llm_model == "claude-sonnet-4-6"  # default


def test_missing_secret_raises(tmp_path: Path) -> None:
    # Create TOML with only alpaca & telegram secrets, missing anthropic
    toml = tmp_path / "c.toml"
    toml.write_text(
        "[alpaca]\n"
        'api_key = "x"\n'
        'secret = "x"\n'
        "[telegram]\n"
        'bot_token = "x"\n'
    )
    with pytest.raises(ValueError, match="anthropic_api_key"):
        AppConfig.load(toml_paths=[toml])


def test_local_override_beats_project(tmp_path: Path) -> None:
    project = tmp_path / "p.toml"
    project.write_text(
        _minimal_secrets_toml() +
        "[position]\nposition_size_pct = 0.10\n"
    )
    local = tmp_path / "l.toml"
    local.write_text('[position]\nposition_size_pct = 0.07\n')
    cfg = AppConfig.load(toml_paths=[project, local])
    assert cfg.position_size_pct == 0.07


def test_load_with_ark_provider(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        "[llm]\n"
        'provider = "ark"\n'
        'ark_api_key = "sk-ark-test"\n'
        'model = "deepseek-v3"\n'
        "[alpaca]\n"
        'api_key = "x"\n'
        'secret = "x"\n'
        "[telegram]\n"
        'bot_token = "x"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.llm_provider == "ark"
    assert cfg.ark_api_key == "sk-ark-test"
    assert cfg.llm_model == "deepseek-v3"


def test_load_ark_provider_missing_ark_key_raises(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        '[llm]\nprovider = "ark"\n'
        "[alpaca]\n"
        'api_key = "x"\n'
        'secret = "x"\n'
        "[telegram]\n"
        'bot_token = "x"\n'
    )
    with pytest.raises(ValueError, match="ark_api_key"):
        AppConfig.load(toml_paths=[toml])


def test_load_with_telegram_approval_gate(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[approval]\ngate = "telegram"\ntimeout_seconds = 120\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.approval_gate == "telegram"
    assert cfg.approval_timeout_seconds == 120


def test_load_defaults_approval_to_noop(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.approval_gate == "noop"
    assert cfg.approval_timeout_seconds == 60


def test_secrets_from_toml(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        "[llm]\n"
        'anthropic_api_key = "sk-ant-from-toml"\n'
        "[alpaca]\n"
        'api_key = "ak-from-toml"\n'
        'secret = "as-from-toml"\n'
        "[telegram]\n"
        'bot_token = "tg-from-toml"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.anthropic_key == "sk-ant-from-toml"
    assert cfg.alpaca_key == "ak-from-toml"
    assert cfg.alpaca_secret == "as-from-toml"
    assert cfg.tg_token == "tg-from-toml"


def test_missing_required_secret_message_helpful(tmp_path: Path) -> None:
    # Empty TOML: all secrets missing
    toml = tmp_path / "c.toml"
    toml.write_text("")
    with pytest.raises(ValueError, match=r"\[alpaca\] api_key"):
        AppConfig.load(toml_paths=[toml])


def test_ark_provider_via_toml_secrets(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        '[llm]\nprovider = "ark"\n'
        'ark_api_key = "sk-ark-toml"\n'
        "[alpaca]\n"
        'api_key = "x"\n'
        'secret = "x"\n'
        "[telegram]\n"
        'bot_token = "x"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.llm_provider == "ark"
    assert cfg.ark_api_key == "sk-ark-toml"


def test_load_with_thinking_effort_high(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        "[llm]\n"
        'anthropic_api_key = "sk-ant-x"\n'
        'thinking_effort = "high"\n'
        "[alpaca]\n"
        'api_key = "ak-x"\n'
        'secret = "as-x"\n'
        "[telegram]\n"
        'bot_token = "tg-x"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.llm_thinking_effort == "high"


def test_load_invalid_thinking_effort_rejected(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        "[llm]\n"
        'anthropic_api_key = "sk-ant-x"\n'
        'thinking_effort = "ultra"\n'
        "[alpaca]\n"
        'api_key = "ak-x"\n'
        'secret = "as-x"\n'
        "[telegram]\n"
        'bot_token = "tg-x"\n'
    )
    with pytest.raises(ValueError, match="thinking_effort"):
        AppConfig.load(toml_paths=[toml])


def test_load_pdt_protection_config(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        '[llm]\nanthropic_api_key = "x"\n'
        '[alpaca]\napi_key = "x"\nsecret = "x"\n'
        '[telegram]\nbot_token = "x"\n'
        '[risk.pdt]\nenabled = true\nmin_equity = 30000.0\nmax_day_trades = 2\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.pdt_protection_enabled is True
    assert cfg.pdt_min_equity == 30000.0
    assert cfg.pdt_max_day_trades == 2


def test_pdt_defaults_disabled(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        '[llm]\nanthropic_api_key = "x"\n'
        '[alpaca]\napi_key = "x"\nsecret = "x"\n'
        '[telegram]\nbot_token = "x"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.pdt_protection_enabled is False
    assert cfg.pdt_min_equity == 25000.0
    assert cfg.pdt_max_day_trades == 3


def test_language_default_en(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.language == "en"


def test_load_with_zh_language(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[i18n]\nlanguage = "zh"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.language == "zh"


def test_invalid_language_rejected(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[i18n]\nlanguage = "fr"\n'
    )
    with pytest.raises(ValueError, match="language"):
        AppConfig.load(toml_paths=[toml])


def test_templates_dir_default(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.templates_dir == Path("/config/templates")


def test_templates_dir_override(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[i18n]\ntemplates_dir = "/custom/templates"\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.templates_dir == Path("/custom/templates")


def test_load_trail_percent_config(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[risk.sl_tp]\ntrail_percent = 8.0\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.trail_percent == 8.0


def test_trail_percent_defaults_to_none(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.trail_percent is None


def test_load_exposure_caps_config(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[position]\nmax_total_exposure_pct = 0.40\nmax_sector_exposure_pct = 0.15\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.max_total_exposure_pct == 0.40
    assert cfg.max_sector_exposure_pct == 0.15


def test_exposure_caps_defaults(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.max_total_exposure_pct == 0.30
    assert cfg.max_sector_exposure_pct == 0.10


def test_load_universe_screener_config(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        _minimal_secrets_toml() +
        '[universe]\nenabled = false\npath = "/tmp/u.txt"\ntop_n = 20\n'
    )
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.universe_screener_enabled is False
    assert str(cfg.universe_path) == "/tmp/u.txt"
    assert cfg.universe_top_n == 20


def test_universe_defaults(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(_minimal_secrets_toml())
    cfg = AppConfig.load(toml_paths=[toml])
    assert cfg.universe_screener_enabled is True
    assert cfg.universe_top_n == 10
