from __future__ import annotations

import pytest
from pydantic import ValidationError

from quanterback.domain.decision import (
    MeanReversionParams,
    MomentumParams,
    StrategyDecision,
)


def test_buy_requires_params() -> None:
    with pytest.raises(ValidationError) as exc:
        StrategyDecision(
            action="BUY",
            ticker="AAPL",
            strategy="MOMENTUM",
            params=None,
            rationale="trend is up and volume confirmed",
            confidence=0.7,
        )
    assert "params" in str(exc.value).lower()


def test_pass_allows_null_params() -> None:
    d = StrategyDecision(
        action="PASS",
        ticker="AAPL",
        strategy="MOMENTUM",
        params=None,
        rationale="extended above SMA200, risk/reward unfavourable",
        confidence=0.4,
    )
    assert d.params is None


def test_lookback_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        MomentumParams(lookback_days=3, momentum_threshold=0.05)


def test_momentum_threshold_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        MomentumParams(lookback_days=20, momentum_threshold=0.5)


def test_rationale_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        StrategyDecision(
            action="PASS", ticker="AAPL", strategy="MOMENTUM",
            params=None, rationale="nope", confidence=0.5,
        )


def test_mean_reversion_params_valid() -> None:
    p = MeanReversionParams(lookback_days=20, entry_z_score=2.0)
    assert p.lookback_days == 20
    assert p.entry_z_score == 2.0


def test_mean_reversion_z_score_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        MeanReversionParams(lookback_days=20, entry_z_score=0.5)
    with pytest.raises(ValidationError):
        MeanReversionParams(lookback_days=20, entry_z_score=5.0)


def test_buy_with_mean_reversion_params_works() -> None:
    d = StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MEAN_REVERSION",
        params=MeanReversionParams(lookback_days=20, entry_z_score=2.0),
        rationale="price 2.5 std below 20d mean, expecting bounce to mean",
        confidence=0.6,
    )
    assert d.strategy == "MEAN_REVERSION"
    assert isinstance(d.params, MeanReversionParams)
