"""CLI entrypoint. Composes all adapters via wire() and dispatches subcommands."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from quanterback.adapters.control.telegram_control_channel import (
    TelegramControlChannel,
)
from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.adapters.data.yfinance_provider import YFinanceProvider
from quanterback.adapters.decision.ark_client import ArkClient
from quanterback.adapters.decision.cached_llm_client import CachedLLMClient
from quanterback.adapters.decision.claude_client import ClaudeClient
from quanterback.adapters.decision.multi_agent_strategist import MultiAgentStrategist
from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.adapters.decision.prompted_strategist import PromptedLLMStrategist
from quanterback.adapters.decision.telegram_approval_gate import TelegramApprovalGate
from quanterback.adapters.events.composite_event_source import CompositeEventSource
from quanterback.adapters.events.universe_screener_event_source import (
    UniverseScreenerEventSource,
)
from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource
from quanterback.adapters.execution.alpaca_broker import (
    AlpacaPaperBroker,
)
from quanterback.adapters.lifecycle.watchlist_auto_manager import (
    WatchlistAutoManager,
)
from quanterback.adapters.notify.buffered_telegram_notifier import (
    BufferedTelegramNotifier,
)
from quanterback.adapters.notify.telegram_notifier import TelegramNotifier
from quanterback.adapters.position.sqlite_alpaca_synced_state import (
    SqliteAlpacaSyncedPositionState,
)
from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.adapters.risk.pdt_aware_risk_gate import PdtAwareRiskGate
from quanterback.adapters.risk.sector_concurrency_risk_gate import (
    SectorConcurrencyRiskGate,
)
from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.config import AppConfig
from quanterback.domain.persisted import PersistedUserTrigger
from quanterback.i18n import I18n
from quanterback.interfaces.decision import ApprovalGate, LLMClient, LLMStrategist
from quanterback.interfaces.risk import RiskGate
from quanterback.pipeline import ScanPipeline
from quanterback.report import generate_report

log = logging.getLogger("quanterback")


def wire(config: AppConfig) -> tuple[ScanPipeline, SqliteSystemStateService, str]:
    store = SqliteStore(config.db_path, watchlist_path=config.watchlist_path)
    sys_state = SqliteSystemStateService(store)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    notifier = BufferedTelegramNotifier(
        token=config.tg_token, chat_ids=config.tg_chat_ids, store=store, i18n=i18n,
    )
    data_provider = YFinanceProvider(
        cache_dir=config.cache_dir, cache_ttl_hours=config.cache_ttl_hours,
    )
    summarizer = RuleBasedSummarizer()
    if config.llm_provider == "ark":
        assert config.ark_api_key is not None
        base_llm: LLMClient = ArkClient(
            api_key=config.ark_api_key, model=config.llm_model,
            thinking_effort=config.llm_thinking_effort,
        )
    else:
        base_llm = ClaudeClient(
            api_key=config.anthropic_key, model=config.llm_model,
            thinking_effort=config.llm_thinking_effort,
        )
    # Wrap with in-memory cache so repeat queries (e.g., same ticker re-asked
    # within a manual /scan triggered by Telegram) don't re-bill tokens.
    llm_client: LLMClient = CachedLLMClient(wrapped=base_llm)

    strategist: LLMStrategist
    if config.strategist_mode == "multi_agent":
        strategist = MultiAgentStrategist(
            llm_client,
            prompts_dir=config.prompts_dir,
            language=config.language,
            parallel=config.agent_parallel,
        )
    else:
        strategist = PromptedLLMStrategist(
            llm_client, prompt_template_path=config.prompt_template_path,
            temperature=config.llm_temperature,
        )
    approval_gate: ApprovalGate
    if config.approval_gate == "telegram":
        approval_gate = TelegramApprovalGate(
            token=config.tg_token, chat_ids=config.tg_chat_ids,
            timeout_seconds=config.approval_timeout_seconds,
        )
    else:
        approval_gate = NoOpApprovalGate()
    backtester = VectorizedBacktester(data_provider)
    order_builder = ATRBracketOrderBuilder(
        sl_atr_multiple=config.sl_atr_multiple,
        tp_atr_multiple=config.tp_atr_multiple,
        position_size_pct=config.position_size_pct,
        trail_percent=config.trail_percent,
    )
    executor = AlpacaPaperBroker(
        api_key=config.alpaca_key, secret=config.alpaca_secret,
    )
    risk_gate: RiskGate = CompositeRiskGate()
    if config.sector_concurrency_enabled:
        risk_gate = SectorConcurrencyRiskGate(
            inner=risk_gate, store=store,
            max_per_sector=config.sector_max_per_sector,
        )
    if config.pdt_protection_enabled:
        risk_gate = PdtAwareRiskGate(
            inner=risk_gate, executor=executor,
            min_equity=config.pdt_min_equity,
            max_day_trades=config.pdt_max_day_trades,
        )
    position_state = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    watchlist = WatchlistEventSource(config.watchlist_path, store=store)
    screener: UniverseScreenerEventSource | None = None
    if config.universe_screener_enabled:
        screener = UniverseScreenerEventSource(
            universe_path=config.universe_path,
            hist_provider=data_provider,
            top_n=config.universe_top_n,
        )
    event_source = CompositeEventSource(
        watchlist=watchlist, store=store, screener=screener,
    )
    watchlist_auto_manager = None
    if config.watchlist_auto_enabled:
        watchlist_auto_manager = WatchlistAutoManager(
            store=store,
            promote_min_buys=config.watchlist_promote_min_buys,
            promote_window_days=config.watchlist_promote_window_days,
            demote_max_quiet_days=config.watchlist_demote_max_quiet_days,
            enabled=config.watchlist_auto_enabled,
        )
    pipeline = ScanPipeline(
        event_source=event_source,
        data_provider=data_provider,
        summarizer=summarizer,
        strategist=strategist,
        approval_gate=approval_gate,
        position_state=position_state,
        backtester=backtester,
        risk_gate=risk_gate,
        order_builder=order_builder,
        executor=executor,
        notifier=notifier,
        state_store=store,
        system_state=sys_state,
        thresholds=config.risk_thresholds,
        backtest_lookback_years=config.backtest_lookback_years,
        max_concurrent_positions=config.max_concurrent_positions,
        macro_data_provider=data_provider,
        news_provider=data_provider,
        fundamentals_provider=data_provider,
        watchlist_auto_manager=watchlist_auto_manager,
        config=config,
    )
    return pipeline, sys_state, config.tg_token


def _load_config() -> AppConfig:
    toml_paths = [
        Path("/config/quanterback.toml"),
        Path("/config/quanterback.local.toml"),
    ]
    return AppConfig.load(toml_paths=toml_paths)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _check_market_or_explain(executor, config, i18n: I18n | None = None) -> str | None:
    """Returns None if scan should proceed; returns user-facing message if market closed.

    Honors force_scan_when_closed config flag.
    """
    if getattr(config, "force_scan_when_closed", False):
        return None
    try:
        if executor.is_market_open():
            return None
        next_open = executor.next_market_open()
        if next_open is not None:
            if i18n:
                next_open_str = i18n.format_dt(next_open, "%Y-%m-%d %H:%M %Z")
            else:
                next_open_str = next_open.strftime("%Y-%m-%d %H:%M %Z")
            return f"⚠️ 市场关闭，scan 跳过。下次开市: {next_open_str}"
        return "⚠️ 市场关闭，scan 跳过。"
    except Exception as e:
        log.warning("Market-open check failed: %s — proceeding cautiously", e)
        return None


def cmd_scan(args: argparse.Namespace) -> int:
    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    store = SqliteStore(config.db_path, watchlist_path=config.watchlist_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)

    dry_run = getattr(args, "dry_run", False)
    if not dry_run:
        closed_msg = _check_market_or_explain(pipeline.executor, config, i18n)
        if closed_msg is not None:
            print(closed_msg)
            return 0

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        ticker_str = ",".join(tickers)
        dry_prefix = "[DRY] " if dry_run else ""
        trigger_label = f"{dry_prefix}/scan {ticker_str[:30]}"
        scan_run_id = pipeline.run_for_tickers(tickers, trigger_label=trigger_label, force_dry_run=dry_run)
    else:
        scan_run_id = pipeline.run(force_dry_run=dry_run)

    # Render output in requested format
    if args.format == "brief":
        from quanterback.brief import render_scan_brief
        output = render_scan_brief(None, i18n, store, config, scan_run_id=scan_run_id)
        print(output)

    return 0


def cmd_rescan(args: argparse.Namespace) -> int:
    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    store = SqliteStore(config.db_path, watchlist_path=config.watchlist_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)

    dry_run = getattr(args, "dry_run", False)
    if not dry_run:
        closed_msg = _check_market_or_explain(pipeline.executor, config, i18n)
        if closed_msg is not None:
            print(closed_msg)
            return 0

    # Read watchlist and extract tickers
    entries = store.list_watchlist()
    tickers = [e.ticker for e in entries][: args.limit]

    if not tickers:
        print("(watchlist is empty)")
        return 0

    print(f"Rescanning {len(tickers)} watchlist tickers: {', '.join(tickers)}")

    # Log a warning if over soft threshold
    if len(tickers) > 20:
        log.warning(
            "Rescan will use ~%d LLM calls (~$%.2f) — over the soft warn threshold",
            len(tickers) * 4,
            len(tickers) * 4 * 0.0013,
        )

    # Run pipeline for these tickers
    dry_prefix = "[DRY] " if dry_run else ""
    scan_run_id = pipeline.run_for_tickers(tickers, trigger_label=f"{dry_prefix}/rescan", force_dry_run=dry_run)

    # Render output in requested format
    if args.format == "brief":
        from quanterback.brief import render_scan_brief
        output = render_scan_brief(None, i18n, store, config, scan_run_id=scan_run_id)
        print(output)

    return 0


def cmd_control_bot(_args: argparse.Namespace) -> int:
    _setup_logging()
    config = _load_config()
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    _, sys_state, token = wire(config)
    store = SqliteStore(config.db_path, watchlist_path=config.watchlist_path)

    from quanterback.adapters.control.telegram_commands import register_commands
    register_commands(token)  # best-effort

    import subprocess

    import requests
    send_endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    edit_endpoint = f"https://api.telegram.org/bot{token}/editMessageText"

    def reply(cmd_or_chat, text: str, reply_to: int = 0) -> int:
        """Best-effort send. Returns the new message_id (0 on failure).

        Pass a ControlCommand to auto-thread under the original /command;
        or pass a chat_id string for non-reply contexts.
        """
        if hasattr(cmd_or_chat, "chat_id"):
            chat_id = cmd_or_chat.chat_id or cmd_or_chat.actor
            reply_to = reply_to or cmd_or_chat.message_id
        else:
            chat_id = str(cmd_or_chat)
        try:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            if reply_to:
                payload["reply_to_message_id"] = reply_to
                payload["allow_sending_without_reply"] = True
            resp = requests.post(send_endpoint, json=payload, timeout=10)
            if not resp.ok:
                # Markdown parse errors are common; retry as plain text so
                # the user never loses the message entirely.
                log.warning("reply send failed %d: %s | retrying as plain text",
                            resp.status_code, resp.text[:200])
                payload.pop("parse_mode", None)
                resp = requests.post(send_endpoint, json=payload, timeout=10)
                if not resp.ok:
                    log.error("reply plain retry also failed %d: %s",
                              resp.status_code, resp.text[:200])
                    return 0
            data = resp.json()
            return int(data.get("result", {}).get("message_id", 0))
        except Exception as e:
            log.warning("reply send exception: %s", e)
            return 0

    def edit(chat_id: str, message_id: int, text: str) -> None:
        """Best-effort editMessageText. Falls back to plain text on Markdown errors."""
        if not message_id:
            return
        try:
            payload = {
                "chat_id": chat_id, "message_id": message_id,
                "text": text, "parse_mode": "Markdown",
            }
            resp = requests.post(edit_endpoint, json=payload, timeout=10)
            if not resp.ok:
                log.warning("edit failed %d: %s | retrying as plain text",
                            resp.status_code, resp.text[:200])
                payload.pop("parse_mode", None)
                resp = requests.post(edit_endpoint, json=payload, timeout=10)
                if not resp.ok:
                    log.error("edit plain retry also failed %d: %s",
                              resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("edit send exception: %s", e)

    def split_for_tg(text: str, limit: int = 3500) -> list[str]:
        """Split a long message on ═══ section dividers to stay under TG 4096 limit.

        Limit conservatively set to 3500 (vs hard TG 4096) because:
        - Markdown entity bytes inflate vs Python len()
        - TG counts emoji as multi-byte sometimes
        - Leaves ~15% margin to avoid silent 400 'message too long'

        First chunk is suitable for editMessageText; remaining are sent as
        threaded replies. Prevents the previous truncation bug that dropped
        the BUY header (top of message) when tailing the last N chars.
        """
        if len(text) <= limit:
            return [text]
        # Split on section dividers (keep delimiter on subsequent chunks)
        parts = text.split("\n═══")
        sections = [parts[0]] + ["═══" + p for p in parts[1:]]
        out: list[str] = []
        cur = ""
        for s in sections:
            if len(cur) + len(s) + 1 <= limit:
                cur = (cur + "\n" + s) if cur else s
            else:
                if cur:
                    out.append(cur)
                if len(s) > limit:
                    # Hard fallback: truncate within section
                    out.append(s[:limit - 30] + "\n\n(...本段截断...)")
                    cur = ""
                else:
                    cur = s
        if cur:
            out.append(cur)
        return out

    def send_text(cmd, text: str) -> None:
        """Reply with text, splitting into multiple TG messages if too long.

        All chunks thread under the original /command via reply_to_message_id.
        Use this for any handler whose output might exceed 4096 chars
        (/watchlist with many entries, /status with many positions, etc).
        """
        for c in split_for_tg(text):
            reply(cmd, c)

    def send_long(cmd, running_id: int, text: str, chat: str) -> None:
        """Edit running message with first chunk; send rest as threaded replies."""
        chunks = split_for_tg(text)
        if running_id and chunks:
            edit(chat, running_id, chunks[0])
            for c in chunks[1:]:
                reply(cmd, c)
        else:
            for c in chunks:
                reply(cmd, c)

    channel = TelegramControlChannel(token=token)
    log.info(
        "ControlBot listening for /freeze /unfreeze /halt /unhalt /status /scan /rescan /preview "
        "/watchlist /add /remove"
    )

    from concurrent.futures import ThreadPoolExecutor

    # Bounded threadpool prevents OS-thread exhaustion if user spams commands.
    # 8 workers is generous for typical use (1-3 concurrent TG sessions).
    # Submissions over the queue limit will block briefly, never silently drop.
    _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cmd")

    def _dispatch(cmd):
        command = cmd.command
        chat = cmd.chat_id or cmd.actor
        if command == "freeze":
            sys_state.set("frozen", "user-requested via Telegram", cmd.actor)
            reply(cmd, i18n.render("control_freeze_reply"))
        elif command == "unfreeze":
            sys_state.set("normal", "user-requested via Telegram", cmd.actor)
            reply(cmd, i18n.render("control_unfreeze_reply"))
        elif command == "halt":
            sys_state.set("halted", "user-requested via Telegram", cmd.actor)
            reply(cmd, i18n.render("control_halt_reply"))
        elif command == "unhalt":
            sys_state.set("normal", "user-requested via Telegram", cmd.actor)
            reply(cmd, i18n.render("control_unhalt_reply"))
        elif command == "scan":
            if not cmd.args:
                reply(cmd, i18n.render("control_scan_usage_reply"))
            else:
                tickers = list(cmd.args)
                ticker_count = len(tickers)
                # Send 'running' message; capture its id so we can EDIT it
                # with the final brief instead of sending a 2nd message.
                running_id = reply(cmd, i18n.render(
                    "control_scan_running", ticker_count=ticker_count,
                ))

                # Run scan synchronously via subprocess with 120s timeout
                cmd_args = ["quanterback", "scan", "--format", "brief", "--tickers", ",".join(tickers)]
                try:
                    result = subprocess.run(
                        cmd_args, capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        stderr_tail = result.stderr[-500:] if result.stderr else "unknown error"
                        final = i18n.render("control_scan_error", stderr=stderr_tail)
                        log.error("scan subprocess failed: %s", result.stderr)
                    else:
                        summary = result.stdout if result.stdout else ""
                        final = i18n.render("control_scan_done", summary=summary)
                        log.info("scan subprocess succeeded for %d tickers", ticker_count)
                except subprocess.TimeoutExpired:
                    final = i18n.render("control_scan_timeout")
                    log.error("scan subprocess exceeded 120s timeout for tickers=%s", tickers)
                except Exception as e:
                    final = i18n.render("control_scan_error", stderr=str(e)[-500:])
                    log.error("scan subprocess exception: %s", e)
                send_long(cmd, running_id, final, chat)
        elif command == "rescan":
            running_id = reply(cmd, i18n.render("control_rescan_running"))

            # Run rescan synchronously via subprocess with 600s timeout (10 min)
            try:
                result = subprocess.run(
                    ["quanterback", "rescan", "--format", "brief"],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode != 0:
                    stderr_tail = result.stderr[-500:] if result.stderr else "unknown error"
                    final = i18n.render("control_rescan_error", stderr=stderr_tail)
                    log.error("rescan subprocess failed: %s", result.stderr)
                else:
                    summary = result.stdout if result.stdout else ""
                    final = i18n.render("control_rescan_done", summary=summary)
                    log.info("rescan subprocess succeeded")
            except subprocess.TimeoutExpired:
                final = i18n.render("control_rescan_timeout")
                log.error("rescan subprocess exceeded 600s timeout")
            except Exception as e:
                final = i18n.render("control_rescan_error", stderr=str(e)[-500:])
                log.error("rescan subprocess exception: %s", e)
            send_long(cmd, running_id, final, chat)
        elif command == "preview":
            if cmd.args:
                tickers = list(cmd.args)
                ticker_count = len(tickers)
                running_id = reply(cmd, i18n.render(
                    "control_scan_running", ticker_count=ticker_count,
                ))
                # Run scan with --dry-run flag via subprocess with 120s timeout
                cmd_args = ["quanterback", "scan", "--format", "brief", "--dry-run",
                            "--tickers", ",".join(tickers)]
                try:
                    result = subprocess.run(
                        cmd_args, capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        stderr_tail = result.stderr[-500:] if result.stderr else "unknown error"
                        final = i18n.render("control_scan_error", stderr=stderr_tail)
                        log.error("preview subprocess failed: %s", result.stderr)
                    else:
                        summary = result.stdout if result.stdout else ""
                        final = i18n.render("control_scan_done", summary=summary)
                        log.info("preview subprocess succeeded for %d tickers", ticker_count)
                except subprocess.TimeoutExpired:
                    final = i18n.render("control_scan_timeout")
                    log.error("preview subprocess exceeded 120s timeout for tickers=%s", tickers)
                except Exception as e:
                    final = i18n.render("control_scan_error", stderr=str(e)[-500:])
                    log.error("preview subprocess exception: %s", e)
                send_long(cmd, running_id, final, chat)
            else:
                running_id = reply(cmd, i18n.render("control_rescan_running"))
                # Run rescan with --dry-run flag via subprocess with 600s timeout
                try:
                    result = subprocess.run(
                        ["quanterback", "rescan", "--format", "brief", "--dry-run"],
                        capture_output=True, text=True, timeout=600,
                    )
                    if result.returncode != 0:
                        stderr_tail = result.stderr[-500:] if result.stderr else "unknown error"
                        final = i18n.render("control_rescan_error", stderr=stderr_tail)
                        log.error("preview (full) subprocess failed: %s", result.stderr)
                    else:
                        summary = result.stdout if result.stdout else ""
                        final = i18n.render("control_rescan_done", summary=summary)
                        log.info("preview (full) subprocess succeeded")
                except subprocess.TimeoutExpired:
                    final = i18n.render("control_rescan_timeout")
                    log.error("preview (full) subprocess exceeded 600s timeout")
                except Exception as e:
                    final = i18n.render("control_rescan_error", stderr=str(e)[-500:])
                    log.error("preview (full) subprocess exception: %s", e)
                send_long(cmd, running_id, final, chat)
        elif command == "watchlist":
            # /watchlist [list|add|remove] [SYM]
            if not cmd.args or cmd.args[0].lower() == "list":
                entries = store.list_watchlist()
                # Use send_text in case watchlist grows beyond 4096 char limit
                send_text(cmd, i18n.render(
                    "control_watchlist_list", entries=entries
                ))
            elif cmd.args[0].lower() == "add":
                if len(cmd.args) < 2:
                    reply(cmd, i18n.render("control_watchlist_usage"))
                else:
                    ticker = cmd.args[1].upper()
                    ok = store.add_watchlist_ticker(ticker, source="user")
                    if ok:
                        reply(cmd, i18n.render(
                            "control_watchlist_added", ticker=ticker
                        ))
                    else:
                        reply(cmd, i18n.render(
                            "control_watchlist_already", ticker=ticker
                        ))
            elif cmd.args[0].lower() == "remove":
                if len(cmd.args) < 2:
                    reply(cmd, i18n.render("control_watchlist_usage"))
                else:
                    ticker = cmd.args[1].upper()
                    ok = store.remove_watchlist_ticker(ticker)
                    if ok:
                        reply(cmd, i18n.render(
                            "control_watchlist_removed", ticker=ticker
                        ))
                    else:
                        reply(cmd, i18n.render(
                            "control_watchlist_not_found", ticker=ticker
                        ))
            else:
                reply(cmd, i18n.render("control_watchlist_usage"))
        elif command == "add":
            # /add SYM
            if not cmd.args:
                reply(cmd, i18n.render("control_watchlist_usage"))
            else:
                ticker = cmd.args[0].upper()
                ok = store.add_watchlist_ticker(ticker, source="user")
                if ok:
                    reply(cmd, i18n.render(
                        "control_watchlist_added", ticker=ticker
                    ))
                else:
                    reply(cmd, i18n.render(
                        "control_watchlist_already", ticker=ticker
                    ))
        elif command == "remove":
            # /remove SYM
            if not cmd.args:
                reply(cmd, i18n.render("control_watchlist_usage"))
            else:
                ticker = cmd.args[0].upper()
                ok = store.remove_watchlist_ticker(ticker)
                if ok:
                    reply(cmd, i18n.render(
                        "control_watchlist_removed", ticker=ticker
                    ))
                else:
                    reply(cmd, i18n.render(
                        "control_watchlist_not_found", ticker=ticker
                    ))
        elif command == "status":
            state = sys_state.get_current()
            pending = len(store.query_pending_user_triggers())
            open_lc = len(store.query_open_lifecycles())
            mode_emoji = {"normal": "🟢", "frozen": "❄️", "halted": "🛑"}.get(
                state.mode, "❔",
            )
            reply(cmd, i18n.render(
                "control_status_reply",
                mode=state.mode, mode_emoji=mode_emoji,
                pending=pending, open_positions=open_lc,
                last_change=i18n.format_dt(state.updated_at, "%Y-%m-%d %H:%M %Z"),
            ))

    # Spawn each command in its own daemon thread so slow handlers
    # (/scan up to 120s, /rescan up to 600s) don't block the poll loop.
    # SqliteStore uses check_same_thread=False, subprocess releases GIL,
    # Jinja/I18n are read-only — safe to share across threads.
    for cmd in channel.listen():
        try:
            _executor.submit(_dispatch, cmd)
        except RuntimeError as e:
            log.exception("Executor rejected command %s: %s", cmd.command, e)
        except Exception as e:
            log.exception("Failed to dispatch command %s: %s", cmd.command, e)
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    sys_state = SqliteSystemStateService(store)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    print(generate_report(store, sys_state, i18n))
    return 0


def cmd_positions(_args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from quanterback.report import generate_positions_report
    print(generate_positions_report(store, i18n=i18n))
    return 0


def cmd_trades(_args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from quanterback.report import generate_trades_report
    print(generate_trades_report(store, i18n=i18n))
    return 0


def cmd_analyze(_args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from quanterback.analyze import generate_analyze_report
    print(generate_analyze_report(store, i18n))
    return 0


def cmd_perf(args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from quanterback.perf import generate_perf_report
    print(generate_perf_report(store, i18n, days=args.days, ticker=args.ticker))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from datetime import date as _date

    from quanterback.replay import (
        ReplayConfig,
        ReplayEngine,
        generate_replay_report,
        print_verbose_decisions,
    )

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    cfg = ReplayConfig(
        start=_date.fromisoformat(args.start),
        end=_date.fromisoformat(args.end),
        tickers=tickers,
        max_llm_calls=args.max_llm_calls,
    )
    engine = ReplayEngine(
        data_provider=pipeline.macro_data_provider or pipeline.data_provider,  # type: ignore
        summarizer=pipeline.summarizer,
        strategist=pipeline.strategist,
        backtester=pipeline.backtester,
    )
    result = engine.run(cfg)
    print(generate_replay_report(result, i18n))
    if args.verbose:
        print_verbose_decisions(result)
    return 0


def cmd_track_positions(_args: argparse.Namespace) -> int:
    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)

    from quanterback.adapters.execution.alpaca_broker import AlpacaPaperBroker
    from quanterback.adapters.lifecycle.position_tracker import PositionTracker

    broker = AlpacaPaperBroker(
        api_key=config.alpaca_key, secret=config.alpaca_secret,
    )
    notifier = TelegramNotifier(
        token=config.tg_token, chat_ids=config.tg_chat_ids, store=store,
    )

    tracker = PositionTracker(
        broker=broker,
        store=store,
        notifier=notifier,
        i18n=i18n,
        lookback_hours=config.position_tracker_lookback_hours,
    )
    result = tracker.tick()
    print(
        f"opens={result['opens']} closes={result['closes']} "
        f"open_positions={result['open_positions']}"
    )
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Reconcile local DB vs Alpaca state. Detects and fixes drift."""
    _setup_logging()
    config = _load_config()
    store = SqliteStore(config.db_path)

    from quanterback.adapters.execution.alpaca_broker import AlpacaPaperBroker
    from quanterback.adapters.lifecycle.reconciler import Reconciler

    broker = AlpacaPaperBroker(
        api_key=config.alpaca_key, secret=config.alpaca_secret,
    )
    reconciler = Reconciler(broker=broker, store=store)

    print("Reconciling local DB with Alpaca state...")
    report = reconciler.reconcile()

    print("\nReconciliation Report:")
    print(f"  Orphan orders cancelled: {report.orphan_orders_cancelled}")
    print(f"  Manual closes detected: {report.manual_closes_detected}")
    print(f"  Unfilled orders detected: {report.local_unfilled_orders_detected}")

    if not args.yes:
        # Already done destructive actions; just report
        if any([report.orphan_orders_cancelled, report.manual_closes_detected,
                report.local_unfilled_orders_detected]):
            print("\nActions were taken. Run 'quanterback report' to see updated state.")
        else:
            print("\nNo drift detected. All good!")

    return 0


def cmd_stress(args: argparse.Namespace) -> int:
    from datetime import date as _date

    from quanterback.replay import ReplayEngine
    from quanterback.stress import (
        DEFAULT_WINDOWS,
        StressWindow,
        generate_stress_report,
        run_stress,
        summarize_stress,
    )

    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    windows = DEFAULT_WINDOWS
    if args.windows:
        windows = []
        for part in args.windows.split(","):
            name, start, end = part.split(":")
            windows.append(StressWindow(
                name=name.strip(),
                start=_date.fromisoformat(start.strip()),
                end=_date.fromisoformat(end.strip()),
            ))

    engine = ReplayEngine(
        data_provider=pipeline.macro_data_provider or pipeline.data_provider,  # type: ignore
        summarizer=pipeline.summarizer,
        strategist=pipeline.strategist,
        backtester=pipeline.backtester,
    )
    rows = run_stress(
        engine=engine, windows=windows, tickers=tickers,
        max_llm_calls_per_window=args.max_llm_calls_per_window,
    )
    summary = summarize_stress(rows)
    print(generate_stress_report(summary, i18n))
    return 0


def cmd_calibration(args: argparse.Namespace) -> int:
    config = _load_config()
    store = SqliteStore(config.db_path)
    i18n = I18n(language=config.language, templates_dir=config.templates_dir, display_timezone=config.display_timezone)
    from quanterback.calibration import generate_calibration_report
    print(generate_calibration_report(
        store, i18n,
        days=args.days,
        bucket_width=args.bucket_width,
        source=args.source,
    ))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanterback")
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="Run a single end-to-end watchlist scan")
    scan_parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated tickers to scan; if omitted, scans entire watchlist"
    )
    scan_parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without submitting orders to broker"
    )
    scan_parser.add_argument(
        "--format", choices=["summary", "brief"], default="summary",
        help="Output format: summary (terse) or brief (rich per-ticker)"
    )
    rescan_parser = sub.add_parser("rescan", help="Full watchlist re-scan (every ticker, synchronous)")
    rescan_parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without submitting orders to broker"
    )
    rescan_parser.add_argument(
        "--format", choices=["summary", "brief"], default="brief",
        help="Output format (default 'brief' for manual full sweep)"
    )
    rescan_parser.add_argument(
        "--limit", type=int, default=50,
        help="Cap on tickers scanned (cost guard). Default 50."
    )
    sub.add_parser("control-bot", help="Run the Telegram control daemon")
    sub.add_parser("report", help="Print a summary of recent scan activity, decisions, and state")
    sub.add_parser("positions", help="Print current open positions")
    sub.add_parser("trades", help="Print recent submitted orders")
    sub.add_parser("analyze", help="Inspect decision patterns over time")
    perf_parser = sub.add_parser(
        "perf", help="Performance attribution over closed trades"
    )
    perf_parser.add_argument(
        "--days", type=int, default=None, help="Limit to last N days"
    )
    perf_parser.add_argument(
        "--ticker", type=str, default=None, help="Limit to one ticker"
    )
    replay_parser = sub.add_parser(
        "replay", help="Historical replay of LLM-driven decisions"
    )
    replay_parser.add_argument(
        "--start", type=str, required=True, help="YYYY-MM-DD inclusive"
    )
    replay_parser.add_argument(
        "--end", type=str, required=True, help="YYYY-MM-DD inclusive"
    )
    replay_parser.add_argument(
        "--tickers",
        type=str,
        required=True,
        help="Comma-separated, e.g. AAPL,NVDA,TSLA",
    )
    replay_parser.add_argument(
        "--max-llm-calls", type=int, default=30, help="Cost cap (default 30)"
    )
    replay_parser.add_argument(
        "--verbose", action="store_true", help="Print per-decision details"
    )
    stress_parser = sub.add_parser(
        "stress", help="Multi-window replay stress test across predefined regimes"
    )
    stress_parser.add_argument(
        "--max-llm-calls-per-window", type=int, default=50,
        help="Cost cap per window (default 50)"
    )
    stress_parser.add_argument(
        "--tickers", type=str, default="NVDA,AAPL,TSLA,AMD,MSFT,GOOGL",
        help="Comma-separated tickers (default NVDA,AAPL,TSLA,AMD,MSFT,GOOGL)"
    )
    stress_parser.add_argument(
        "--windows", type=str, default=None,
        help="Override with comma-separated name:start:end (start/end YYYY-MM-DD)"
    )
    sub.add_parser("track-positions", help="Run one position lifecycle tracking tick")
    reconcile_parser = sub.add_parser("reconcile", help="Reconcile local DB vs Alpaca state")
    reconcile_parser.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation (already takes destructive actions; flag is for future use)"
    )
    cal_parser = sub.add_parser("calibration", help="LLM confidence vs realized outcomes")
    cal_parser.add_argument("--source", choices=["live", "replay"], default="live")
    cal_parser.add_argument("--days", type=int, default=None,
                            help="Limit to last N days")
    cal_parser.add_argument("--bucket-width", type=float, default=0.1,
                            help="Confidence bucket width (default 0.1)")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "rescan":
        return cmd_rescan(args)
    if args.command == "control-bot":
        return cmd_control_bot(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "positions":
        return cmd_positions(args)
    if args.command == "trades":
        return cmd_trades(args)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "perf":
        return cmd_perf(args)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "stress":
        return cmd_stress(args)
    if args.command == "track-positions":
        return cmd_track_positions(args)
    if args.command == "reconcile":
        return cmd_reconcile(args)
    if args.command == "calibration":
        return cmd_calibration(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
