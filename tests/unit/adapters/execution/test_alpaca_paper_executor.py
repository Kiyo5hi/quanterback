from __future__ import annotations

from types import SimpleNamespace

import pytest

from quanterback.adapters.execution.alpaca_paper_executor import AlpacaPaperExecutor
from quanterback.domain.order import BracketOrderSpec


@pytest.fixture()
def fake_trading_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"orders": [], "account_calls": 0}

    class FakeClient:
        def __init__(self, api_key: str, secret_key: str, paper: bool) -> None:
            self.api_key = api_key

        def submit_order(self, order_data):
            calls["orders"].append(order_data)
            return SimpleNamespace(id="alpaca-1", status="accepted")

        def get_account(self):
            calls["account_calls"] += 1
            return SimpleNamespace(equity="125000.50", daytrade_count=2)

    monkeypatch.setattr(
        "quanterback.adapters.execution.alpaca_broker.TradingClient",
        FakeClient,
    )
    return calls


def _spec() -> BracketOrderSpec:
    return BracketOrderSpec(
        ticker="AAPL", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=95.0, take_profit_price=110.0,
    )


def test_submit_returns_alpaca_order_id(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=False)
    assert r.submitted
    assert r.order_id == "alpaca-1"


def test_dry_run_does_not_call_broker(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=True)
    assert not r.submitted
    assert r.order_id is None
    assert fake_trading_client["orders"] == []


def test_account_value_returned_as_float(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    assert ex.get_account_value() == 125_000.50


def test_get_day_trade_count_returns_int(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    assert ex.get_day_trade_count() == 2


def test_trailing_stop_ignored_with_bracket_order(
    fake_trading_client: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    """Trailing stop is NOT submitted when bracket has static stop_loss.

    Instead, a warning is logged recommending position_management agent for dynamic SL.
    """
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    spec = BracketOrderSpec(
        ticker="AMD", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=95.0, take_profit_price=110.0,
        trail_percent=8.0,
    )
    r = ex.submit(spec, dry_run=False)
    assert r.submitted
    # Only bracket order submitted; no trailing stop
    assert len(fake_trading_client["orders"]) == 1
    # Warning logged about trailing stop being ignored
    assert "trail_percent" in caplog.text
    assert "position_management" in caplog.text


def test_bracket_order_when_trail_percent_is_none(
    fake_trading_client: dict,
) -> None:
    """Bracket order submitted normally when trail_percent is not set."""
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    spec = BracketOrderSpec(
        ticker="AMD", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=95.0, take_profit_price=110.0,
        trail_percent=None,
    )
    r = ex.submit(spec, dry_run=False)
    assert r.submitted
    assert len(fake_trading_client["orders"]) == 1
