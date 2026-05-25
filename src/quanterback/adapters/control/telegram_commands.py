"""Register the bot's command menu via setMyCommands. Called once at startup."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

# (command, default English description, zh-CN description)
COMMANDS: list[tuple[str, str, str]] = [
    ("status",    "Show current system mode and recent activity",
                  "查看系统状态和最近活动"),
    ("scan",      "Trigger an immediate scan: /scan AAPL,MSFT or /scan (all)",
                  "手动触发扫描: /scan AAPL,MSFT 或 /scan (全部)"),
    ("rescan",    "Re-scan entire watchlist",
                  "重新扫描整个监视列表"),
    ("watchlist", "Manage watchlist: /watchlist [list|add|remove] [SYM]",
                  "管理监视列表: /watchlist [list|add|remove] [SYM]"),
    ("add",       "Add ticker to watchlist: /add AAPL",
                  "添加股票到监视列表: /add AAPL"),
    ("remove",    "Remove ticker from watchlist: /remove AAPL",
                  "从监视列表移除股票: /remove AAPL"),
    ("freeze",    "Stop submitting orders to broker (scans still run)",
                  "停止下单到交易所 (扫描继续)"),
    ("unfreeze",  "Resume submitting orders",
                  "恢复正常下单"),
    ("halt",      "Stop all scans until /unhalt",
                  "完全停止扫描,直到 /unhalt"),
    ("unhalt",    "Resume scans after a halt",
                  "halt 后恢复扫描"),
]


def register_commands(token: str) -> None:
    """Call Bot API setMyCommands for default + zh language_code.

    Logs warnings on failure (best-effort; bot still functions without menu).
    """
    endpoint = f"https://api.telegram.org/bot{token}/setMyCommands"

    # Default (English) registration
    payload_en: dict = {
        "commands": [
            {"command": cmd, "description": desc_en}
            for cmd, desc_en, _ in COMMANDS
        ],
    }
    _post(endpoint, payload_en, "default")

    # Chinese registration (TG matches user's preferred language)
    payload_zh: dict = {
        "commands": [
            {"command": cmd, "description": desc_zh}
            for cmd, _, desc_zh in COMMANDS
        ],
        "language_code": "zh",
    }
    _post(endpoint, payload_zh, "zh")


def _post(endpoint: str, payload: dict, label: str) -> None:
    try:
        r = requests.post(endpoint, json=payload, timeout=10)
        if r.status_code >= 300:
            log.warning("setMyCommands(%s) failed: HTTP %s — %s",
                        label, r.status_code, r.text[:200])
        else:
            log.info("Registered %d commands (%s)",
                     len(payload["commands"]), label)
    except Exception as e:
        log.warning("setMyCommands(%s) error: %s", label, e)
