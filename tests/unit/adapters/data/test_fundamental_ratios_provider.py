from __future__ import annotations

import os
from pathlib import Path

import pytest

from quanterback.adapters.data.fundamental_ratios_provider import FundamentalRatiosProvider


def test_fetch_fundamentals_returns_dict_of_floats_or_none(tmp_path: Path) -> None:
    """Test that fetch_fundamentals returns expected dict structure."""
    if os.environ.get("CI"):
        pytest.skip("Skipping network test in CI")

    provider = FundamentalRatiosProvider(cache_dir=tmp_path)
    result = provider.fetch_fundamentals("MSFT")
    assert isinstance(result, dict)

    # Check all expected keys are present
    expected_keys = {
        "pe_ratio", "forward_pe", "peg_ratio", "price_to_book",
        "fcf_yield", "roe", "profit_margin", "debt_to_equity",
        "revenue_growth_yoy",
    }
    assert set(result.keys()) == expected_keys

    # At minimum, a few should be non-None for a major company like MSFT
    populated = [k for k, v in result.items() if v is not None]
    assert len(populated) >= 3, f"Expected ≥3 ratios populated, got {populated}"


def test_fetch_fundamentals_unknown_ticker_returns_empty_dict(tmp_path: Path) -> None:
    """Test that unknown ticker returns empty dict (no exception)."""
    if os.environ.get("CI"):
        pytest.skip("Skipping network test in CI")

    provider = FundamentalRatiosProvider(cache_dir=tmp_path)
    result = provider.fetch_fundamentals("ZZZZZZ")
    # Should not raise; either {} or all None values
    assert isinstance(result, dict)


def test_cache_writes_and_reads(tmp_path: Path) -> None:
    """Test that cache write and read work correctly."""
    provider = FundamentalRatiosProvider(cache_dir=tmp_path)
    test_data = {
        "pe_ratio": 25.5,
        "forward_pe": 24.0,
        "peg_ratio": 2.1,
        "price_to_book": 15.0,
        "fcf_yield": 0.05,
        "roe": 0.25,
        "profit_margin": 0.20,
        "debt_to_equity": 0.5,
        "revenue_growth_yoy": 0.10,
    }
    provider._write_cache("TEST", test_data)

    # Read it back
    cached = provider._read_cache("TEST")
    assert cached is not None
    assert cached["pe_ratio"] == 25.5
    assert cached["roe"] == 0.25


def test_normalize_float_handles_invalid_values() -> None:
    """Test that normalization rejects NaN, inf, and out-of-range values."""
    from quanterback.adapters.data.fundamental_ratios_provider import _normalize_float

    assert _normalize_float(25.5) == 25.5
    assert _normalize_float(None) is None
    assert _normalize_float("invalid") is None
    assert _normalize_float(float("nan")) is None
    assert _normalize_float(float("inf")) is None
    assert _normalize_float(1e7) is None  # Out of range
