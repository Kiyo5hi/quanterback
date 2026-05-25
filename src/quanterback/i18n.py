"""Jinja2-backed i18n renderer. Templates live in config/templates/<lang>/."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from quanterback.failed_check_labels import humanize, humanize_list, humanize_reject

EXIT_REASON_ZH = {
    "STOP_LOSS": "止损触发",
    "TAKE_PROFIT": "止盈触发",
    "TRAILING_STOP": "追踪止损",
    "MANUAL_CLOSE": "手动平仓",
    "TIMEOUT": "超时退出",
    "UNKNOWN": "未知原因",
    "pending_timeout": "待成交超时(Alpaca 未撮合)",
    "superseded_by_new_submit": "被新订单取代",
    "zombie_cleanup": "僵尸订单清理",
    "manual": "手动处理",
}

EXIT_REASON_EN = {
    "STOP_LOSS": "Stop Loss",
    "TAKE_PROFIT": "Take Profit",
    "TRAILING_STOP": "Trailing Stop",
    "MANUAL_CLOSE": "Manual Close",
    "TIMEOUT": "Timeout",
    "UNKNOWN": "Unknown",
    "pending_timeout": "Pending Timeout (Alpaca unfilled)",
    "superseded_by_new_submit": "Superseded by Newer Submit",
    "zombie_cleanup": "Zombie Cleanup",
    "manual": "Manual",
}


def _exit_reason_zh(reason: str) -> str:
    return EXIT_REASON_ZH.get(reason, reason)


def _exit_reason_en(reason: str) -> str:
    return EXIT_REASON_EN.get(reason, reason)


class I18n:
    """Render Jinja2 templates from config/templates/<lang>/<name>.j2."""

    def __init__(self, language: str, templates_dir: Path) -> None:
        lang_dir = templates_dir / language
        if not lang_dir.exists():
            raise ValueError(
                f"i18n templates not found at {lang_dir} — "
                f"check [i18n] templates_dir + language settings"
            )
        self._language = language
        self._env = Environment(
            loader=FileSystemLoader(str(lang_dir)),
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Register humanize functions as globals and filters for templates
        self._env.globals["humanize_check"] = lambda fc: humanize(fc, self._language)
        self._env.filters["humanize_checks"] = lambda fcs: humanize_list(fcs, self._language)
        self._env.filters["humanize_reject"] = lambda r: humanize_reject(r, self._language)
        # Register exit reason filters
        if self._language == "zh":
            self._env.filters["exit_reason_zh"] = _exit_reason_zh
            self._env.filters["exit_reason"] = _exit_reason_zh
        else:
            self._env.filters["exit_reason_en"] = _exit_reason_en
            self._env.filters["exit_reason"] = _exit_reason_en

    @property
    def language(self) -> str:
        return self._language

    def render(self, template_name: str, **context: object) -> str:
        tpl = self._env.get_template(f"{template_name}.j2")
        return tpl.render(**context)
