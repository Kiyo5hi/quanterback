"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path

from quanterback.i18n import I18n

_CANDIDATE_TEMPLATES_DIRS = [
    Path(__file__).parent.parent / "config" / "templates",  # repo root (CI / dev)
    Path("/config/templates"),                              # container mount
]


def _find_templates_dir() -> Path | None:
    for p in _CANDIDATE_TEMPLATES_DIRS:
        if p.exists():
            return p
    return None


@pytest.fixture
def real_templates_dir() -> Path:
    """Always returns the real templates dir. Tests must handle the real template's vars."""
    found = _find_templates_dir()
    if found is None:
        pytest.skip(f"Templates dir not found in any of: {_CANDIDATE_TEMPLATES_DIRS}")
    return found


@pytest.fixture
def i18n_en(real_templates_dir) -> I18n:
    return I18n(language="en", templates_dir=real_templates_dir)


@pytest.fixture
def i18n_zh(real_templates_dir) -> I18n:
    return I18n(language="zh", templates_dir=real_templates_dir)
