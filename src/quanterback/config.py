from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from quanterback.domain.risk import RiskThresholds


@dataclass(frozen=True)
class AppConfig:
    # secrets (TOML-only)
    anthropic_key: str
    ark_api_key: str | None
    alpaca_key: str
    alpaca_secret: str
    tg_token: str

    # scan
    watchlist_path: Path
    universe_path: Path
    universe_top_n: int
    force_scan_when_closed: bool

    # market benchmark — ETF tracking the broad market for trend / RS / B&H
    # comparison. Default VOO (Vanguard S&P 500, expense 0.03%); can swap to
    # SPY, IVV, VTI, QQQ, etc.
    benchmark_ticker: str

    # position sizing
    position_size_pct: float
    max_total_exposure_pct: float
    max_sector_exposure_pct: float

    # SL/TP
    sl_atr_multiple: float
    tp_atr_multiple: float
    trail_percent: float | None

    # risk gate
    risk_thresholds: RiskThresholds
    pdt_protection_enabled: bool
    pdt_min_equity: float
    pdt_max_day_trades: int

    # backtest
    backtest_lookback_years: int

    # llm
    llm_provider: Literal["claude", "ark"]
    llm_model: str
    llm_temperature: float
    llm_thinking_effort: Literal["off", "low", "medium", "high"]
    prompt_template_path: Path
    strategist_mode: Literal["single", "multi_agent"]
    prompts_dir: Path
    agent_parallel: bool

    # data
    cache_dir: Path
    cache_ttl_hours: int

    # telegram
    tg_chat_ids: tuple[str, ...]
    notifier_retry_window_hours: int

    # approval
    approval_gate: Literal["noop", "telegram"]
    approval_timeout_seconds: int

    # storage
    db_path: Path

    # i18n
    language: Literal["en", "zh"]
    templates_dir: Path
    display_timezone: str  # e.g., "America/Los_Angeles"; display only, storage stays UTC

    # position tracker
    position_tracker_enabled: bool
    position_tracker_lookback_hours: int

    # position management
    position_management_enabled: bool
    position_management_min_age_hours: float
    position_management_reeval_interval_hours: float

    # watchlist auto-management
    watchlist_auto_enabled: bool
    watchlist_promote_min_buys: int
    watchlist_promote_window_days: int
    watchlist_demote_max_quiet_days: int

    @classmethod
    def load(cls, toml_paths: list[Path] | None = None) -> AppConfig:
        toml_paths = toml_paths or []
        merged: dict = {}
        for p in toml_paths:
            if p.exists():
                with p.open("rb") as f:
                    _deep_merge(merged, tomllib.load(f))

        scan = merged.get("scan", {})
        universe = merged.get("universe", {})
        position = merged.get("position", {})
        position_tracker = merged.get("position_tracker", {})
        position_management = merged.get("position_management", {})
        watchlist_auto = merged.get("watchlist_auto", {})
        risk_section = merged.get("risk", {})
        risk_sl_tp = risk_section.get("sl_tp", {})
        risk_th = risk_section.get("thresholds", {})
        pdt_section = risk_section.get("pdt", {})
        backtest = merged.get("backtest", {})
        llm = merged.get("llm", {})
        thinking_effort = str(llm.get("thinking_effort", "off"))
        if thinking_effort not in ("off", "low", "medium", "high"):
            raise ValueError(
                f"Invalid llm.thinking_effort: {thinking_effort!r}; "
                "must be one of off/low/medium/high"
            )
        data = merged.get("data", {})
        telegram = merged.get("telegram", {})
        approval = merged.get("approval", {})
        storage = merged.get("storage", {})
        alpaca = merged.get("alpaca", {})
        i18n = merged.get("i18n", {})

        # Helper to read secret from TOML section
        def _get(section: dict, toml_key: str) -> str:
            """Read from TOML section, else empty."""
            val = section.get(toml_key)
            return str(val) if val else ""

        # Determine provider and validate secrets accordingly
        provider: Literal["claude", "ark"] = str(llm.get("provider", "claude"))  # type: ignore[assignment]
        if provider not in ("claude", "ark"):
            raise ValueError(f"Unknown llm.provider: {provider}")

        # Read secrets from their logical sections
        anthropic_key = _get(llm, "anthropic_api_key")
        ark_api_key = _get(llm, "ark_api_key") or None
        alpaca_key = _get(alpaca, "api_key")
        alpaca_secret = _get(alpaca, "secret")
        tg_token = _get(telegram, "bot_token")

        # Validate required secrets
        if not alpaca_key:
            raise ValueError(
                "Missing secret: [alpaca] api_key — set in config/quanterback.local.toml"
            )
        if not alpaca_secret:
            raise ValueError(
                "Missing secret: [alpaca] secret — set in config/quanterback.local.toml"
            )
        if not tg_token:
            raise ValueError(
                "Missing secret: [telegram] bot_token — set in config/quanterback.local.toml"
            )
        if provider == "claude" and not anthropic_key:
            raise ValueError(
                "provider=claude requires [llm] anthropic_api_key in config/quanterback.local.toml"
            )
        if provider == "ark" and not ark_api_key:
            raise ValueError(
                "provider=ark requires [llm] ark_api_key in config/quanterback.local.toml"
            )

        trail_percent_val = risk_sl_tp.get("trail_percent")
        trail_percent = float(trail_percent_val) if trail_percent_val is not None else None
        instance = cls(
            anthropic_key=anthropic_key,
            ark_api_key=ark_api_key,
            alpaca_key=alpaca_key,
            alpaca_secret=alpaca_secret,
            tg_token=tg_token,
            watchlist_path=Path(scan.get("watchlist_path", "/config/watchlist.txt")),
            universe_path=Path(universe.get("path", "/config/universe.txt")),
            benchmark_ticker=str(merged.get("market", {}).get("benchmark_ticker") or "VOO"),
            universe_top_n=int(universe.get("top_n", 10)),
            force_scan_when_closed=bool(scan.get("force_scan_when_closed", False)),
            position_size_pct=float(position.get("position_size_pct", 0.05)),
            max_total_exposure_pct=float(position.get("max_total_exposure_pct", 0.30)),
            max_sector_exposure_pct=float(position.get("max_sector_exposure_pct", 0.10)),
            sl_atr_multiple=float(risk_sl_tp.get("sl_atr_multiple", 1.5)),
            tp_atr_multiple=float(risk_sl_tp.get("tp_atr_multiple", 3.0)),
            trail_percent=trail_percent,
            risk_thresholds=RiskThresholds(
                max_drawdown=float(risk_th.get("max_drawdown", 0.50)),
                min_sharpe=float(risk_th.get("min_sharpe", -0.5)),
                min_win_rate=float(risk_th.get("min_win_rate", 0.0)),
                min_profit_factor=float(risk_th.get("min_profit_factor", 0.0)),
                min_num_trades=int(risk_th.get("min_num_trades", 5)),
            ),
            pdt_protection_enabled=bool(pdt_section.get("enabled", False)),
            pdt_min_equity=float(pdt_section.get("min_equity", 25_000.0)),
            pdt_max_day_trades=int(pdt_section.get("max_day_trades", 3)),
            backtest_lookback_years=int(backtest.get("lookback_years", 3)),
            llm_provider=provider,
            llm_model=str(llm.get("model", "claude-sonnet-4-6")),
            llm_temperature=float(llm.get("temperature", 0.0)),
            llm_thinking_effort=thinking_effort,  # type: ignore[arg-type]
            prompt_template_path=Path(
                llm.get("prompt_template_path", "/config/prompts/momentum_strategist.md")
            ),
            strategist_mode=str(llm.get("strategist_mode", "multi_agent")),  # type: ignore[arg-type]
            prompts_dir=Path(llm.get("prompts_dir", "/config/prompts")),
            agent_parallel=bool(llm.get("agent_parallel", True)),
            cache_dir=Path(data.get("cache_dir", "/data/cache")),
            cache_ttl_hours=int(data.get("cache_ttl_hours", 4)),
            tg_chat_ids=tuple(str(c) for c in telegram.get("chat_ids", [])),
            notifier_retry_window_hours=int(telegram.get("retry_window_hours", 1)),
            approval_gate=str(approval.get("gate", "noop")),  # type: ignore[arg-type]
            approval_timeout_seconds=int(approval.get("timeout_seconds", 60)),
            db_path=Path(storage.get("db_path", "/data/quanterback.sqlite")),
            language=_validate_language(i18n.get("language", "en")),
            templates_dir=Path(i18n.get("templates_dir", "/config/templates")),
            display_timezone=str(i18n.get("timezone") or "America/Los_Angeles"),
            position_tracker_enabled=bool(position_tracker.get("enabled", True)),
            position_tracker_lookback_hours=int(position_tracker.get("lookback_hours", 48)),
            position_management_enabled=bool(position_management.get("enabled", False)),
            position_management_min_age_hours=float(position_management.get("min_age_hours_before_eval", 6.0)),
            position_management_reeval_interval_hours=float(position_management.get("reeval_interval_hours", 4.0)),
            watchlist_auto_enabled=bool(watchlist_auto.get("enabled", True)),
            watchlist_promote_min_buys=int(watchlist_auto.get("promote_min_buys", 3)),
            watchlist_promote_window_days=int(watchlist_auto.get("promote_window_days", 7)),
            watchlist_demote_max_quiet_days=int(watchlist_auto.get("demote_max_quiet_days", 14)),
        )
        # Set universe dynamic fields on frozen instance using object.__setattr__
        object.__setattr__(instance, "universe_screener_enabled", bool(universe.get("enabled", True)))
        object.__setattr__(instance, "universe_top_n_bullish", int(universe.get("top_n_bullish", 15)))
        object.__setattr__(instance, "universe_top_n_neutral", int(universe.get("top_n_neutral", 10)))
        object.__setattr__(instance, "universe_top_n_bearish", int(universe.get("top_n_bearish", 5)))
        object.__setattr__(instance, "universe_dynamic_top_n", bool(universe.get("dynamic_top_n", False)))
        return instance


def _validate_language(lang: object) -> Literal["en", "zh"]:
    lang_str = str(lang)
    if lang_str not in ("en", "zh"):
        raise ValueError(
            f"Invalid [i18n] language: {lang_str!r}; must be en or zh"
        )
    return lang_str  # type: ignore[return-value]


def _deep_merge(base: dict, overlay: dict) -> None:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
