from __future__ import annotations

from types import SimpleNamespace

import pytest

from quanterback.adapters.execution.alpaca_broker import AlpacaPaperBroker
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
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=False)
    assert r.submitted
    assert r.order_id == "alpaca-1"


def test_dry_run_does_not_call_broker(fake_trading_client: dict) -> None:
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=True)
    assert not r.submitted
    assert r.order_id is None
    assert fake_trading_client["orders"] == []


def test_account_value_returned_as_float(fake_trading_client: dict) -> None:
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    assert ex.get_account_value() == 125_000.50


def test_get_day_trade_count_returns_int(fake_trading_client: dict) -> None:
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    assert ex.get_day_trade_count() == 2


def test_trailing_stop_ignored_with_bracket_order(
    fake_trading_client: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    """Trailing stop is NOT submitted when bracket has static stop_loss.

    Instead, a warning is logged recommending position_management agent for dynamic SL.
    """
    ex = AlpacaPaperBroker(api_key="k", secret="s")
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
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    spec = BracketOrderSpec(
        ticker="AMD", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=95.0, take_profit_price=110.0,
        trail_percent=None,
    )
    r = ex.submit(spec, dry_run=False)
    assert r.submitted
    assert len(fake_trading_client["orders"]) == 1


# ---------------------- trim_position tests ----------------------


@pytest.fixture()
def fake_trading_client_with_orders(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Trading client fake that supports get_orders, cancel_order_by_id, submit_order."""
    state: dict = {
        "open_orders": [],
        "cancelled": [],
        "submitted_orders": [],
    }

    class FakeClient:
        def __init__(self, api_key: str, secret_key: str, paper: bool) -> None:
            self.api_key = api_key

        def get_orders(self, filter=None):
            return list(state["open_orders"])

        def cancel_order_by_id(self, order_id: str):
            state["cancelled"].append(order_id)
            state["open_orders"] = [
                o for o in state["open_orders"] if str(o.id) != order_id
            ]

        def submit_order(self, order_data):
            state["submitted_orders"].append(order_data)
            return SimpleNamespace(id="trim-1", status="accepted")

        def get_account(self):
            return SimpleNamespace(equity="100000", daytrade_count=0)

    monkeypatch.setattr(
        "quanterback.adapters.execution.alpaca_broker.TradingClient",
        FakeClient,
    )
    # Avoid the real 1.5s settle delay in tests.
    monkeypatch.setattr(
        "quanterback.adapters.execution.alpaca_broker.time.sleep",
        lambda _: None,
    )
    return state


def _make_sell_order(order_id: str, ticker: str, qty: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id, symbol=ticker, side="OrderSide.SELL", qty=qty,
    )


def test_trim_position_cancels_exit_leg_and_submits_market_sell(
    fake_trading_client_with_orders: dict,
) -> None:
    """Happy path: trim cancels enough exit legs to free shares, then sells."""
    # Open SELL legs: one for the full 10-share bracket
    fake_trading_client_with_orders["open_orders"] = [
        _make_sell_order("leg-tp", "AMD", 10),
    ]
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    ok = ex.trim_position("AMD", qty_to_sell=5)

    assert ok is True
    # The exit leg must have been cancelled.
    assert "leg-tp" in fake_trading_client_with_orders["cancelled"]
    # A market SELL for 5 shares must have been submitted.
    assert len(fake_trading_client_with_orders["submitted_orders"]) == 1
    submitted = fake_trading_client_with_orders["submitted_orders"][0]
    assert submitted.symbol == "AMD"
    assert submitted.qty == 5


def test_trim_position_rejects_non_positive_qty(
    fake_trading_client_with_orders: dict,
) -> None:
    """trim_position(qty=0) is a no-op returning False."""
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    assert ex.trim_position("AMD", qty_to_sell=0) is False
    assert ex.trim_position("AMD", qty_to_sell=-3) is False
    # No orders touched
    assert fake_trading_client_with_orders["cancelled"] == []
    assert fake_trading_client_with_orders["submitted_orders"] == []


def test_trim_position_returns_false_on_broker_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Alpaca client blows up, trim_position returns False (doesn't raise)."""
    class BoomClient:
        def __init__(self, *a, **kw) -> None: ...

        def get_orders(self, filter=None):
            raise RuntimeError("API down")

    monkeypatch.setattr(
        "quanterback.adapters.execution.alpaca_broker.TradingClient",
        BoomClient,
    )
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    assert ex.trim_position("AMD", qty_to_sell=5) is False


def test_trim_position_only_cancels_legs_for_target_ticker(
    fake_trading_client_with_orders: dict,
) -> None:
    """Sell legs for OTHER tickers must NOT be touched."""
    fake_trading_client_with_orders["open_orders"] = [
        _make_sell_order("amd-leg", "AMD", 10),
        _make_sell_order("nvda-leg", "NVDA", 8),
    ]
    ex = AlpacaPaperBroker(api_key="k", secret="s")
    ex.trim_position("AMD", qty_to_sell=5)

    assert "amd-leg" in fake_trading_client_with_orders["cancelled"]
    assert "nvda-leg" not in fake_trading_client_with_orders["cancelled"]
