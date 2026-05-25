from __future__ import annotations

from pathlib import Path

import pytest

from quanterback.adapters.data.yfinance_provider import YFinanceProvider
from tests.fakes.yfinance_stub import StubTicker, make_daily_df, make_hourly_df


@pytest.fixture()
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> YFinanceProvider:
    daily = make_daily_df()
    hourly = make_hourly_df()
    stub = StubTicker(daily, hourly)
    monkeypatch.setattr(
        "quanterback.adapters.data.yfinance_provider.yf.Ticker",
        lambda symbol: stub,
    )
    return YFinanceProvider(cache_dir=tmp_path, cache_ttl_hours=4)


def test_fetch_returns_price_window(provider: YFinanceProvider) -> None:
    pw = provider.fetch("AAPL")
    assert pw.ticker == "AAPL"
    assert len(pw.daily) >= 250
    assert len(pw.hourly) >= 30
    assert "close" in pw.daily.columns  # lowercased


def test_fetch_writes_parquet_cache(provider: YFinanceProvider, tmp_path: Path) -> None:
    provider.fetch("AAPL")
    files = list(tmp_path.glob("AAPL_*.parquet"))
    assert len(files) >= 1


def test_cache_hit_skips_remote(provider: YFinanceProvider, tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    provider.fetch("AAPL")
    calls = {"n": 0}

    def boom(symbol: str):
        calls["n"] += 1
        raise RuntimeError("must not be called")

    monkeypatch.setattr("quanterback.adapters.data.yfinance_provider.yf.Ticker", boom)
    pw = provider.fetch("AAPL")  # second call should use cache
    assert pw.ticker == "AAPL"
    assert calls["n"] == 0


def test_fetch_historical_returns_dataframe(provider: YFinanceProvider) -> None:
    df = provider.fetch_historical("AAPL", years=3)
    assert "close" in df.columns
    assert len(df) >= 250


def test_fetch_earnings_date_returns_date_or_none(provider: YFinanceProvider) -> None:
    d = provider.fetch_next_earnings_date("AAPL")
    assert d is not None
    from datetime import date
    assert isinstance(d, date)


def test_fetch_insider_activity_aggregates(provider: YFinanceProvider) -> None:
    ia = provider.fetch_insider_activity("AAPL", lookback_days=30)
    assert ia is not None
    assert ia.n_buys >= 0
    assert ia.n_sells >= 0
    assert ia.lookback_days == 30


def test_fetch_analyst_actions_filters_to_window(provider: YFinanceProvider) -> None:
    actions = provider.fetch_analyst_actions("AAPL", lookback_days=14)
    assert isinstance(actions, list)
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=14)).date()
    for action in actions:
        assert action.date >= cutoff


def test_fetch_short_interest_extracts_from_info(provider: YFinanceProvider) -> None:
    si = provider.fetch_short_interest("AAPL")
    assert si is not None
    assert si.short_pct_of_float is not None
    assert 0 <= si.short_pct_of_float <= 1


def test_fetch_eps_trend_extracts_growth(provider: YFinanceProvider) -> None:
    eps = provider.fetch_eps_trend("AAPL")
    assert eps is not None
    if eps.current_estimate is not None:
        assert eps.current_estimate > 0
