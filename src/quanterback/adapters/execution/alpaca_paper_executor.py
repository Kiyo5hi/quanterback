"""Backwards-compatible alias for AlpacaPaperBroker."""
from __future__ import annotations

from quanterback.adapters.execution.alpaca_broker import AlpacaPaperBroker

# For backwards compatibility
AlpacaPaperExecutor = AlpacaPaperBroker
