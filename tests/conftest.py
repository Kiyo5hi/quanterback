"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path

from quanterback.i18n import I18n

REAL_TEMPLATES_DIR = Path(__file__).parent.parent / "config" / "templates"


@pytest.fixture
def real_templates_dir() -> Path:
    """Always returns the real templates dir. Tests must handle the real template's vars."""
    if not REAL_TEMPLATES_DIR.exists():
        pytest.skip(f"Templates dir not found at {REAL_TEMPLATES_DIR}")
    return REAL_TEMPLATES_DIR


@pytest.fixture
def i18n_en(real_templates_dir) -> I18n:
    return I18n(language="en", templates_dir=real_templates_dir)


@pytest.fixture
def i18n_zh(real_templates_dir) -> I18n:
    return I18n(language="zh", templates_dir=real_templates_dir)
