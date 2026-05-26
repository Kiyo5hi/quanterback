from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from quanterback.domain.backtest import BacktestRequest
from quanterback.domain.events import NotificationEvent, ScanEvent
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.domain.risk import RiskThresholds
from quanterback.interfaces.data import (
    DataProvider,
    FundamentalsProvider,
    HistoricalDataProvider,
    NewsProvider,
    Summarizer,
)
from quanterback.interfaces.decision import ApprovalGate, LLMStrategist
from quanterback.interfaces.events import EventSource
from quanterback.interfaces.execution import Executor
from quanterback.interfaces.notify import Notifier
from quanterback.interfaces.risk import (
    Backtester,
    OrderBuilder,
    PositionStateService,
    RiskGate,
)
from quanterback.interfaces.state import SystemStateService
from quanterback.interfaces.store import StateStore

log = logging.getLogger(__name__)


@dataclass
class ScanPipeline:
    event_source: EventSource
    data_provider: DataProvider
    summarizer: Summarizer
    strategist: LLMStrategist
    approval_gate: ApprovalGate
    position_state: PositionStateService
    backtester: Backtester
    risk_gate: RiskGate
    order_builder: OrderBuilder
    executor: Executor
    notifier: Notifier
    state_store: StateStore
    system_state: SystemStateService
    thresholds: RiskThresholds
    backtest_lookback_years: int
    max_concurrent_positions: int
    macro_data_provider: HistoricalDataProvider | None = None
    news_provider: NewsProvider | None = None
    fundamentals_provider: FundamentalsProvider | None = None
    watchlist_auto_manager: object | None = None
    config: object | None = None
    _spy_closes: object = field(default=None, init=False, repr=False)
    _market_context: dict = field(default_factory=dict, init=False, repr=False)
    _effective_top_n: int = field(default=10, init=False, repr=False)

    def run_for_tickers(self, tickers: list[str], trigger_label: str = "", force_dry_run: bool = False) -> int | None:
        """Run scan for specific tickers, ignoring watchlist/screener.

        Returns the scan_run id of this invocation (so callers can render
        a brief for THIS specific run, not race against concurrent scans).
        Returns None if the system is halted.

        Args:
            tickers: List of ticker symbols to scan
            trigger_label: Label for this scan run (e.g., "/scan AAPL" or "[DRY] /preview")
            force_dry_run: If True, skip order submission (per-invocation override)
        """
        st = self.system_state.get_current()
        if st.mode == "halted":
            log.info("System halted; exiting without scan.")
            return None
        dry_run = force_dry_run or (st.mode == "frozen")

        run = ScanRun(started_at=datetime.now(tz=timezone.utc), source="user_trigger",
                      trigger_label=trigger_label)
        run_id = self.state_store.insert_scan_run(run)
        run.id = run_id

        # Market hours guard. Skip scan if market is closed (respects holidays, early closes).
        # Dry-run skips this check (useful for weekend testing).
        # Can override with force_scan_when_closed config flag.
        # Now AFTER scan_run insert so we have an audit record.
        if not dry_run:
            force_scan_closed = self.config.force_scan_when_closed if self.config else False
            if not force_scan_closed:
                try:
                    if not self.executor.is_market_open():
                        next_open = self.executor.next_market_open()
                        log.info("Market closed; skipping scan. Next open: %s",
                                 next_open.isoformat() if next_open else "unknown")
                        # Close out the scan_run for audit trail
                        run.tickers_processed = 0
                        run.errors_count = 0
                        run.ended_at = datetime.now(tz=timezone.utc)
                        run.trigger_label = (run.trigger_label or "") + " [market_closed]"
                        self.state_store.update_scan_run(run)
                        # Clear any stale buffer (defensive — user_trigger path)
                        if hasattr(self.notifier, "discard_buffer"):
                            try:
                                self.notifier.discard_buffer()
                            except Exception:
                                pass
                        return run_id
                except Exception as e:
                    log.warning("Market-open check failed: %s — ABORTING scan (fail-safe)", e)
                    run.tickers_processed = 0
                    run.errors_count = 1
                    run.ended_at = datetime.now(tz=timezone.utc)
                    run.trigger_label = (run.trigger_label or "") + " [clock_check_failed]"
                    self.state_store.update_scan_run(run)
                    return run_id

        self._market_context = self._compute_market_context()
        self._spy_closes = self._get_spy_closes()  # cache for this run
        self._effective_top_n = self._compute_effective_top_n()
        self._set_screener_top_n()
        if hasattr(self.strategist, "set_market_context"):
            self.strategist.set_market_context(self._market_context)

        processed = 0
        errors = 0
        seen_tickers: set[str] = set()
        for ticker in tickers:
            ticker = ticker.strip().upper()
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            processed += 1
            try:
                event = ScanEvent(
                    ticker=ticker,
                    source="user_manual_scan",
                    priority=10,
                    requested_at=datetime.now(tz=timezone.utc),
                )
                self._process_event(event, run_id=run_id, dry_run=dry_run)
            except Exception as exc:
                errors += 1
                log.exception("Ticker %s failed in scan", ticker)
                self._notify("error", {"ticker": ticker, "error": str(exc)})
                self.state_store.insert_decision(PersistedDecision(
                    scan_run_id=run_id, ticker=ticker,
                    summary_json="{}", decision_json="{}", llm_model="n/a",
                    rejected_reason=f"exception: {exc}",
                    created_at=datetime.now(tz=timezone.utc),
                ))

        run.tickers_processed = processed
        run.errors_count = errors
        run.ended_at = datetime.now(tz=timezone.utc)
        self.state_store.update_scan_run(run)
        # Skip auto-broadcast for user-triggered scans — control-bot handler
        # sends its own (richer) brief; otherwise user gets duplicate messages.
        # BUT we must still clear the per-event buffer in the notifier;
        # otherwise events accumulate and leak into the next cron scan_summary.
        if run.source != "user_trigger":
            self._notify("scan_summary",
                         {"processed": processed, "errors": errors, "dry_run": dry_run})
        elif hasattr(self.notifier, "discard_buffer"):
            try:
                discarded = self.notifier.discard_buffer()
                if discarded:
                    log.info("Discarded %d buffered events (user_trigger)", discarded)
            except Exception as e:
                log.warning("discard_buffer failed: %s", e)
        return run_id

    def run(self, force_dry_run: bool = False) -> int | None:
        st = self.system_state.get_current()
        if st.mode == "halted":
            log.info("System halted; exiting without scan.")
            return None
        dry_run = force_dry_run or (st.mode == "frozen")

        run = ScanRun(started_at=datetime.now(tz=timezone.utc), source="cron", trigger_label="cron")
        run_id = self.state_store.insert_scan_run(run)
        run.id = run_id

        # Market hours guard. Skip scan if market is closed (respects holidays, early closes).
        # Dry-run skips this check (useful for weekend testing).
        # Can override with force_scan_when_closed config flag.
        # Now AFTER scan_run insert so we have an audit record.
        if not dry_run:
            force_scan_closed = self.config.force_scan_when_closed if self.config else False
            if not force_scan_closed:
                try:
                    if not self.executor.is_market_open():
                        next_open = self.executor.next_market_open()
                        log.info("Market closed; skipping scan. Next open: %s",
                                 next_open.isoformat() if next_open else "unknown")
                        # Close out the scan_run for audit trail
                        run.tickers_processed = 0
                        run.errors_count = 0
                        run.ended_at = datetime.now(tz=timezone.utc)
                        run.trigger_label = (run.trigger_label or "") + " [market_closed]"
                        self.state_store.update_scan_run(run)
                        # Clear any stale buffer (defensive — cron path, but defensive anyway)
                        if hasattr(self.notifier, "discard_buffer"):
                            try:
                                self.notifier.discard_buffer()
                            except Exception:
                                pass
                        return run_id
                except Exception as e:
                    log.warning("Market-open check failed: %s — ABORTING scan (fail-safe)", e)
                    run.tickers_processed = 0
                    run.errors_count = 1
                    run.ended_at = datetime.now(tz=timezone.utc)
                    run.trigger_label = (run.trigger_label or "") + " [clock_check_failed]"
                    self.state_store.update_scan_run(run)
                    return run_id

        self._market_context = self._compute_market_context()
        self._spy_closes = self._get_spy_closes()  # cache for this run
        self._effective_top_n = self._compute_effective_top_n()
        self._set_screener_top_n()
        if hasattr(self.strategist, "set_market_context"):
            self.strategist.set_market_context(self._market_context)

        processed = 0
        errors = 0
        seen_tickers: set[str] = set()
        for event in self.event_source.stream():
            if event.ticker in seen_tickers:
                continue
            seen_tickers.add(event.ticker)
            processed += 1
            try:
                self._process_event(event, run_id=run_id, dry_run=dry_run)
            except Exception as exc:
                errors += 1
                log.exception("Ticker %s failed in scan", event.ticker)
                self._notify("error", {"ticker": event.ticker, "error": str(exc)})
                self.state_store.insert_decision(PersistedDecision(
                    scan_run_id=run_id, ticker=event.ticker,
                    summary_json="{}", decision_json="{}", llm_model="n/a",
                    rejected_reason=f"exception: {exc}",
                    created_at=datetime.now(tz=timezone.utc),
                ))

        run.tickers_processed = processed
        run.errors_count = errors
        run.ended_at = datetime.now(tz=timezone.utc)
        self.state_store.update_scan_run(run)
        # Skip auto-broadcast for user-triggered scans — control-bot handler
        # sends its own (richer) brief; otherwise user gets duplicate messages.
        # BUT we must still clear the per-event buffer in the notifier;
        # otherwise events accumulate and leak into the next cron scan_summary.
        if run.source != "user_trigger":
            self._notify("scan_summary",
                         {"processed": processed, "errors": errors, "dry_run": dry_run})
        elif hasattr(self.notifier, "discard_buffer"):
            try:
                discarded = self.notifier.discard_buffer()
                if discarded:
                    log.info("Discarded %d buffered events (user_trigger)", discarded)
            except Exception as e:
                log.warning("discard_buffer failed: %s", e)

        # Run position management if enabled
        if getattr(self.config, "position_management_enabled", False):
            try:
                self._run_position_management(run_id, dry_run)
            except Exception as e:
                log.exception("Position management failed: %s", e)

        # Auto-manage watchlist membership
        if self.watchlist_auto_manager is not None:
            try:
                counts = self.watchlist_auto_manager.tick()  # type: ignore[attr-defined]
                if counts.get("promoted") or counts.get("demoted"):
                    log.info("Watchlist auto-management: promoted=%d, demoted=%d",
                             counts.get("promoted", 0), counts.get("demoted", 0))
            except Exception as e:
                log.exception("Watchlist auto-management failed: %s", e)
        return run_id

    def _process_event(self, event: ScanEvent, *, run_id: int, dry_run: bool) -> None:
        # Reconcile before checking if we can open new positions (defense in depth)
        # This catches any drift from previous runs and frees up capacity.
        # Failures are logged but don't block the scan (production safety).
        try:
            from quanterback.adapters.lifecycle.reconciler import Reconciler
            reconciler = Reconciler(broker=self.executor, store=self.state_store)
            report = reconciler.reconcile()
            if report.orphan_orders_cancelled or report.manual_closes_detected:
                log.info("Reconciliation freed capacity: orphans=%d, manual_closes=%d",
                         report.orphan_orders_cancelled, report.manual_closes_detected)
        except Exception as e:
            log.warning("Pre-event reconciliation failed (continuing scan): %s", e)

        if len(self.state_store.query_open_lifecycles()) >= self.max_concurrent_positions:
            self._persist_rejection(run_id, event.ticker, "max_concurrent_positions reached")
            return
        if self.position_state.has_open_lifecycle(event.ticker):
            self._persist_rejection(run_id, event.ticker, "ticker has open lifecycle")
            return

        window = self.data_provider.fetch(event.ticker)
        news: list = []
        if self.news_provider is not None:
            try:
                news = self.news_provider.fetch_news(event.ticker)
            except Exception as e:
                log.warning("News fetch failed for %s: %s", event.ticker, e)

        # Fetch fundamentals enrichment if provider exists
        earnings_date = None
        insider_activity = None
        analyst_actions = None
        short_interest = None
        eps_trend = None
        fundamental_ratios: dict = {}
        if self.fundamentals_provider is not None:
            try:
                earnings_date = self.fundamentals_provider.fetch_next_earnings_date(
                    event.ticker
                )
            except Exception as e:
                log.warning("Earnings date fetch failed for %s: %s", event.ticker, e)
            try:
                insider_activity = self.fundamentals_provider.fetch_insider_activity(
                    event.ticker
                )
            except Exception as e:
                log.warning("Insider activity fetch failed for %s: %s", event.ticker, e)
            try:
                analyst_actions = self.fundamentals_provider.fetch_analyst_actions(
                    event.ticker
                )
            except Exception as e:
                log.warning("Analyst actions fetch failed for %s: %s", event.ticker, e)
            try:
                short_interest = self.fundamentals_provider.fetch_short_interest(
                    event.ticker
                )
            except Exception as e:
                log.warning("Short interest fetch failed for %s: %s", event.ticker, e)
            try:
                eps_trend = self.fundamentals_provider.fetch_eps_trend(event.ticker)
            except Exception as e:
                log.warning("EPS trend fetch failed for %s: %s", event.ticker, e)
            # Fetch fundamental ratios if provider has the method
            if hasattr(self.fundamentals_provider, "fetch_fundamentals"):
                try:
                    fundamental_ratios = (
                        self.fundamentals_provider.fetch_fundamentals(event.ticker)
                    )
                except Exception as e:
                    log.warning("Fundamental ratios fetch failed for %s: %s", event.ticker, e)
                    fundamental_ratios = {}

        # Pass SPY closes and news if the summarizer accepts them (duck typing)
        import inspect
        sig = inspect.signature(self.summarizer.summarize)
        kwargs = {}
        if "spy_closes" in sig.parameters:
            kwargs["spy_closes"] = getattr(self, "_spy_closes", None)
        if "news" in sig.parameters:
            kwargs["news"] = news
        if "earnings_date" in sig.parameters:
            kwargs["earnings_date"] = earnings_date
        if "insider_activity" in sig.parameters:
            kwargs["insider_activity"] = insider_activity
        if "analyst_actions" in sig.parameters:
            kwargs["analyst_actions"] = analyst_actions
        if "short_interest" in sig.parameters:
            kwargs["short_interest"] = short_interest
        if "eps_trend" in sig.parameters:
            kwargs["eps_trend"] = eps_trend
        if "fundamental_ratios" in sig.parameters:
            kwargs["fundamental_ratios"] = fundamental_ratios
        summary = self.summarizer.summarize(window, **kwargs)

        # Enrich strategist with current positions if it supports them
        if hasattr(self.strategist, "set_current_positions"):
            try:
                open_pos = self.state_store.query_open_lifecycles()
                positions_context = []
                for lifecycle in open_pos:
                    # Fetch position details (would come from position tracker in prod)
                    positions_context.append({
                        "ticker": lifecycle.ticker,
                        "order_id": lifecycle.order_id,
                        "days_held": 0.0,  # populated by position tracker in real scenario
                    })
                self.strategist.set_current_positions(positions_context if positions_context else None)
            except Exception as e:
                log.warning("Failed to enrich current_positions: %s", e)

        decision = self.strategist.decide(summary)

        # Serialize agent_debate if present (from multi-agent strategist)
        agent_debate_json = None
        if decision.agent_debate is not None:
            agent_debate_json = decision.agent_debate.model_dump_json()

        dec_id = self.state_store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker=event.ticker,
            summary_json=summary.model_dump_json(),
            decision_json=decision.model_dump_json(),
            llm_model=getattr(self.strategist, "model_name", "unknown"),
            agent_debate_json=agent_debate_json,
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("decision", {
            "ticker": event.ticker, "action": decision.action,
            "rationale": decision.rationale,
        })

        if decision.action != "BUY":
            return

        approval = self.approval_gate.review(decision)
        if not approval.approved:
            self._update_rejection(dec_id, f"approval_gate: {approval.reason}")
            return

        assert decision.params is not None
        bt = self.backtester.run(_make_backtest_request(decision, self.backtest_lookback_years))
        assessment = self.risk_gate.evaluate(bt, self.thresholds)
        bt_id = self.state_store.insert_backtest(PersistedBacktest(
            decision_id=dec_id, report_json=bt.model_dump_json(),
            passed=assessment.passed,
            failed_checks=",".join(assessment.failed_checks) or None,
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("backtest", {
            "ticker": event.ticker, "passed": assessment.passed,
            "failed_checks": assessment.failed_checks,
        })
        if not assessment.passed:
            self._update_rejection(dec_id, f"risk_gate: {assessment.failed_checks}")
            return

        account_value = self.executor.get_account_value()
        spec = self.order_builder.build(decision, summary, account_value,
                                         size_multiplier=assessment.size_multiplier)
        result = self.executor.submit(spec, dry_run=dry_run, decision_id=dec_id)
        order_id = self.state_store.insert_order(PersistedOrder(
            decision_id=dec_id, backtest_id=bt_id,
            bracket_spec_json=spec.model_dump_json(),
            alpaca_order_id=result.order_id,
            submitted_at=datetime.now(tz=timezone.utc),
            dry_run=dry_run,
            raw_response_json=json.dumps(result.raw_response),
        ))
        self._notify("order", {
            "ticker": event.ticker, "submitted": result.submitted,
            "dry_run": dry_run, "order_id": result.order_id,
        })
        if result.submitted and result.order_id:
            self.state_store.upsert_position(
                PersistedPosition(
                    ticker=event.ticker, order_id=order_id, state="pending",
                    entry_price=None, sl=spec.stop_loss_price, tp=spec.take_profit_price,
                    qty=spec.qty, opened_at=datetime.now(tz=timezone.utc),
                    decision_id=dec_id,
                ),
                broker_cancel_stale=self.executor.cancel_order,
            )

    def _persist_rejection(self, run_id: int, ticker: str, reason: str) -> None:
        self.state_store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker=ticker, summary_json="{}",
            decision_json="{}", llm_model="n/a",
            rejected_reason=reason,
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("decision",
                     {"ticker": ticker, "action": "REJECTED", "reason": reason})

    def _update_rejection(self, decision_id: int, reason: str) -> None:
        self.state_store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE decisions SET rejected_reason=? WHERE id=?",
            (reason, decision_id),
        )

    def _benchmark_ticker(self) -> str:
        return getattr(self.config, "benchmark_ticker", "VOO") if self.config else "VOO"

    def _get_spy_closes(self) -> pd.Series | None:
        if self.macro_data_provider is None:
            return None
        try:
            return self.macro_data_provider.fetch_historical(self._benchmark_ticker(), 1)["close"]
        except Exception:
            return None

    def _compute_market_context(self) -> dict[str, str]:
        if self.macro_data_provider is None:
            return {}
        try:
            df = self.macro_data_provider.fetch_historical(self._benchmark_ticker(), 1)
            closes = df["close"]
            sma20 = float(closes.rolling(20).mean().iloc[-1])
            sma50 = float(closes.rolling(50).mean().iloc[-1])
            sma200 = float(closes.rolling(200).mean().iloc[-1])
            last = float(closes.iloc[-1])
            if sma20 > sma50 > sma200 and last > sma50:
                trend = "uptrend"
            elif sma20 < sma50 < sma200 and last < sma50:
                trend = "downtrend"
            else:
                trend = "sideways"
            pct_from_high = float(last / closes.tail(252).max() - 1)
            return {
                "spy_trend": trend,
                "spy_pct_from_52w_high": f"{pct_from_high:+.1%}",
                "spy_last_close": f"{last:.2f}",
                "benchmark_ticker": self._benchmark_ticker(),
            }
        except Exception as e:
            log.warning("Macro context fetch failed: %s", e)
            return {}

    def _compute_effective_top_n(self) -> int:
        """Derive effective top_n based on market regime if dynamic mode is enabled."""
        if self.config is None:
            return 10
        dynamic = getattr(self.config, "universe_dynamic_top_n", False)
        if not dynamic:
            return getattr(self.config, "universe_top_n", 10)

        trend = self._market_context.get("spy_trend", "sideways")
        if trend == "uptrend":
            return getattr(self.config, "universe_top_n_bullish", 15)
        elif trend == "downtrend":
            return getattr(self.config, "universe_top_n_bearish", 5)
        else:
            return getattr(self.config, "universe_top_n_neutral", 10)

    def _set_screener_top_n(self) -> None:
        """Set the computed top_n on the universe screener if it exists."""
        if hasattr(self.event_source, "screener") and self.event_source.screener is not None:
            if hasattr(self.event_source.screener, "set_top_n"):
                self.event_source.screener.set_top_n(self._effective_top_n)
                log.info("Universe screener top_n set to %d (regime: %s)",
                         self._effective_top_n, self._market_context.get("spy_trend", "unknown"))

    def _run_position_management(self, run_id: int, dry_run: bool) -> None:
        """Evaluate held positions and decide on HOLD/TIGHTEN_SL/EXIT actions.

        This runs after the entry scan to re-evaluate existing positions.
        """
        try:
            from quanterback.adapters.decision.position_management_agent import (
                PositionManagementAgent,
            )
        except ImportError:
            log.warning("Position management agent not available; skipping.")
            return

        open_lifecycles = self.state_store.query_open_lifecycles()
        if not open_lifecycles:
            log.info("No open positions for management.")
            return

        min_age_hours = getattr(self.config, "position_management_min_age_hours", 6.0)
        reeval_interval_hours = getattr(self.config, "position_management_reeval_interval_hours", 4.0)
        now = datetime.now(tz=timezone.utc)

        # Initialize agent
        agent = PositionManagementAgent(
            llm_client=self.strategist,  # Reuse strategist's LLM client
            prompts_dir=getattr(self.config, "prompts_dir", Path("/config/prompts")),
            language=getattr(self.config, "language", "en"),
        )

        for lifecycle in open_lifecycles:
            ticker = lifecycle.ticker
            try:
                # Check min age
                age_hours = (now - lifecycle.opened_at).total_seconds() / 3600
                if age_hours < min_age_hours:
                    log.debug("[%s] Position too young (%.1f h < %.1f h); skipping.",
                             ticker, age_hours, min_age_hours)
                    continue

                # TODO: check reeval interval (would need to store last_eval time)
                # For now, evaluate all eligible positions

                # Fetch fresh data and summary
                window = self.data_provider.fetch(ticker)
                news = []
                if self.news_provider is not None:
                    try:
                        news = self.news_provider.fetch_news(ticker)
                    except Exception as e:
                        log.warning("News fetch failed for %s during position mgmt: %s", ticker, e)

                # Build summary (reuse same logic as entry scan)
                import inspect
                sig = inspect.signature(self.summarizer.summarize)
                kwargs = {}
                if "spy_closes" in sig.parameters:
                    kwargs["spy_closes"] = getattr(self, "_spy_closes", None)
                if "news" in sig.parameters:
                    kwargs["news"] = news
                summary = self.summarizer.summarize(window, **kwargs)

                # Build position context (would come from position tracker in prod)
                # For now, minimal context
                position_context = {
                    "order_id": lifecycle.order_id,
                    "days_held": age_hours / 24,
                    "entry_price": None,  # Would fetch from PersistedPosition
                    "current_price": float(summary.price.last_close) if summary.price.last_close else None,
                    "unrealized_pnl_pct": 0.0,  # Would calculate from position tracker
                    "current_sl": None,
                    "current_tp": None,
                }

                # Run agent
                decision = agent.evaluate(summary, position_context)
                log.info(
                    "[%s/position_mgmt] action=%s reasoning=%s conf=%.2f",
                    ticker, decision.action, decision.reasoning, decision.confidence,
                )

                # Apply decision
                if decision.action == "HOLD":
                    pass  # No action needed
                elif decision.action == "TIGHTEN_SL":
                    if decision.new_sl_price and not dry_run:
                        success = self.executor.replace_stop_loss(ticker, decision.new_sl_price)
                        if success:
                            log.info("[%s] Tightened stop loss to %.2f", ticker, decision.new_sl_price)
                        else:
                            log.warning("[%s] Failed to tighten stop loss", ticker)
                elif decision.action == "EXIT_NOW":
                    if not dry_run:
                        success = self.executor.market_close(ticker)
                        if success:
                            log.info("[%s] Market closed (position management)", ticker)
                        else:
                            log.warning("[%s] Failed to market close", ticker)

                self._notify("position_management", {
                    "ticker": ticker,
                    "action": decision.action,
                    "reasoning": decision.reasoning,
                    "confidence": decision.confidence,
                })

            except Exception as e:
                log.exception("Position management failed for %s: %s", ticker, e)

    def _notify(
        self,
        kind: Literal["decision", "backtest", "order", "scan_summary", "error", "position_management"],
        payload: dict,
    ) -> None:
        self.notifier.push(
            NotificationEvent(
                kind=kind, payload=payload,
                timestamp=datetime.now(tz=timezone.utc),
            )
        )


def _make_backtest_request(decision: object, lookback_years: int) -> BacktestRequest:
    assert decision.params is not None  # type: ignore[attr-defined]
    return BacktestRequest(
        ticker=decision.ticker,  # type: ignore[attr-defined]
        strategy=decision.strategy,  # type: ignore[attr-defined]
        params=decision.params.model_dump(),  # type: ignore[attr-defined]
        lookback_years=lookback_years,
    )
