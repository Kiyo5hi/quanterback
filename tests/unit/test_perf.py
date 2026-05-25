from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import PersistedTrade
from quanterback.i18n import I18n
from quanterback.perf import _risk_adjusted, generate_perf_report


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "perf.sqlite")




def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _trade(
    store: SqliteStore, *,
    ticker: str, pnl_usd: float, pnl_pct: float,
    reason: str = "TAKE_PROFIT", hours_ago: float = 12,
    holding_hours: float = 12,
    order_id: str | None = None,
) -> None:
    exit_at = _now() - timedelta(hours=hours_ago)
    t = PersistedTrade(
        ticker=ticker, qty=10, entry_price=100,
        entry_at=exit_at - timedelta(hours=holding_hours),
        exit_price=100 + pnl_usd / 10, exit_at=exit_at,
        exit_reason=reason, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
        holding_hours=holding_hours,
        exit_order_id=order_id or f"exit-{ticker}-{hours_ago}-{pnl_usd}",
    )
    store.insert_trade(t)


def test_empty_store_renders(store: SqliteStore, i18n_en: I18n) -> None:
    out = generate_perf_report(store, i18n_en)
    assert "n=0" in out or "No trades" in out or "暂无" in out


def test_win_rate_and_total_pnl(store: SqliteStore, i18n_en: I18n) -> None:
    _trade(store, ticker="AAPL", pnl_usd=100, pnl_pct=10, hours_ago=10, order_id="a1")
    _trade(store, ticker="AAPL", pnl_usd=-50, pnl_pct=-5, hours_ago=20, order_id="a2")
    _trade(store, ticker="TSLA", pnl_usd=200, pnl_pct=20, hours_ago=5, order_id="t1")
    out = generate_perf_report(store, i18n_en)
    # 2 wins out of 3 → 66.7%
    assert "66.7" in out or "wins=2" in out


def test_days_filter(store: SqliteStore, i18n_en: I18n) -> None:
    _trade(store, ticker="OLD", pnl_usd=100, pnl_pct=10, hours_ago=24*40, order_id="old1")
    _trade(store, ticker="NEW", pnl_usd=200, pnl_pct=20, hours_ago=24*5, order_id="new1")
    out = generate_perf_report(store, i18n_en, days=7)
    # "OLD" should not appear (>7 days)
    assert "OLD" not in out
    assert "NEW" in out or "n=1" in out


def test_ticker_filter(store: SqliteStore, i18n_en: I18n) -> None:
    _trade(store, ticker="AAPL", pnl_usd=100, pnl_pct=10, hours_ago=10, order_id="aapl1")
    _trade(store, ticker="TSLA", pnl_usd=200, pnl_pct=20, hours_ago=5, order_id="tsla1")
    out = generate_perf_report(store, i18n_en, ticker="AAPL")
    assert "AAPL" in out or "n=1" in out
    assert "TSLA" not in out


def test_max_drawdown_pct_capped_at_100() -> None:
    # Scenario: trade 1 is a small gain (+$100), pushing cumulative to +$100.
    # trade 2 is a massive loss (-$1000). Drawdown from peak of $100 is $1000,
    # which would normally be 1000%, but should be capped at 100%.
    trades = [
        PersistedTrade(
            ticker="T1", qty=10, entry_price=100,
            entry_at=_now() - timedelta(hours=30),
            exit_price=110, exit_at=_now() - timedelta(hours=25),
            exit_reason="TAKE_PROFIT", pnl_usd=100, pnl_pct=10,
            holding_hours=5, exit_order_id="t1",
        ),
        PersistedTrade(
            ticker="T2", qty=10, entry_price=100,
            entry_at=_now() - timedelta(hours=25),
            exit_price=0, exit_at=_now() - timedelta(hours=20),
            exit_reason="STOP_LOSS", pnl_usd=-1000, pnl_pct=-100,
            holding_hours=5, exit_order_id="t2",
        ),
    ]
    result = _risk_adjusted(trades)
    # Peak should be $100 (after trade 1), drawdown is $1000 from that peak
    # which would be 1000%, but should be capped at 100%
    assert result["max_drawdown_pct"] == 100.0
    assert result["dd_pct_is_capped"] is True
    assert result["peak_equity_usd"] == 100.0
    assert result["max_drawdown_usd"] == 1000.0


def test_risk_adjusted_includes_peak_equity() -> None:
    # Test that peak_equity_usd is included in result
    trades = [
        PersistedTrade(
            ticker="T1", qty=10, entry_price=100,
            entry_at=_now() - timedelta(hours=24),
            exit_price=110, exit_at=_now() - timedelta(hours=20),
            exit_reason="TAKE_PROFIT", pnl_usd=100, pnl_pct=10,
            holding_hours=4, exit_order_id="t1",
        ),
        PersistedTrade(
            ticker="T2", qty=10, entry_price=100,
            entry_at=_now() - timedelta(hours=20),
            exit_price=50, exit_at=_now() - timedelta(hours=16),
            exit_reason="STOP_LOSS", pnl_usd=-500, pnl_pct=-50,
            holding_hours=4, exit_order_id="t2",
        ),
    ]
    result = _risk_adjusted(trades)
    assert "peak_equity_usd" in result
    assert "dd_pct_is_capped" in result
    # peak should be $100 (first trade pnl_usd), drawdown of $500 from $100 = 500%
    # Should be capped at 100%
    assert result["max_drawdown_pct"] == 100.0
    assert result["dd_pct_is_capped"] is True
    assert result["peak_equity_usd"] == 100.0
