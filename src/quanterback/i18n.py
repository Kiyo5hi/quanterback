"""Jinja2-backed i18n renderer. Templates live in config/templates/<lang>/."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

    def __init__(self, language: str, templates_dir: Path, display_timezone: str = "America/Los_Angeles") -> None:
        lang_dir = templates_dir / language
        if not lang_dir.exists():
            raise ValueError(
                f"i18n templates not found at {lang_dir} — "
                f"check [i18n] templates_dir + language settings"
            )
        self._language = language
        self._display_tz_name = display_timezone
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
        # Register timezone-aware datetime filters
        self._env.filters["tz"] = self._to_display_tz
        self._env.filters["fmt_dt"] = self._fmt_dt
        self._env.filters["fmt_date"] = self._fmt_date
        self._env.filters["fmt_time"] = self._fmt_time

    def _to_display_tz(self, dt: datetime | None) -> datetime | None:
        """Convert a UTC-aware datetime to user display timezone."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            # naive — assume UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(self._display_tz_name))

    def _fmt_dt(self, dt: datetime | None, pattern: str = "%Y-%m-%d %H:%M %Z") -> str:
        """Format datetime in display timezone."""
        if dt is None:
            return ""
        converted = self._to_display_tz(dt)
        return converted.strftime(pattern) if converted else ""

    def _fmt_date(self, dt: datetime | None) -> str:
        """Format date only in display timezone."""
        if dt is None:
            return ""
        converted = self._to_display_tz(dt)
        return converted.strftime("%Y-%m-%d") if converted else ""

    def _fmt_time(self, dt: datetime | None) -> str:
        """Format time only in display timezone."""
        if dt is None:
            return ""
        converted = self._to_display_tz(dt)
        return converted.strftime("%H:%M %Z") if converted else ""

    def now_display(self) -> str:
        """Current time formatted in display timezone."""
        return self.format_dt(datetime.now(tz=timezone.utc))

    def format_dt(self, dt: datetime | None, pattern: str = "%Y-%m-%d %H:%M %Z") -> str:
        """Format a datetime in display timezone (Python helper for non-template code)."""
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(self._display_tz_name)).strftime(pattern)

    @property
    def language(self) -> str:
        return self._language

    def render(self, template_name: str, **context: object) -> str:
        tpl = self._env.get_template(f"{template_name}.j2")
        return tpl.render(**context)
