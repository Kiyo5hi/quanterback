from __future__ import annotations

import pytest
from pydantic import ValidationError

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


def test_bracket_order_spec_minimal() -> None:
    s = BracketOrderSpec(
        ticker="AAPL", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=180.0, take_profit_price=200.0,
    )
    assert s.qty == 10


def test_limit_price_required_when_entry_type_limit() -> None:
    with pytest.raises(ValidationError):
        BracketOrderSpec(
            ticker="AAPL", side="buy", qty=10, entry_type="limit",
            limit_price=None, stop_loss_price=180.0, take_profit_price=200.0,
        )


def test_execution_result_ok() -> None:
    r = ExecutionResult(submitted=True, order_id="abc", error=None, raw_response={})
    assert r.submitted
