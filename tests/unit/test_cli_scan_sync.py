"""Test that pipeline.run_for_tickers exists and is callable."""
from __future__ import annotations

from quanterback.pipeline import ScanPipeline


def test_pipeline_has_run_for_tickers_method() -> None:
    """Verify ScanPipeline has run_for_tickers method."""
    assert hasattr(ScanPipeline, "run_for_tickers")
    assert callable(getattr(ScanPipeline, "run_for_tickers"))


def test_run_for_tickers_is_separate_from_run() -> None:
    """Verify run_for_tickers and run are distinct methods."""
    assert hasattr(ScanPipeline, "run")
    assert hasattr(ScanPipeline, "run_for_tickers")
    assert ScanPipeline.run != ScanPipeline.run_for_tickers
