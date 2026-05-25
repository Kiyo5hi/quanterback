"""Fundamental ratios provider — sources institutional-grade valuation metrics.

Uses yfinance Ticker.info as primary source for P/E, PEG, P/B, etc.
Ratios are cached aggressively (24h) since they change slowly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


class FundamentalRatiosProvider:
    """Wraps yfinance to pull institutional-grade valuation and profitability ratios.

    Caches aggressively (24h) since ratios change slowly.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_fundamentals(self, ticker: str) -> dict:
        """Returns a dict of ratio_name → float (or None).

        Keys match FundamentalLite field names (pe_ratio, fcf_yield, etc).
        All None values if any error occurs.
        """
        ticker = ticker.upper()

        # Check cache first (24h TTL)
        if self.cache_dir is not None:
            cached = self._read_cache(ticker)
            if cached is not None:
                return cached

        try:
            import yfinance as yf  # type: ignore[import-untyped]
            t = yf.Ticker(ticker)
            info = t.info or {}

            result = {
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "peg_ratio": info.get("pegRatio"),
                "price_to_book": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),  # already decimal
                "profit_margin": info.get("profitMargins"),  # already decimal
                "debt_to_equity": info.get("debtToEquity"),
                "revenue_growth_yoy": info.get("revenueGrowth"),  # already decimal
                "fcf_yield": None,  # Compute from FCF / market cap if both present
            }

            # Compute FCF yield if both available
            free_cash_flow = info.get("freeCashflow")
            market_cap = info.get("marketCap")
            if free_cash_flow is not None and market_cap is not None and market_cap > 0:
                result["fcf_yield"] = free_cash_flow / market_cap

            # Normalize any that are out of range or NaN
            result = {k: _normalize_float(v) for k, v in result.items()}

            # Write cache
            if self.cache_dir is not None:
                self._write_cache(ticker, result)

            return result
        except Exception as e:
            log.warning("fetch_fundamentals(%s) failed: %s", ticker, e)
            return {}

    def _cache_path(self, ticker: str) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / f"{ticker}_fundamentals.parquet"

    def _read_cache(self, ticker: str) -> dict | None:
        if self.cache_dir is None:
            return None
        path = self._cache_path(ticker)
        if not path.exists():
            return None
        try:
            now = datetime.now(tz=timezone.utc)
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if age > timedelta(hours=24):
                return None
            df = pd.read_parquet(path)
            if df.empty:
                return None
            row = df.iloc[0]
            return {
                "pe_ratio": _normalize_float(row.get("pe_ratio")),
                "forward_pe": _normalize_float(row.get("forward_pe")),
                "peg_ratio": _normalize_float(row.get("peg_ratio")),
                "price_to_book": _normalize_float(row.get("price_to_book")),
                "fcf_yield": _normalize_float(row.get("fcf_yield")),
                "roe": _normalize_float(row.get("roe")),
                "profit_margin": _normalize_float(row.get("profit_margin")),
                "debt_to_equity": _normalize_float(row.get("debt_to_equity")),
                "revenue_growth_yoy": _normalize_float(row.get("revenue_growth_yoy")),
            }
        except Exception as e:
            log.debug("Fundamentals cache read failed for %s: %s", ticker, e)
            return None

    def _write_cache(self, ticker: str, data: dict) -> None:
        if self.cache_dir is None:
            return
        try:
            df = pd.DataFrame([data])
            df.to_parquet(self._cache_path(ticker))
        except Exception as e:
            log.debug("Fundamentals cache write failed for %s: %s", ticker, e)


def _normalize_float(val: object) -> float | None:
    """Convert value to float, returning None if invalid."""
    if val is None:
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        # Reject NaN, inf, or extremely out-of-range values
        if f != f:  # NaN check
            return None
        if not (-1e6 < f < 1e6):  # Reject unreasonable outliers
            return None
        return f
    except (TypeError, ValueError):
        return None
