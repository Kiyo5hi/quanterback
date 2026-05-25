# quanterback v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v0 LLM-driven autonomous trading harness against Alpaca Paper Trading, per `docs/superpowers/specs/2026-05-22-quanterback-trading-harness-design.md`.

**Architecture:** Three-layer ports-and-adapters Python application. Data → LLM Decision → Risk Barrier. Single `scan` process invoked by cron + a long-running Telegram `control-bot` process. SQLite persistence. Container-native (Docker Compose). All external dependencies abstracted as `typing.Protocol`; one concrete implementation per port in v0.

**Tech Stack:** Python 3.12+, pydantic v2, pandas+numpy, anthropic SDK, alpaca-py, yfinance, python-telegram-bot, pytest, tomllib (stdlib), supercronic, Docker.

**Reference spec sections:** Cited inline as `[spec §N.M]`.

---

## How to Execute This Plan

- Steps are bite-sized (2-5 minutes each). Run them in order within a task.
- Every task ends with a `Commit` step. Commit per task, not per step.
- TDD throughout: every new module gets a failing test first.
- **No skipping tests.** A test must fail before its implementation lands.
- The plan is divided into 8 phases (0-7). Each phase has an exit milestone — verify it before moving on.

---

## Phase 0 — Project Foundation

**Goal:** Get a working Python 3.12 project, container build, and `pytest` runnable inside the container.

**Exit milestone:** `make test` passes (running a placeholder test inside the container) and `make build` succeeds.

### Task 0.1: Initialize Python project with uv

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "quanterback"
version = "0.1.0"
description = "LLM-driven autonomous trading harness for Alpaca Paper Trading"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "pandas>=2.2",
    "numpy>=1.26",
    "yfinance>=0.2.40",
    "anthropic>=0.40",
    "alpaca-py>=0.30",
    "python-telegram-bot>=21.0",
    "pyarrow>=15.0",         # for Parquet cache
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "mypy>=1.10",
]

[project.scripts]
quanterback = "quanterback.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/quanterback"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src"]
```

- [ ] **Step 2: Write `.python-version`**

```
3.12
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml .python-version
git commit -m "build: initialize Python project with uv-managed dependencies"
```

### Task 0.2: Create source layout and placeholder modules

**Files:**
- Create: `src/quanterback/__init__.py`
- Create: `src/quanterback/cli.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_smoke.py`

- [ ] **Step 1: Write `src/quanterback/__init__.py`**

```python
"""quanterback — LLM-driven autonomous trading harness."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Write `src/quanterback/cli.py`**

```python
"""CLI entrypoint. Real wiring lands in Phase 7."""
from __future__ import annotations


def main() -> int:
    print("quanterback v0 — not yet implemented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Write `tests/__init__.py`, `tests/unit/__init__.py`**

Both empty files (just create them).

- [ ] **Step 4: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
from __future__ import annotations
```

- [ ] **Step 5: Write `tests/unit/test_smoke.py`**

```python
from __future__ import annotations

import quanterback


def test_package_has_version() -> None:
    assert quanterback.__version__ == "0.1.0"
```

- [ ] **Step 6: Commit**

```bash
git add src/ tests/
git commit -m "build: scaffold src layout, cli entrypoint, smoke test"
```

### Task 0.3: Write Dockerfile + supercronic install

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/crontab`
- Create: `docker/entrypoint.sh`

- [ ] **Step 1: Write `docker/Dockerfile`**

```dockerfile
FROM python:3.12-slim

# System deps for pandas/yfinance/SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# supercronic — container-native cron
ARG SUPERCRONIC_VERSION=0.2.29
ARG SUPERCRONIC_SHA1=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
RUN curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
 && echo "${SUPERCRONIC_SHA1}  /usr/local/bin/supercronic" | sha1sum -c - \
 && chmod +x /usr/local/bin/supercronic

# uv for fast dependency install
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system -e ".[dev]"

COPY src/ ./src/
COPY tests/ ./tests/
COPY docker/crontab /app/docker/crontab
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# Re-install to pick up src/
RUN uv pip install --system -e ".[dev]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["quanterback"]
```

- [ ] **Step 2: Write `docker/crontab`**

```
# Supercronic crontab — America/New_York
# Hourly during market hours (Mon-Fri, 9am-4pm ET)
0 9-16 * * 1-5    quanterback scan
# Extra run 30 minutes after open
30 9 * * 1-5      quanterback scan
```

- [ ] **Step 3: Write `docker/entrypoint.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# If first arg is a recognized subcommand, exec it directly.
exec "$@"
```

- [ ] **Step 4: Commit**

```bash
git add docker/
git commit -m "build: add Dockerfile with supercronic and uv-based deps"
```

### Task 0.4: Write docker-compose.yml + Makefile

**Files:**
- Create: `docker-compose.yml`
- Create: `Makefile`
- Create: `.env.example`

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  scan:
    build:
      context: .
      dockerfile: docker/Dockerfile
    image: quanterback:dev
    command: supercronic /app/docker/crontab
    volumes:
      - ./data:/data
      - ./config:/config:ro
    env_file: .env
    environment:
      - TZ=America/New_York
    restart: unless-stopped

  control-bot:
    image: quanterback:dev
    command: quanterback control-bot
    depends_on:
      - scan
    volumes:
      - ./data:/data
      - ./config:/config:ro
    env_file: .env
    restart: unless-stopped
```

- [ ] **Step 2: Write `Makefile`**

```makefile
.PHONY: build up down scan-once test lint typecheck logs shell clean

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

scan-once:
	docker compose run --rm scan quanterback scan

test:
	docker compose run --rm scan pytest -v

lint:
	docker compose run --rm scan ruff check src tests

typecheck:
	docker compose run --rm scan mypy src

logs:
	docker compose logs -f

shell:
	docker compose run --rm scan bash

clean:
	docker compose down -v
	rm -rf data/cache/*.parquet
```

- [ ] **Step 3: Write `.env.example`**

```
# Anthropic
ANTHROPIC_API_KEY=

# Alpaca Paper Trading
ALPACA_API_KEY=
ALPACA_SECRET=

# Telegram
TELEGRAM_BOT_TOKEN=
```

- [ ] **Step 4: Create empty `.env` (gitignored)**

```bash
cp .env.example .env
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml Makefile .env.example
git commit -m "build: add docker-compose orchestration and Makefile"
```

### Task 0.5: Verify container build + smoke test

- [ ] **Step 1: Build the image**

Run: `make build`
Expected: image `quanterback:dev` built without errors.

- [ ] **Step 2: Run smoke test inside container**

Run: `make test`
Expected: `tests/unit/test_smoke.py::test_package_has_version PASSED`

- [ ] **Step 3: Verify lint and typecheck pass**

Run: `make lint && make typecheck`
Expected: both exit 0 (no findings on the placeholder code).

- [ ] **Step 4: Commit any tooling-driven fixes if needed**

If lint or typecheck modified anything, commit:
```bash
git add -A
git commit -m "build: pass lint and typecheck on phase 0 scaffold"
```

**Phase 0 milestone reached.** Container builds, tests run, lint and typecheck are green.

---

## Phase 1 — Config, Domain DTOs, and Interfaces

**Goal:** Define the entire shape of the system without any business logic. All `pydantic.BaseModel` DTOs and all `typing.Protocol` interfaces land here, in single-purpose files. The `AppConfig` TOML loader is also implemented.

**Exit milestone:** Every Protocol is defined, every DTO is defined and unit-tested for validation rules, and `AppConfig.load(path)` reads a sample TOML and yields the expected dataclass.

### Task 1.1: Domain — events module

**Files:**
- Create: `src/quanterback/domain/__init__.py`
- Create: `src/quanterback/domain/events.py`
- Create: `tests/unit/domain/__init__.py`
- Create: `tests/unit/domain/test_events.py`

- [ ] **Step 1: Write failing test**

`tests/unit/domain/test_events.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from quanterback.domain.events import (
    ControlCommand,
    NotificationEvent,
    ScanEvent,
)


def test_scan_event_minimal() -> None:
    e = ScanEvent(
        ticker="AAPL",
        source="watchlist",
        requested_at=datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc),
    )
    assert e.ticker == "AAPL"
    assert e.priority == 0


def test_scan_event_ticker_uppercased() -> None:
    e = ScanEvent(
        ticker="aapl",
        source="watchlist",
        requested_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert e.ticker == "AAPL"


def test_control_command_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ControlCommand(
            command="explode",
            actor="user1",
            received_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
        )


def test_notification_event_payload_is_dict() -> None:
    n = NotificationEvent(
        kind="decision",
        payload={"ticker": "AAPL", "action": "BUY"},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert n.payload["ticker"] == "AAPL"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `make test`
Expected: FAIL with `ModuleNotFoundError: quanterback.domain`

- [ ] **Step 3: Create domain package**

`src/quanterback/domain/__init__.py` (empty):
```python
"""Pure data DTOs. No business logic, no I/O."""
```

- [ ] **Step 4: Write `src/quanterback/domain/events.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class ScanEvent(BaseModel):
    """One unit of work coming out of an EventSource."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    source: str
    priority: int = 0
    requested_at: datetime

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class ControlCommand(BaseModel):
    """Inbound command from a ControlChannel."""

    model_config = ConfigDict(frozen=True)

    command: Literal["freeze", "unfreeze", "halt", "unhalt", "status"]
    actor: str
    received_at: datetime


class NotificationEvent(BaseModel):
    """Outbound notification to be pushed via Notifier."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["decision", "backtest", "order", "fill", "scan_summary", "error"]
    payload: dict
    timestamp: datetime
```

- [ ] **Step 5: Create `tests/unit/domain/__init__.py`** (empty)

- [ ] **Step 6: Run tests, verify pass**

Run: `make test`
Expected: `test_events.py` 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/domain/ tests/unit/domain/
git commit -m "feat(domain): add events DTOs (ScanEvent, ControlCommand, NotificationEvent)"
```

### Task 1.2: Domain — market module (PriceWindow and CondensedSummary)

**Files:**
- Create: `src/quanterback/domain/market.py`
- Create: `tests/unit/domain/test_market.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    MovingAverages,
    PriceSnapshot,
    PriceWindow,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)


def _summary() -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL",
        as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(
            last_close=185.42, return_1d=0.008, return_5d=0.032,
            return_20d=0.085, return_60d=-0.021,
            pct_from_52w_high=-0.042, pct_from_52w_low=0.351,
        ),
        moving_averages=MovingAverages(
            sma_20=181.6, sma_50=177.7, sma_200=164.4,
            pct_above_sma_20=0.021, pct_above_sma_50=0.043, pct_above_sma_200=0.128,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.22, atr_14=3.40,
            atr_pct_of_price=0.0183, regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(
            last_volume=80_000_000, avg_volume_20d=50_000_000,
            volume_ratio=1.6, regime=VolumeRegime.ELEVATED,
        ),
        technicals=TechnicalIndicators(rsi_14=58.3, macd_signal="bullish_cross"),
        fundamentals=FundamentalLite(
            days_to_next_earnings=38, market_cap_bucket="large",
        ),
        trend_regime=TrendRegime.UPTREND,
    )


def test_condensed_summary_roundtrip_json() -> None:
    s = _summary()
    data = s.model_dump_json()
    s2 = CondensedSummary.model_validate_json(data)
    assert s2 == s


def test_condensed_summary_to_prompt_text_contains_key_facts() -> None:
    s = _summary()
    text = s.to_prompt_text()
    assert "AAPL" in text
    assert "UPTREND" in text
    assert "RSI(14): 58.3" in text or "RSI(14): 58.30" in text


def test_price_window_validates_dataframes() -> None:
    daily = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [100]})
    hourly = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [100]})
    pw = PriceWindow(
        ticker="AAPL", daily=daily, hourly=hourly,
        as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert len(pw.daily) == 1


def test_rsi_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        TechnicalIndicators(rsi_14=120.0, macd_signal="none")
```

- [ ] **Step 2: Run test, verify it fails**

Run: `make test tests/unit/domain/test_market.py`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `src/quanterback/domain/market.py`**

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class TrendRegime(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"


class VolatilityRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class VolumeRegime(str, Enum):
    BELOW_AVG = "below_avg"
    NORMAL = "normal"
    ELEVATED = "elevated"
    EXTREME = "extreme"


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_close: float
    return_1d: float
    return_5d: float
    return_20d: float
    return_60d: float
    pct_from_52w_high: float
    pct_from_52w_low: float


class MovingAverages(BaseModel):
    model_config = ConfigDict(frozen=True)
    sma_20: float
    sma_50: float
    sma_200: float
    pct_above_sma_20: float
    pct_above_sma_50: float
    pct_above_sma_200: float
    alignment: Literal["bullish", "bearish", "mixed"]


class VolatilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    realized_vol_20d_annualized: float = Field(ge=0)
    atr_14: float = Field(ge=0)
    atr_pct_of_price: float = Field(ge=0)
    regime: VolatilityRegime


class VolumeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_volume: int = Field(ge=0)
    avg_volume_20d: int = Field(ge=0)
    volume_ratio: float = Field(ge=0)
    regime: VolumeRegime


class TechnicalIndicators(BaseModel):
    model_config = ConfigDict(frozen=True)
    rsi_14: float = Field(ge=0, le=100)
    macd_signal: Literal["bullish_cross", "bearish_cross", "none"]


class FundamentalLite(BaseModel):
    model_config = ConfigDict(frozen=True)
    days_to_next_earnings: int | None = None
    market_cap_bucket: Literal["large", "mid", "small", "unknown"]


class CondensedSummary(BaseModel):
    """LLM-facing compressed snapshot. See spec §4.1."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: datetime
    price: PriceSnapshot
    moving_averages: MovingAverages
    volatility: VolatilityProfile
    volume: VolumeProfile
    technicals: TechnicalIndicators
    fundamentals: FundamentalLite
    trend_regime: TrendRegime

    def to_prompt_text(self) -> str:
        p = self.price
        ma = self.moving_averages
        v = self.volatility
        vol = self.volume
        t = self.technicals
        f = self.fundamentals
        ts = self.as_of.strftime("%Y-%m-%d %H:%M %Z").strip()
        macd = (
            "bullish_cross within last 5 days"
            if t.macd_signal == "bullish_cross"
            else "bearish_cross within last 5 days"
            if t.macd_signal == "bearish_cross"
            else "no recent cross"
        )
        earnings = (
            f"{f.days_to_next_earnings} days away" if f.days_to_next_earnings is not None else "unknown"
        )
        return (
            f"[{self.ticker} @ {ts}]\n"
            f"Price: ${p.last_close:.2f} "
            f"({p.return_1d:+.1%} 1d / {p.return_5d:+.1%} 5d / "
            f"{p.return_20d:+.1%} 20d / {p.return_60d:+.1%} 60d)\n"
            f"52w range: {p.pct_from_52w_high:+.1%} from high, "
            f"{p.pct_from_52w_low:+.1%} from low\n"
            f"Trend: {self.trend_regime.value.upper()}  "
            f"(price above SMA20 {ma.pct_above_sma_20:+.1%}, "
            f"SMA50 {ma.pct_above_sma_50:+.1%}, "
            f"SMA200 {ma.pct_above_sma_200:+.1%})\n"
            f"                SMA stack alignment: {ma.alignment}\n"
            f"Volatility: {v.regime.value.upper()}  "
            f"(20d realized {v.realized_vol_20d_annualized:.0%} ann.; "
            f"ATR14 = ${v.atr_14:.2f} = {v.atr_pct_of_price:.2%} of price)\n"
            f"Volume: {vol.regime.value.upper()}  "
            f"(today {vol.volume_ratio:.1f}x 20d avg)\n"
            f"RSI(14): {t.rsi_14:.1f}\n"
            f"MACD: {macd}\n"
            f"Earnings: {earnings}\n"
            f"Market cap: {f.market_cap_bucket}\n"
        )


class PriceWindow(BaseModel):
    """Raw OHLCV bundle from a DataProvider."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    ticker: str
    daily: pd.DataFrame
    hourly: pd.DataFrame
    as_of: datetime
```

- [ ] **Step 4: Run tests, verify pass**

Run: `make test`
Expected: 4 tests PASS in `test_market.py`.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/domain/market.py tests/unit/domain/test_market.py
git commit -m "feat(domain): add market DTOs (PriceWindow, CondensedSummary with to_prompt_text)"
```

### Task 1.3: Domain — decision module

**Files:**
- Create: `src/quanterback/domain/decision.py`
- Create: `tests/unit/domain/test_decision.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from quanterback.domain.decision import MomentumParams, StrategyDecision


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
```

- [ ] **Step 2: Run test, verify it fails**

Run: `make test tests/unit/domain/test_decision.py`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/quanterback/domain/decision.py`**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MomentumParams(BaseModel):
    """Parameters for the Momentum strategy."""

    model_config = ConfigDict(frozen=True)

    lookback_days: int = Field(
        ge=5, le=60, description="Days to evaluate momentum"
    )
    momentum_threshold: float = Field(
        ge=0.0, le=0.30,
        description="Required cumulative return over lookback window",
    )


class StrategyDecision(BaseModel):
    """LLM output. JSON-schema-enforced. See spec §4.2."""

    model_config = ConfigDict(frozen=True)

    action: Literal["BUY", "PASS"]
    ticker: str
    strategy: Literal["MOMENTUM"]
    params: MomentumParams | None = None
    rationale: str = Field(min_length=20, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _params_required_for_buy(self) -> StrategyDecision:
        if self.action == "BUY" and self.params is None:
            raise ValueError("BUY action requires non-null params")
        return self
```

- [ ] **Step 4: Run tests, verify pass**

Run: `make test`
Expected: 5 tests PASS in `test_decision.py`.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/domain/decision.py tests/unit/domain/test_decision.py
git commit -m "feat(domain): add StrategyDecision and MomentumParams with strict validation"
```

### Task 1.4: Domain — backtest, risk, order, position, state, persisted

**Files:**
- Create: `src/quanterback/domain/backtest.py`
- Create: `src/quanterback/domain/risk.py`
- Create: `src/quanterback/domain/order.py`
- Create: `src/quanterback/domain/position.py`
- Create: `src/quanterback/domain/state.py`
- Create: `src/quanterback/domain/persisted.py`
- Create: `tests/unit/domain/test_backtest.py`
- Create: `tests/unit/domain/test_risk.py`
- Create: `tests/unit/domain/test_order.py`
- Create: `tests/unit/domain/test_misc_dtos.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/domain/test_backtest.py`:
```python
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quanterback.domain.backtest import BacktestReport, BacktestRequest, TradeRecord


def test_backtest_request_defaults_lookback() -> None:
    r = BacktestRequest(ticker="AAPL", strategy="MOMENTUM",
                        params={"lookback_days": 20, "momentum_threshold": 0.05})
    assert r.lookback_years == 3


def test_trade_record_exit_reason_strict() -> None:
    with pytest.raises(ValidationError):
        TradeRecord(
            entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 10),
            entry_price=100, exit_price=110, return_pct=0.10,
            bars_held=10, exit_reason="i_changed_my_mind",
        )


def test_backtest_report_holds_trades() -> None:
    r = BacktestReport(
        ticker="AAPL", strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=42, win_rate=0.5, max_drawdown=0.06, sharpe=0.8,
        profit_factor=1.5, cumulative_return=0.20, avg_trade_return=0.005,
        avg_bars_held=7.5, trades=[],
    )
    assert r.num_trades == 42
```

`tests/unit/domain/test_risk.py`:
```python
from __future__ import annotations

from quanterback.domain.risk import RiskAssessment, RiskThresholds


def test_risk_thresholds_have_defaults() -> None:
    t = RiskThresholds()
    assert t.max_drawdown == 0.08
    assert t.min_sharpe == 0.5
    assert t.min_num_trades == 30


def test_risk_assessment_with_failures() -> None:
    a = RiskAssessment(passed=False, failed_checks=["max_drawdown", "min_sharpe"])
    assert not a.passed
    assert len(a.failed_checks) == 2
```

`tests/unit/domain/test_order.py`:
```python
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
```

`tests/unit/domain/test_misc_dtos.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from quanterback.domain.position import OpenLifecycle
from quanterback.domain.state import SystemState


def test_open_lifecycle_state_literal() -> None:
    lc = OpenLifecycle(
        ticker="AAPL", order_id="abc", state="pending",
        opened_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    assert lc.state == "pending"


def test_system_state_defaults_to_normal() -> None:
    s = SystemState(mode="normal", updated_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
                    updated_by="bootstrap")
    assert s.mode == "normal"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `make test`
Expected: ModuleNotFoundError for each new module.

- [ ] **Step 3: Write `src/quanterback/domain/backtest.py`**

```python
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TradeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    return_pct: float
    bars_held: int = Field(ge=0)
    exit_reason: Literal["stop_loss", "take_profit", "timeout"]


class BacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    strategy: str
    params: dict
    lookback_years: int = Field(default=3, ge=1, le=10)


class BacktestReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    strategy: str
    params: dict
    period_start: date
    period_end: date
    num_trades: int = Field(ge=0)
    win_rate: float = Field(ge=0, le=1)
    max_drawdown: float = Field(ge=0, le=1)
    sharpe: float
    profit_factor: float = Field(ge=0)
    cumulative_return: float
    avg_trade_return: float
    avg_bars_held: float = Field(ge=0)
    trades: list[TradeRecord]
```

- [ ] **Step 4: Write `src/quanterback/domain/risk.py`**

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RiskThresholds(BaseModel):
    """Hard thresholds for the RiskGate. All checks must pass."""

    model_config = ConfigDict(frozen=True)

    max_drawdown: float = Field(default=0.08, ge=0, le=1)
    min_sharpe: float = Field(default=0.5)
    min_win_rate: float = Field(default=0.40, ge=0, le=1)
    min_profit_factor: float = Field(default=1.2, ge=0)
    min_num_trades: int = Field(default=30, ge=0)


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)
    passed: bool
    failed_checks: list[str]
```

- [ ] **Step 5: Write `src/quanterback/domain/order.py`**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BracketOrderSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    side: Literal["buy"]
    qty: int = Field(ge=1)
    entry_type: Literal["market", "limit"]
    limit_price: float | None = None
    stop_loss_price: float = Field(gt=0)
    take_profit_price: float = Field(gt=0)

    @model_validator(mode="after")
    def _limit_requires_price(self) -> BracketOrderSpec:
        if self.entry_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required when entry_type='limit'")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    submitted: bool
    order_id: str | None
    error: str | None
    raw_response: dict
```

- [ ] **Step 6: Write `src/quanterback/domain/position.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class OpenLifecycle(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    order_id: str
    state: Literal["pending", "filled", "bracket_active"]
    opened_at: datetime
```

- [ ] **Step 7: Write `src/quanterback/domain/state.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SystemState(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: Literal["normal", "frozen", "halted"]
    updated_at: datetime
    updated_by: str
    reason: str | None = None
```

- [ ] **Step 8: Write `src/quanterback/domain/persisted.py`**

```python
"""Persistence-layer DTOs. These shapes are what StateStore reads/writes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScanRun(BaseModel):
    model_config = ConfigDict(frozen=False)  # mutable for end-time update
    id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    source: str
    tickers_processed: int = 0
    errors_count: int = 0


class PersistedDecision(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    scan_run_id: int
    ticker: str
    summary_json: str
    decision_json: str
    llm_model: str
    llm_usage_json: str | None = None
    rejected_reason: str | None = None
    created_at: datetime


class PersistedBacktest(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    decision_id: int
    report_json: str
    passed: bool
    failed_checks: str | None = None
    created_at: datetime


class PersistedOrder(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    decision_id: int
    backtest_id: int
    bracket_spec_json: str
    alpaca_order_id: str | None = None
    submitted_at: datetime
    dry_run: bool = False
    raw_response_json: str | None = None


class PersistedPosition(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    ticker: str
    order_id: int
    state: Literal["pending", "filled", "bracket_active", "closed"]
    entry_price: float | None = None
    sl: float | None = None
    tp: float | None = None
    qty: int | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    exit_reason: str | None = None


class PersistedNotification(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: int | None = None
    event_kind: str
    payload_json: str
    sent_at: datetime | None = None
    sent_ok: bool = False
    retry_count: int = 0
    error: str | None = None
```

- [ ] **Step 9: Run tests, verify all pass**

Run: `make test`
Expected: all domain tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/quanterback/domain/ tests/unit/domain/
git commit -m "feat(domain): add backtest/risk/order/position/state/persisted DTOs"
```

### Task 1.5: Interfaces — all Protocols in one batch

Protocols are typing-only; they don't need their own tests (mypy verifies them when adapters are written).

**Files:**
- Create: `src/quanterback/interfaces/__init__.py`
- Create: `src/quanterback/interfaces/events.py`
- Create: `src/quanterback/interfaces/data.py`
- Create: `src/quanterback/interfaces/decision.py`
- Create: `src/quanterback/interfaces/risk.py`
- Create: `src/quanterback/interfaces/execution.py`
- Create: `src/quanterback/interfaces/notify.py`
- Create: `src/quanterback/interfaces/state.py`
- Create: `src/quanterback/interfaces/store.py`

- [ ] **Step 1: Write `src/quanterback/interfaces/__init__.py`**

```python
"""Ports — Python `Protocol` definitions. Adapters in `quanterback.adapters`."""
```

- [ ] **Step 2: Write `src/quanterback/interfaces/events.py`**

```python
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from quanterback.domain.events import ControlCommand, ScanEvent


class EventSource(Protocol):
    def stream(self) -> Iterable[ScanEvent]: ...


class ControlChannel(Protocol):
    def listen(self) -> Iterable[ControlCommand]: ...
```

- [ ] **Step 3: Write `src/quanterback/interfaces/data.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.market import CondensedSummary, PriceWindow


class DataProvider(Protocol):
    def fetch(self, ticker: str) -> PriceWindow: ...


class Summarizer(Protocol):
    def summarize(self, window: PriceWindow) -> CondensedSummary: ...
```

- [ ] **Step 4: Write `src/quanterback/interfaces/decision.py`**

```python
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: str
    model: str
    usage: dict


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse: ...


class LLMStrategist(Protocol):
    def decide(self, summary: CondensedSummary) -> StrategyDecision: ...


class ApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    approved: bool
    reason: str
    approver: str | None = None


class ApprovalGate(Protocol):
    def review(self, decision: StrategyDecision) -> ApprovalResult: ...
```

- [ ] **Step 5: Write `src/quanterback/interfaces/risk.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.backtest import BacktestReport, BacktestRequest
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.domain.order import BracketOrderSpec
from quanterback.domain.position import OpenLifecycle
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class PositionStateService(Protocol):
    def has_open_lifecycle(self, ticker: str) -> bool: ...
    def get_open(self, ticker: str) -> OpenLifecycle | None: ...


class Backtester(Protocol):
    def run(self, request: BacktestRequest) -> BacktestReport: ...


class RiskGate(Protocol):
    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds
    ) -> RiskAssessment: ...


class OrderBuilder(Protocol):
    def build(
        self,
        decision: StrategyDecision,
        summary: CondensedSummary,
        account_value: float,
    ) -> BracketOrderSpec: ...
```

- [ ] **Step 6: Write `src/quanterback/interfaces/execution.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


class Executor(Protocol):
    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult: ...

    def get_account_value(self) -> float: ...
```

- [ ] **Step 7: Write `src/quanterback/interfaces/notify.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.events import NotificationEvent


class Notifier(Protocol):
    def push(self, event: NotificationEvent) -> None:
        """MUST NOT raise. Failures are caught and logged internally."""
        ...
```

- [ ] **Step 8: Write `src/quanterback/interfaces/state.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.state import SystemState


class SystemStateService(Protocol):
    def get_current(self) -> SystemState: ...
    def set(self, mode: str, reason: str, actor: str) -> None: ...
```

- [ ] **Step 9: Write `src/quanterback/interfaces/store.py`**

```python
from __future__ import annotations

from typing import Protocol

from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedNotification,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.domain.position import OpenLifecycle


class StateStore(Protocol):
    """Repository pattern. Raw SQL inside adapters; no ORM."""

    def insert_scan_run(self, run: ScanRun) -> int: ...
    def update_scan_run(self, run: ScanRun) -> None: ...
    def insert_decision(self, decision: PersistedDecision) -> int: ...
    def insert_backtest(self, report: PersistedBacktest) -> int: ...
    def insert_order(self, order: PersistedOrder) -> int: ...
    def upsert_position(self, position: PersistedPosition) -> int: ...
    def insert_notification(self, n: PersistedNotification) -> int: ...
    def update_notification(self, n: PersistedNotification) -> None: ...
    def query_open_lifecycles(self) -> list[OpenLifecycle]: ...
    def query_recent_decisions(self, ticker: str, limit: int) -> list[PersistedDecision]: ...
    def query_pending_notifications(self) -> list[PersistedNotification]: ...
```

- [ ] **Step 10: Run typecheck (validates Protocol imports work)**

Run: `make typecheck`
Expected: passes — interfaces are pure types.

- [ ] **Step 11: Commit**

```bash
git add src/quanterback/interfaces/
git commit -m "feat(interfaces): add all 11 Protocol ports per spec §3"
```

### Task 1.6: AppConfig + TOML loader

**Files:**
- Create: `src/quanterback/config.py`
- Create: `config/quanterback.toml`
- Create: `config/watchlist.txt`
- Create: `tests/unit/test_config.py`
- Create: `tests/fixtures/sample_config.toml`

- [ ] **Step 1: Write failing test**

`tests/unit/test_config.py`:
```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from quanterback.config import AppConfig


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample_config.toml"


def test_load_defaults_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ALPACA_API_KEY", "ak-test")
    monkeypatch.setenv("ALPACA_SECRET", "as-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-test")
    cfg = AppConfig.load(toml_paths=[])
    assert cfg.position_size_pct == 0.05
    assert cfg.max_concurrent_positions == 5
    assert cfg.sl_atr_multiple == 2.0
    assert cfg.tp_atr_multiple == 4.0
    assert cfg.risk_thresholds.max_drawdown == 0.08
    assert cfg.anthropic_key == "sk-ant-test"


def test_toml_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, "x")
    cfg = AppConfig.load(toml_paths=[SAMPLE])
    assert cfg.position_size_pct == 0.03  # overridden in fixture
    assert cfg.llm_model == "claude-sonnet-4-6"


def test_missing_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AppConfig.load(toml_paths=[])


def test_local_override_beats_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, "x")
    project = tmp_path / "p.toml"
    project.write_text('[position]\nposition_size_pct = 0.10\n')
    local = tmp_path / "l.toml"
    local.write_text('[position]\nposition_size_pct = 0.07\n')
    cfg = AppConfig.load(toml_paths=[project, local])
    assert cfg.position_size_pct == 0.07
```

`tests/fixtures/sample_config.toml`:
```toml
[position]
position_size_pct = 0.03

[llm]
model = "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `make test tests/unit/test_config.py`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/config.py`**

```python
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from quanterback.domain.risk import RiskThresholds


REQUIRED_SECRETS = (
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET",
    "TELEGRAM_BOT_TOKEN",
)


@dataclass(frozen=True)
class AppConfig:
    # secrets (env-only)
    anthropic_key: str
    alpaca_key: str
    alpaca_secret: str
    tg_token: str

    # scan
    watchlist_path: Path

    # position sizing
    position_size_pct: float
    max_concurrent_positions: int

    # SL/TP
    sl_atr_multiple: float
    tp_atr_multiple: float

    # risk gate
    risk_thresholds: RiskThresholds

    # backtest
    backtest_lookback_years: int

    # llm
    llm_model: str
    llm_temperature: float
    prompt_template_path: Path

    # data
    cache_dir: Path
    cache_ttl_hours: int

    # telegram
    tg_chat_ids: tuple[str, ...]
    notifier_retry_window_hours: int

    # storage
    db_path: Path

    @classmethod
    def load(cls, toml_paths: list[Path] | None = None) -> AppConfig:
        toml_paths = toml_paths or []
        merged: dict = {}
        for p in toml_paths:
            if p.exists():
                with p.open("rb") as f:
                    _deep_merge(merged, tomllib.load(f))

        for key in REQUIRED_SECRETS:
            if not os.environ.get(key):
                raise ValueError(f"Required secret env var missing: {key}")

        scan = merged.get("scan", {})
        position = merged.get("position", {})
        risk_sl_tp = merged.get("risk", {}).get("sl_tp", {})
        risk_th = merged.get("risk", {}).get("thresholds", {})
        backtest = merged.get("backtest", {})
        llm = merged.get("llm", {})
        data = merged.get("data", {})
        telegram = merged.get("telegram", {})
        storage = merged.get("storage", {})

        return cls(
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
            alpaca_key=os.environ["ALPACA_API_KEY"],
            alpaca_secret=os.environ["ALPACA_SECRET"],
            tg_token=os.environ["TELEGRAM_BOT_TOKEN"],
            watchlist_path=Path(scan.get("watchlist_path", "/config/watchlist.txt")),
            position_size_pct=float(position.get("position_size_pct", 0.05)),
            max_concurrent_positions=int(position.get("max_concurrent_positions", 5)),
            sl_atr_multiple=float(risk_sl_tp.get("sl_atr_multiple", 2.0)),
            tp_atr_multiple=float(risk_sl_tp.get("tp_atr_multiple", 4.0)),
            risk_thresholds=RiskThresholds(
                max_drawdown=float(risk_th.get("max_drawdown", 0.08)),
                min_sharpe=float(risk_th.get("min_sharpe", 0.5)),
                min_win_rate=float(risk_th.get("min_win_rate", 0.40)),
                min_profit_factor=float(risk_th.get("min_profit_factor", 1.2)),
                min_num_trades=int(risk_th.get("min_num_trades", 30)),
            ),
            backtest_lookback_years=int(backtest.get("lookback_years", 3)),
            llm_model=str(llm.get("model", "claude-sonnet-4-6")),
            llm_temperature=float(llm.get("temperature", 0.0)),
            prompt_template_path=Path(
                llm.get("prompt_template_path", "/config/prompts/momentum_strategist.md")
            ),
            cache_dir=Path(data.get("cache_dir", "/data/cache")),
            cache_ttl_hours=int(data.get("cache_ttl_hours", 4)),
            tg_chat_ids=tuple(str(c) for c in telegram.get("chat_ids", [])),
            notifier_retry_window_hours=int(telegram.get("retry_window_hours", 1)),
            db_path=Path(storage.get("db_path", "/data/quanterback.sqlite")),
        )


def _deep_merge(base: dict, overlay: dict) -> None:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
```

- [ ] **Step 4: Write the real `config/quanterback.toml`**

```toml
[scan]
watchlist_path = "/config/watchlist.txt"

[position]
position_size_pct = 0.05
max_concurrent_positions = 5

[risk.sl_tp]
sl_atr_multiple = 2.0
tp_atr_multiple = 4.0

[risk.thresholds]
max_drawdown = 0.08
min_sharpe = 0.5
min_win_rate = 0.40
min_profit_factor = 1.2
min_num_trades = 30

[backtest]
lookback_years = 3

[llm]
model = "claude-sonnet-4-6"
temperature = 0.0
prompt_template_path = "/config/prompts/momentum_strategist.md"

[data]
cache_dir = "/data/cache"
cache_ttl_hours = 4

[telegram]
chat_ids = []
retry_window_hours = 1

[storage]
db_path = "/data/quanterback.sqlite"
```

- [ ] **Step 5: Write `config/watchlist.txt`**

```
AAPL
MSFT
GOOGL
NVDA
TSLA
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `make test tests/unit/test_config.py`
Expected: 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/config.py config/ tests/unit/test_config.py tests/fixtures/
git commit -m "feat(config): TOML-based AppConfig loader with env-secret enforcement"
```

**Phase 1 milestone reached.** All domain DTOs are tested. All 11 Protocol interfaces are defined. `AppConfig.load()` reads layered TOML and validates secrets.

---

## Phase 2 — Storage Layer (SqliteStore)

**Goal:** Implement `SqliteStore` adapter with schema migration, WAL mode enabled, and every `StateStore` method exercised by tests using a temp SQLite file.

**Exit milestone:** All `StateStore` methods unit-tested; schema migration runs cleanly on an empty DB.

### Task 2.1: Schema migration

**Files:**
- Create: `src/quanterback/adapters/__init__.py`
- Create: `src/quanterback/adapters/store/__init__.py`
- Create: `src/quanterback/adapters/store/schema.py`
- Create: `tests/unit/adapters/__init__.py`
- Create: `tests/unit/adapters/store/__init__.py`
- Create: `tests/unit/adapters/store/test_schema.py`

- [ ] **Step 1: Write failing test**

`tests/unit/adapters/store/test_schema.py`:
```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from quanterback.adapters.store.schema import apply_schema


def test_apply_schema_creates_all_tables(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    expected = {
        "scan_runs", "decisions", "backtests", "orders",
        "positions", "system_state", "notifications",
    }
    assert expected.issubset(names)


def test_apply_schema_enables_wal(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_apply_schema_creates_unique_active_position_index(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    finally:
        conn.close()
    assert any("idx_one_active_per_ticker" == r[0] for r in rows)


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        apply_schema(conn)  # second time must not raise
    finally:
        conn.close()
```

- [ ] **Step 2: Run test, verify fail**

Run: `make test tests/unit/adapters/store/test_schema.py`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/quanterback/adapters/__init__.py`** (empty)

- [ ] **Step 4: Write `src/quanterback/adapters/store/__init__.py`** (empty)

- [ ] **Step 5: Write `src/quanterback/adapters/store/schema.py`**

```python
from __future__ import annotations

import sqlite3


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  source TEXT NOT NULL,
  tickers_processed INTEGER NOT NULL DEFAULT 0,
  errors_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
  ticker TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  llm_usage_json TEXT,
  rejected_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker_created
  ON decisions(ticker, created_at);

CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  report_json TEXT NOT NULL,
  passed INTEGER NOT NULL,
  failed_checks TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  backtest_id INTEGER NOT NULL REFERENCES backtests(id),
  bracket_spec_json TEXT NOT NULL,
  alpaca_order_id TEXT,
  submitted_at TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 0,
  raw_response_json TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  state TEXT NOT NULL,
  entry_price REAL,
  sl REAL,
  tp REAL,
  qty INTEGER,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  exit_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_per_ticker
  ON positions(ticker) WHERE state != 'closed';

CREATE TABLE IF NOT EXISTS system_state (
  id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL,
  reason TEXT,
  actor TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  sent_at TEXT,
  sent_ok INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 6: Run tests, verify pass**

Run: `make test tests/unit/adapters/store/test_schema.py`
Expected: 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/adapters/ tests/unit/adapters/
git commit -m "feat(store): add SQLite schema migration with WAL and unique-active-position index"
```

### Task 2.2: SqliteStore — connection lifecycle and scan_runs CRUD

**Files:**
- Create: `src/quanterback/adapters/store/sqlite_store.py`
- Create: `tests/unit/adapters/store/test_sqlite_store.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import ScanRun


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "test.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)


def test_insert_scan_run_returns_id(store: SqliteStore) -> None:
    rid = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    assert rid >= 1


def test_update_scan_run_sets_ended(store: SqliteStore) -> None:
    run = ScanRun(started_at=_now(), source="cron")
    rid = store.insert_scan_run(run)
    run.id = rid
    run.ended_at = _now()
    run.tickers_processed = 7
    store.update_scan_run(run)
    rows = store._conn.execute(
        "SELECT ended_at, tickers_processed FROM scan_runs WHERE id=?", (rid,)
    ).fetchone()
    assert rows[0] is not None
    assert rows[1] == 7
```

- [ ] **Step 2: Run test, verify fail**

Run: `make test tests/unit/adapters/store/test_sqlite_store.py`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/store/sqlite_store.py` (initial)**

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from quanterback.adapters.store.schema import apply_schema
from quanterback.domain.persisted import ScanRun


class SqliteStore:
    """Concrete StateStore backed by a single SQLite file. WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage txns explicitly
        )
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def insert_scan_run(self, run: ScanRun) -> int:
        cur = self._conn.execute(
            "INSERT INTO scan_runs (started_at, source, tickers_processed, errors_count) "
            "VALUES (?, ?, ?, ?)",
            (run.started_at.isoformat(), run.source, run.tickers_processed, run.errors_count),
        )
        return int(cur.lastrowid or 0)

    def update_scan_run(self, run: ScanRun) -> None:
        assert run.id is not None
        self._conn.execute(
            "UPDATE scan_runs SET ended_at=?, tickers_processed=?, errors_count=? WHERE id=?",
            (
                run.ended_at.isoformat() if run.ended_at else None,
                run.tickers_processed,
                run.errors_count,
                run.id,
            ),
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `make test tests/unit/adapters/store/test_sqlite_store.py`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/store/sqlite_store.py tests/unit/adapters/store/test_sqlite_store.py
git commit -m "feat(store): SqliteStore with scan_runs CRUD"
```

### Task 2.3: SqliteStore — decisions, backtests, orders, notifications

- [ ] **Step 1: Append failing tests to `tests/unit/adapters/store/test_sqlite_store.py`**

```python
from quanterback.domain.persisted import (
    PersistedBacktest, PersistedDecision, PersistedNotification, PersistedOrder,
)


def test_insert_decision_returns_id(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json='{"x":1}', decision_json='{"action":"PASS"}',
        llm_model="claude-sonnet-4-6", created_at=_now(),
    ))
    assert did >= 1


def test_insert_backtest_links_to_decision(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    assert bid >= 1


def test_insert_order_links_decision_and_backtest(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    oid = store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))
    assert oid >= 1


def test_query_recent_decisions_ordered_desc(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    for i in range(3):
        store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker="AAPL", summary_json=str(i),
            decision_json="{}", llm_model="m",
            created_at=datetime(2026, 5, 22, 14, i, tzinfo=timezone.utc),
        ))
    recent = store.query_recent_decisions("AAPL", limit=2)
    assert len(recent) == 2
    # most recent first
    assert recent[0].summary_json == "2"


def test_insert_and_update_notification(store: SqliteStore) -> None:
    nid = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    assert nid >= 1
    store.update_notification(PersistedNotification(
        id=nid, event_kind="decision", payload_json="{}",
        sent_at=_now(), sent_ok=True,
    ))
    rows = store._conn.execute(
        "SELECT sent_ok FROM notifications WHERE id=?", (nid,)
    ).fetchone()
    assert rows[0] == 1


def test_query_pending_notifications_only_unsent(store: SqliteStore) -> None:
    nid_pending = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    nid_sent = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    store.update_notification(PersistedNotification(
        id=nid_sent, event_kind="decision", payload_json="{}",
        sent_at=_now(), sent_ok=True,
    ))
    pending = store.query_pending_notifications()
    ids = {p.id for p in pending}
    assert nid_pending in ids
    assert nid_sent not in ids
```

- [ ] **Step 2: Run, verify fail**

Run: `make test tests/unit/adapters/store/test_sqlite_store.py`
Expected: AttributeError — methods not yet implemented.

- [ ] **Step 3: Append to `sqlite_store.py`**

```python
    # --- decisions ---
    def insert_decision(self, d: "PersistedDecision") -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions "
            "(scan_run_id, ticker, summary_json, decision_json, llm_model, "
            " llm_usage_json, rejected_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d.scan_run_id, d.ticker, d.summary_json, d.decision_json,
             d.llm_model, d.llm_usage_json, d.rejected_reason,
             d.created_at.isoformat()),
        )
        return int(cur.lastrowid or 0)

    def query_recent_decisions(self, ticker: str, limit: int) -> list["PersistedDecision"]:
        rows = self._conn.execute(
            "SELECT id, scan_run_id, ticker, summary_json, decision_json, "
            "llm_model, llm_usage_json, rejected_reason, created_at "
            "FROM decisions WHERE ticker=? ORDER BY created_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [
            PersistedDecision(
                id=r["id"], scan_run_id=r["scan_run_id"], ticker=r["ticker"],
                summary_json=r["summary_json"], decision_json=r["decision_json"],
                llm_model=r["llm_model"], llm_usage_json=r["llm_usage_json"],
                rejected_reason=r["rejected_reason"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # --- backtests ---
    def insert_backtest(self, b: "PersistedBacktest") -> int:
        cur = self._conn.execute(
            "INSERT INTO backtests (decision_id, report_json, passed, failed_checks, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (b.decision_id, b.report_json, 1 if b.passed else 0,
             b.failed_checks, b.created_at.isoformat()),
        )
        return int(cur.lastrowid or 0)

    # --- orders ---
    def insert_order(self, o: "PersistedOrder") -> int:
        cur = self._conn.execute(
            "INSERT INTO orders (decision_id, backtest_id, bracket_spec_json, "
            "alpaca_order_id, submitted_at, dry_run, raw_response_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (o.decision_id, o.backtest_id, o.bracket_spec_json,
             o.alpaca_order_id, o.submitted_at.isoformat(),
             1 if o.dry_run else 0, o.raw_response_json),
        )
        return int(cur.lastrowid or 0)

    # --- notifications ---
    def insert_notification(self, n: "PersistedNotification") -> int:
        cur = self._conn.execute(
            "INSERT INTO notifications (event_kind, payload_json, sent_at, sent_ok, retry_count, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (n.event_kind, n.payload_json,
             n.sent_at.isoformat() if n.sent_at else None,
             1 if n.sent_ok else 0, n.retry_count, n.error),
        )
        return int(cur.lastrowid or 0)

    def update_notification(self, n: "PersistedNotification") -> None:
        assert n.id is not None
        self._conn.execute(
            "UPDATE notifications SET sent_at=?, sent_ok=?, retry_count=?, error=? WHERE id=?",
            (n.sent_at.isoformat() if n.sent_at else None,
             1 if n.sent_ok else 0, n.retry_count, n.error, n.id),
        )

    def query_pending_notifications(self) -> list["PersistedNotification"]:
        rows = self._conn.execute(
            "SELECT id, event_kind, payload_json, sent_at, sent_ok, retry_count, error "
            "FROM notifications WHERE sent_ok=0 ORDER BY id ASC"
        ).fetchall()
        return [
            PersistedNotification(
                id=r["id"], event_kind=r["event_kind"], payload_json=r["payload_json"],
                sent_at=datetime.fromisoformat(r["sent_at"]) if r["sent_at"] else None,
                sent_ok=bool(r["sent_ok"]), retry_count=r["retry_count"], error=r["error"],
            )
            for r in rows
        ]
```

Add at top of file:
```python
from datetime import datetime

from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedNotification,
    PersistedOrder,
)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `make test tests/unit/adapters/store/test_sqlite_store.py`
Expected: all PASS (previous 2 + new 6).

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/store/sqlite_store.py tests/unit/adapters/store/test_sqlite_store.py
git commit -m "feat(store): decisions/backtests/orders/notifications CRUD"
```

### Task 2.4: SqliteStore — positions + open lifecycle queries

- [ ] **Step 1: Append failing tests**

```python
from quanterback.domain.persisted import PersistedPosition
from quanterback.domain.position import OpenLifecycle


def _seeded_order(store: SqliteStore) -> int:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    return store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))


def test_upsert_position_inserts_when_new(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    assert pid >= 1


def test_unique_active_position_constraint(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    with pytest.raises(Exception):
        store.upsert_position(PersistedPosition(
            ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
        ))


def test_closed_position_does_not_block_new(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    store.upsert_position(PersistedPosition(
        id=pid, ticker="AAPL", order_id=oid, state="closed",
        opened_at=_now(), closed_at=_now(), exit_reason="manual",
    ))
    new_pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    assert new_pid > pid


def test_query_open_lifecycles_filters_closed(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="bracket_active", opened_at=_now(),
    ))
    open_list = store.query_open_lifecycles()
    tickers = {lc.ticker for lc in open_list}
    assert "AAPL" in tickers
    assert all(lc.state != "closed" for lc in open_list)
```

- [ ] **Step 2: Run, verify fail**

Run: `make test`
Expected: AttributeError — `upsert_position` and `query_open_lifecycles` missing.

- [ ] **Step 3: Append to `sqlite_store.py`**

```python
    # --- positions ---
    def upsert_position(self, p: "PersistedPosition") -> int:
        if p.id is None:
            cur = self._conn.execute(
                "INSERT INTO positions (ticker, order_id, state, entry_price, sl, tp, qty, "
                "opened_at, closed_at, exit_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.ticker, p.order_id, p.state, p.entry_price, p.sl, p.tp, p.qty,
                 p.opened_at.isoformat(),
                 p.closed_at.isoformat() if p.closed_at else None,
                 p.exit_reason),
            )
            return int(cur.lastrowid or 0)
        else:
            self._conn.execute(
                "UPDATE positions SET ticker=?, order_id=?, state=?, entry_price=?, "
                "sl=?, tp=?, qty=?, opened_at=?, closed_at=?, exit_reason=? WHERE id=?",
                (p.ticker, p.order_id, p.state, p.entry_price, p.sl, p.tp, p.qty,
                 p.opened_at.isoformat(),
                 p.closed_at.isoformat() if p.closed_at else None,
                 p.exit_reason, p.id),
            )
            return int(p.id)

    def query_open_lifecycles(self) -> list[OpenLifecycle]:
        rows = self._conn.execute(
            "SELECT ticker, order_id, state, opened_at FROM positions WHERE state != 'closed'"
        ).fetchall()
        return [
            OpenLifecycle(
                ticker=r["ticker"], order_id=str(r["order_id"]),
                state=r["state"], opened_at=datetime.fromisoformat(r["opened_at"]),
            )
            for r in rows
        ]
```

Add to top imports:
```python
from quanterback.domain.persisted import PersistedPosition
from quanterback.domain.position import OpenLifecycle
```

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/store/sqlite_store.py tests/unit/adapters/store/test_sqlite_store.py
git commit -m "feat(store): positions upsert and open-lifecycle query with unique-active constraint"
```

**Phase 2 milestone reached.** `SqliteStore` implements the full `StateStore` Protocol with tested CRUD on a temp DB.

---

## Phase 3 — Data Layer (DataProvider + Summarizer)

**Goal:** A working `YFinanceProvider` with Parquet cache, and a `RuleBasedSummarizer` that produces deterministic `CondensedSummary` from a `PriceWindow`.

**Exit milestone:** Given a fixture OHLCV CSV, `RuleBasedSummarizer` produces an expected `CondensedSummary`; `YFinanceProvider` reads from cache on second call.

### Task 3.1: YFinanceProvider with Parquet cache

**Files:**
- Create: `src/quanterback/adapters/data/__init__.py`
- Create: `src/quanterback/adapters/data/yfinance_provider.py`
- Create: `tests/unit/adapters/data/__init__.py`
- Create: `tests/unit/adapters/data/test_yfinance_provider.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/yfinance_stub.py`

- [ ] **Step 1: Write fake yfinance backend**

`tests/fakes/__init__.py` (empty)

`tests/fakes/yfinance_stub.py`:
```python
"""Stub of `yfinance.Ticker.history` used to make YFinanceProvider testable."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd


def make_daily_df(days: int = 260, start_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=days, freq="D")
    closes = [start_price + i * 0.5 for i in range(days)]
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 1000 for i in range(days)],
    }, index=idx)


def make_hourly_df(hours: int = 30 * 7) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=hours, freq="h")
    closes = [100.0 + i * 0.1 for i in range(hours)]
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes],
        "Close": closes,
        "Volume": [50_000 for _ in range(hours)],
    }, index=idx)


class StubTicker:
    def __init__(self, daily: pd.DataFrame, hourly: pd.DataFrame) -> None:
        self._daily = daily
        self._hourly = hourly

    def history(self, period: str | None = None, interval: str = "1d", **kw) -> pd.DataFrame:
        if interval == "1d":
            return self._daily
        return self._hourly
```

- [ ] **Step 2: Write failing test**

`tests/unit/adapters/data/__init__.py` (empty)

`tests/unit/adapters/data/test_yfinance_provider.py`:
```python
from __future__ import annotations

from pathlib import Path

import pandas as pd
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
```

- [ ] **Step 3: Run, verify fail**

Run: `make test tests/unit/adapters/data/`
Expected: ModuleNotFoundError.

- [ ] **Step 4: Write `src/quanterback/adapters/data/__init__.py`** (empty)

- [ ] **Step 5: Write `src/quanterback/adapters/data/yfinance_provider.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from quanterback.domain.market import PriceWindow


class YFinanceProvider:
    """DataProvider adapter over yfinance with on-disk Parquet cache."""

    def __init__(self, cache_dir: Path, cache_ttl_hours: int = 4) -> None:
        self._cache = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(hours=cache_ttl_hours)

    def fetch(self, ticker: str) -> PriceWindow:
        ticker = ticker.upper()
        now = datetime.now(tz=timezone.utc)

        daily = self._read_cache(ticker, "daily", now)
        hourly = self._read_cache(ticker, "hourly", now)

        if daily is None or hourly is None:
            t = yf.Ticker(ticker)
            if daily is None:
                daily = self._normalize(t.history(period="1y", interval="1d"))
                self._write_cache(ticker, "daily", daily, now)
            if hourly is None:
                hourly = self._normalize(t.history(period="30d", interval="1h"))
                self._write_cache(ticker, "hourly", hourly, now)

        return PriceWindow(ticker=ticker, daily=daily, hourly=hourly, as_of=now)

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        out = df.rename(columns={c: c.lower() for c in df.columns})
        return out[["open", "high", "low", "close", "volume"]].copy()

    def _cache_path(self, ticker: str, kind: str, now: datetime) -> Path:
        day = now.strftime("%Y%m%d")
        return self._cache / f"{ticker}_{kind}_{day}.parquet"

    def _read_cache(self, ticker: str, kind: str, now: datetime) -> pd.DataFrame | None:
        path = self._cache_path(ticker, kind, now)
        if not path.exists():
            return None
        age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if age > self._ttl:
            return None
        return pd.read_parquet(path)

    def _write_cache(self, ticker: str, kind: str, df: pd.DataFrame, now: datetime) -> None:
        df.to_parquet(self._cache_path(ticker, kind, now))
```

- [ ] **Step 6: Run tests, verify pass**

Run: `make test tests/unit/adapters/data/`
Expected: 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/adapters/data/ tests/unit/adapters/data/ tests/fakes/
git commit -m "feat(data): YFinanceProvider with Parquet cache and TTL"
```

### Task 3.2: RuleBasedSummarizer — indicator math

**Files:**
- Create: `src/quanterback/adapters/data/rule_based_summarizer.py`
- Create: `src/quanterback/adapters/data/indicators.py`
- Create: `tests/unit/adapters/data/test_indicators.py`
- Create: `tests/unit/adapters/data/test_rule_based_summarizer.py`

- [ ] **Step 1: Write failing test for indicators (synthetic input)**

`tests/unit/adapters/data/test_indicators.py`:
```python
from __future__ import annotations

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import (
    atr_wilder,
    macd_recent_cross,
    realized_vol_annualized,
    rsi_wilder,
    simple_moving_average,
)


def _flat_then_up_close() -> pd.Series:
    # 30 days flat at 100, then 20 days linearly to 120
    flat = [100.0] * 30
    up = [100.0 + i for i in range(1, 21)]
    return pd.Series(flat + up)


def test_sma_window_matches_manual() -> None:
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert simple_moving_average(s, 3).iloc[-1] == 4.0


def test_rsi_full_uptrend_is_100() -> None:
    s = pd.Series(range(1, 31), dtype=float)
    rsi = rsi_wilder(s, period=14)
    assert rsi.iloc[-1] > 95


def test_rsi_full_downtrend_is_0() -> None:
    s = pd.Series(range(30, 0, -1), dtype=float)
    rsi = rsi_wilder(s, period=14)
    assert rsi.iloc[-1] < 5


def test_atr_nonnegative() -> None:
    df = pd.DataFrame({
        "high":  [10, 11, 12, 11, 13],
        "low":   [8,  9,  9,  10, 11],
        "close": [9,  10, 11, 10, 12],
    }, dtype=float)
    atr = atr_wilder(df, period=3)
    assert (atr.dropna() > 0).all()


def test_realized_vol_zero_for_flat() -> None:
    s = pd.Series([100.0] * 50)
    assert realized_vol_annualized(s, 20) == 0.0


def test_macd_bullish_cross_detected() -> None:
    s = _flat_then_up_close()
    assert macd_recent_cross(s, window=5) == "bullish_cross"


def test_macd_no_cross_on_flat() -> None:
    s = pd.Series([100.0] * 60)
    assert macd_recent_cross(s, window=5) == "none"
```

- [ ] **Step 2: Run, verify fail**

Run: `make test tests/unit/adapters/data/test_indicators.py`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/data/indicators.py`**

```python
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def simple_moving_average(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window, min_periods=window).mean()


def rsi_wilder(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100.0).clip(0, 100)


def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def realized_vol_annualized(closes: pd.Series, window: int = 20) -> float:
    returns = closes.pct_change().dropna()
    if len(returns) < window:
        window = len(returns)
    if window <= 1:
        return 0.0
    return float(returns.tail(window).std() * np.sqrt(252))


def macd_recent_cross(
    closes: pd.Series, window: int = 5,
) -> Literal["bullish_cross", "bearish_cross", "none"]:
    fast = closes.ewm(span=12, adjust=False).mean()
    slow = closes.ewm(span=26, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False).mean()
    diff = macd - signal
    if len(diff) < window + 1:
        return "none"
    recent = diff.tail(window + 1).to_list()
    for i in range(1, len(recent)):
        if recent[i - 1] <= 0 < recent[i]:
            return "bullish_cross"
        if recent[i - 1] >= 0 > recent[i]:
            return "bearish_cross"
    return "none"
```

- [ ] **Step 4: Run, verify pass**

Run: `make test tests/unit/adapters/data/test_indicators.py`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/data/indicators.py tests/unit/adapters/data/test_indicators.py
git commit -m "feat(data): technical indicators (SMA, RSI, ATR, realized vol, MACD cross)"
```

### Task 3.3: RuleBasedSummarizer — full pipeline

- [ ] **Step 1: Write failing test**

`tests/unit/adapters/data/test_rule_based_summarizer.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.domain.market import PriceWindow, TrendRegime, VolatilityRegime


def _uptrending_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc), periods=300, freq="D")
    closes = [100.0 + i * 0.3 for i in range(300)]
    daily = pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000 + i * 1_000 for i in range(300)],
    }, index=idx)
    hourly = daily.iloc[-30:].copy()
    return PriceWindow(ticker="AAPL", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def test_summarize_uptrend() -> None:
    s = RuleBasedSummarizer().summarize(_uptrending_window())
    assert s.ticker == "AAPL"
    assert s.trend_regime == TrendRegime.UPTREND
    assert s.moving_averages.alignment == "bullish"
    assert s.technicals.rsi_14 > 50
    assert s.volatility.regime in (VolatilityRegime.LOW, VolatilityRegime.NORMAL)


def test_summarize_returns_finite_values() -> None:
    s = RuleBasedSummarizer().summarize(_uptrending_window())
    text = s.to_prompt_text()
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()
```

- [ ] **Step 2: Run, verify fail**

Run: `make test tests/unit/adapters/data/test_rule_based_summarizer.py`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/data/rule_based_summarizer.py`**

```python
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import (
    atr_wilder,
    macd_recent_cross,
    realized_vol_annualized,
    rsi_wilder,
    simple_moving_average,
)
from quanterback.domain.market import (
    CondensedSummary,
    FundamentalLite,
    MovingAverages,
    PriceSnapshot,
    PriceWindow,
    TechnicalIndicators,
    TrendRegime,
    VolatilityProfile,
    VolatilityRegime,
    VolumeProfile,
    VolumeRegime,
)


class RuleBasedSummarizer:
    """Deterministic CondensedSummary builder from PriceWindow.

    All indicator math is in `indicators.py`; this module orchestrates the
    pipeline and classifies regimes via fixed thresholds.
    """

    def summarize(self, w: PriceWindow) -> CondensedSummary:
        daily = w.daily
        closes = daily["close"]

        price = self._price_snapshot(closes)
        ma = self._moving_averages(closes)
        vol = self._vol_profile(daily)
        volprof = self._volume_profile(daily)
        tech = self._technicals(closes)
        funda = FundamentalLite(days_to_next_earnings=None, market_cap_bucket="unknown")
        trend = self._trend_regime(ma)

        return CondensedSummary(
            ticker=w.ticker, as_of=w.as_of, price=price, moving_averages=ma,
            volatility=vol, volume=volprof, technicals=tech, fundamentals=funda,
            trend_regime=trend,
        )

    # --- pieces ---

    def _price_snapshot(self, closes: pd.Series) -> PriceSnapshot:
        last = float(closes.iloc[-1])
        def _ret(n: int) -> float:
            if len(closes) <= n:
                return 0.0
            return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1)
        win = closes.tail(252) if len(closes) >= 252 else closes
        hi = float(win.max())
        lo = float(win.min())
        return PriceSnapshot(
            last_close=last, return_1d=_ret(1), return_5d=_ret(5),
            return_20d=_ret(20), return_60d=_ret(60),
            pct_from_52w_high=(last / hi - 1) if hi > 0 else 0.0,
            pct_from_52w_low=(last / lo - 1) if lo > 0 else 0.0,
        )

    def _moving_averages(self, closes: pd.Series) -> MovingAverages:
        sma20 = float(simple_moving_average(closes, 20).iloc[-1])
        sma50 = float(simple_moving_average(closes, 50).iloc[-1])
        sma200 = float(simple_moving_average(closes, 200).iloc[-1])
        last = float(closes.iloc[-1])
        alignment: Literal["bullish", "bearish", "mixed"]
        if sma20 > sma50 > sma200:
            alignment = "bullish"
        elif sma20 < sma50 < sma200:
            alignment = "bearish"
        else:
            alignment = "mixed"
        return MovingAverages(
            sma_20=sma20, sma_50=sma50, sma_200=sma200,
            pct_above_sma_20=last / sma20 - 1, pct_above_sma_50=last / sma50 - 1,
            pct_above_sma_200=last / sma200 - 1, alignment=alignment,
        )

    def _vol_profile(self, daily: pd.DataFrame) -> VolatilityProfile:
        closes = daily["close"]
        rv = realized_vol_annualized(closes, 20)
        atr = float(atr_wilder(daily, 14).iloc[-1])
        atr_pct = atr / float(closes.iloc[-1]) if float(closes.iloc[-1]) > 0 else 0.0
        if rv < 0.15:
            regime = VolatilityRegime.LOW
        elif rv < 0.30:
            regime = VolatilityRegime.NORMAL
        elif rv < 0.60:
            regime = VolatilityRegime.HIGH
        else:
            regime = VolatilityRegime.EXTREME
        return VolatilityProfile(
            realized_vol_20d_annualized=rv, atr_14=atr,
            atr_pct_of_price=atr_pct, regime=regime,
        )

    def _volume_profile(self, daily: pd.DataFrame) -> VolumeProfile:
        vol = daily["volume"]
        last = int(vol.iloc[-1])
        avg20 = float(vol.tail(20).mean())
        ratio = last / avg20 if avg20 > 0 else 0.0
        if ratio < 0.7:
            regime = VolumeRegime.BELOW_AVG
        elif ratio < 1.3:
            regime = VolumeRegime.NORMAL
        elif ratio < 2.0:
            regime = VolumeRegime.ELEVATED
        else:
            regime = VolumeRegime.EXTREME
        return VolumeProfile(
            last_volume=last, avg_volume_20d=int(avg20),
            volume_ratio=ratio, regime=regime,
        )

    def _technicals(self, closes: pd.Series) -> TechnicalIndicators:
        return TechnicalIndicators(
            rsi_14=float(rsi_wilder(closes, 14).iloc[-1]),
            macd_signal=macd_recent_cross(closes, window=5),
        )

    def _trend_regime(self, ma: MovingAverages) -> TrendRegime:
        if ma.alignment == "bullish" and ma.pct_above_sma_50 > 0.02:
            return TrendRegime.UPTREND
        if ma.alignment == "bearish" and ma.pct_above_sma_50 < -0.02:
            return TrendRegime.DOWNTREND
        return TrendRegime.SIDEWAYS
```

- [ ] **Step 4: Run, verify pass**

Run: `make test tests/unit/adapters/data/`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/data/rule_based_summarizer.py tests/unit/adapters/data/test_rule_based_summarizer.py
git commit -m "feat(data): RuleBasedSummarizer producing CondensedSummary from PriceWindow"
```

**Phase 3 milestone reached.** Data layer is functional end-to-end with cache and deterministic summarization.

---

## Phase 4 — Decision Layer (LLMClient + Strategist + ApprovalGate)

**Goal:** Concrete `ClaudeClient` over Anthropic SDK with structured output; `PromptedLLMStrategist` that turns a `CondensedSummary` into a `StrategyDecision`; `NoOpApprovalGate` as the v0 ApprovalGate.

**Exit milestone:** Strategist test with a `FakeLLMClient` produces a `StrategyDecision` end-to-end; the prompt template renders without errors.

### Task 4.1: Prompt template + JSON schema constants

**Files:**
- Create: `config/prompts/momentum_strategist.md`
- Create: `src/quanterback/adapters/decision/__init__.py`
- Create: `src/quanterback/adapters/decision/prompt.py`
- Create: `tests/unit/adapters/decision/__init__.py`
- Create: `tests/unit/adapters/decision/test_prompt.py`

- [ ] **Step 1: Write `config/prompts/momentum_strategist.md`**

```markdown
You are a disciplined momentum-strategy advisor. You DO NOT predict prices.
You judge whether the current technical setup of a single US equity warrants
opening a long position using a Momentum strategy.

You receive one ticker's `CondensedSummary`. You output ONLY a JSON object
matching the provided schema. No prose, no markdown, no commentary outside
the JSON.

Decision rules — apply in this order:

1. If `volatility.regime` is `extreme` OR `fundamentals.days_to_next_earnings`
   is non-null and < 7 → action MUST be `PASS`.
2. If `trend_regime` is `downtrend` OR `moving_averages.alignment` is
   `bearish` → action MUST be `PASS`.
3. If `technicals.rsi_14` > 75 (already overbought) → action SHOULD be `PASS`
   unless other signals are exceptionally strong.
4. Otherwise: consider `BUY` if you see at least two of these confirming:
   - `trend_regime` = `uptrend`
   - `moving_averages.alignment` = `bullish` with `pct_above_sma_50` between
     +1% and +12% (not too extended)
   - `technicals.macd_signal` = `bullish_cross`
   - `volume.regime` in (`elevated`, `extreme`)

If `action = BUY`, you MUST set `params`:
- `lookback_days` in [5, 60] — pick the window that best matches the
  trend strength you observed
- `momentum_threshold` in [0.0, 0.30] — the cumulative return over the
  lookback you would require this name to deliver historically

Always include `rationale` (20-600 chars) referencing concrete fields from
the input. Always include `confidence` in [0, 1].
```

- [ ] **Step 2: Write failing test for prompt rendering**

`tests/unit/adapters/decision/__init__.py` (empty)

`tests/unit/adapters/decision/test_prompt.py`:
```python
from __future__ import annotations

from pathlib import Path

from quanterback.adapters.decision.prompt import (
    DECISION_RESPONSE_SCHEMA,
    render_prompt,
)


def test_render_prompt_inlines_summary_text(tmp_path: Path) -> None:
    tpl = tmp_path / "tpl.md"
    tpl.write_text("SYSTEM\n--SUMMARY--\nEND")
    summary_text = "[AAPL] price 100"
    out = render_prompt(tpl, summary_text)
    assert "SYSTEM" in out
    assert summary_text in out


def test_response_schema_has_required_fields() -> None:
    s = DECISION_RESPONSE_SCHEMA
    assert "action" in s["properties"]
    assert s["required"] == ["action", "ticker", "strategy", "rationale", "confidence"]
```

- [ ] **Step 3: Run, verify fail**

Run: `make test tests/unit/adapters/decision/test_prompt.py`
Expected: ModuleNotFoundError.

- [ ] **Step 4: Write `src/quanterback/adapters/decision/__init__.py`** (empty)

- [ ] **Step 5: Write `src/quanterback/adapters/decision/prompt.py`**

```python
from __future__ import annotations

from pathlib import Path


DECISION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "PASS"]},
        "ticker": {"type": "string"},
        "strategy": {"type": "string", "enum": ["MOMENTUM"]},
        "params": {
            "type": ["object", "null"],
            "properties": {
                "lookback_days": {"type": "integer", "minimum": 5, "maximum": 60},
                "momentum_threshold": {"type": "number", "minimum": 0.0, "maximum": 0.30},
            },
            "required": ["lookback_days", "momentum_threshold"],
            "additionalProperties": False,
        },
        "rationale": {"type": "string", "minLength": 20, "maxLength": 600},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["action", "ticker", "strategy", "rationale", "confidence"],
    "additionalProperties": False,
}


def render_prompt(template_path: Path, summary_text: str) -> str:
    template = template_path.read_text()
    if "--SUMMARY--" in template:
        return template.replace("--SUMMARY--", summary_text)
    return template + "\n\n" + summary_text
```

- [ ] **Step 6: Run, verify pass**

Run: `make test tests/unit/adapters/decision/test_prompt.py`
Expected: 2 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add config/prompts/ src/quanterback/adapters/decision/ tests/unit/adapters/decision/
git commit -m "feat(decision): prompt template and JSON response schema"
```

### Task 4.2: FakeLLMClient + ClaudeClient

**Files:**
- Create: `tests/fakes/llm_client.py`
- Create: `src/quanterback/adapters/decision/claude_client.py`
- Create: `tests/unit/adapters/decision/test_claude_client.py`

- [ ] **Step 1: Write the fake (for downstream tests to use)**

`tests/fakes/llm_client.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from quanterback.interfaces.decision import ChatMessage, ChatResponse


@dataclass
class FakeLLMClient:
    """Returns canned JSON responses. Records the last input."""
    canned_content: str
    last_messages: list[ChatMessage] | None = None
    last_schema: dict | None = None

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.last_messages = messages
        self.last_schema = response_schema
        return ChatResponse(
            content=self.canned_content,
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
```

- [ ] **Step 2: Write failing test for ClaudeClient**

`tests/unit/adapters/decision/test_claude_client.py`:
```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from quanterback.adapters.decision.claude_client import ClaudeClient
from quanterback.interfaces.decision import ChatMessage


def test_claude_client_calls_sdk_with_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Resp:
                content = [type("B", (), {"text": '{"ok":true}'})]
                usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})
                model = "claude-sonnet-4-6"
            return Resp()

    class FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "quanterback.adapters.decision.claude_client.Anthropic",
        FakeAnthropic,
    )

    client = ClaudeClient(api_key="sk", model="claude-sonnet-4-6")
    msgs = [
        ChatMessage(role="system", content="be precise"),
        ChatMessage(role="user", content="data here"),
    ]
    resp = client.chat(msgs, response_schema={"foo": "bar"}, temperature=0.0)
    assert resp.content == '{"ok":true}'
    assert resp.usage["input_tokens"] == 10
    assert captured["model"] == "claude-sonnet-4-6"
    assert any("system" in str(captured.get("system", "")) for _ in [0])
    assert captured["temperature"] == 0.0
```

- [ ] **Step 3: Run, verify fail**

Run: `make test tests/unit/adapters/decision/test_claude_client.py`
Expected: ModuleNotFoundError.

- [ ] **Step 4: Write `src/quanterback/adapters/decision/claude_client.py`**

```python
from __future__ import annotations

from anthropic import Anthropic

from quanterback.interfaces.decision import ChatMessage, ChatResponse


class ClaudeClient:
    """LLMClient adapter over the Anthropic Python SDK."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 1024) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": temperature,
            "system": "\n\n".join(system_parts) if system_parts else "",
            "messages": chat_messages,
        }
        if response_schema is not None:
            kwargs["extra_body"] = {"response_format": {
                "type": "json_schema",
                "schema": response_schema,
            }}
        resp = self._client.messages.create(**kwargs)
        text = "".join(getattr(block, "text", "") for block in resp.content)
        return ChatResponse(
            content=text,
            model=getattr(resp, "model", self._model),
            usage={
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            },
        )
```

- [ ] **Step 5: Run, verify pass**

Run: `make test tests/unit/adapters/decision/test_claude_client.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/fakes/llm_client.py src/quanterback/adapters/decision/claude_client.py tests/unit/adapters/decision/test_claude_client.py
git commit -m "feat(decision): ClaudeClient and FakeLLMClient"
```

### Task 4.3: PromptedLLMStrategist

**Files:**
- Create: `src/quanterback/adapters/decision/prompted_strategist.py`
- Create: `tests/unit/adapters/decision/test_prompted_strategist.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.decision.prompted_strategist import PromptedLLMStrategist
from quanterback.domain.market import (
    CondensedSummary, FundamentalLite, MovingAverages, PriceSnapshot,
    TechnicalIndicators, TrendRegime, VolatilityProfile, VolatilityRegime,
    VolumeProfile, VolumeRegime,
)
from tests.fakes.llm_client import FakeLLMClient


def _summary() -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL", as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(last_close=185.42, return_1d=0.008, return_5d=0.032,
                            return_20d=0.085, return_60d=-0.021,
                            pct_from_52w_high=-0.042, pct_from_52w_low=0.351),
        moving_averages=MovingAverages(
            sma_20=181.6, sma_50=177.7, sma_200=164.4,
            pct_above_sma_20=0.021, pct_above_sma_50=0.043, pct_above_sma_200=0.128,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(realized_vol_20d_annualized=0.22, atr_14=3.40,
                                     atr_pct_of_price=0.0183,
                                     regime=VolatilityRegime.NORMAL),
        volume=VolumeProfile(last_volume=80_000_000, avg_volume_20d=50_000_000,
                              volume_ratio=1.6, regime=VolumeRegime.ELEVATED),
        technicals=TechnicalIndicators(rsi_14=58.3, macd_signal="bullish_cross"),
        fundamentals=FundamentalLite(days_to_next_earnings=38, market_cap_bucket="large"),
        trend_regime=TrendRegime.UPTREND,
    )


@pytest.fixture()
def tpl(tmp_path: Path) -> Path:
    p = tmp_path / "tpl.md"
    p.write_text("SYSTEM PROMPT\n--SUMMARY--")
    return p


def test_strategist_parses_buy_response(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"BUY","ticker":"AAPL","strategy":"MOMENTUM",'
        '"params":{"lookback_days":20,"momentum_threshold":0.05},'
        '"rationale":"bullish alignment with elevated volume confirms momentum",'
        '"confidence":0.7}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    out = s.decide(_summary())
    assert out.action == "BUY"
    assert out.params is not None
    assert out.params.lookback_days == 20


def test_strategist_parses_pass_response(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"PASS","ticker":"AAPL","strategy":"MOMENTUM","params":null,'
        '"rationale":"already extended above SMA200 by more than 12 percent",'
        '"confidence":0.4}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    out = s.decide(_summary())
    assert out.action == "PASS"
    assert out.params is None


def test_strategist_includes_summary_in_prompt(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content=(
        '{"action":"PASS","ticker":"AAPL","strategy":"MOMENTUM","params":null,'
        '"rationale":"too extended above moving averages, wait for pullback",'
        '"confidence":0.4}'
    ))
    s = PromptedLLMStrategist(fake, tpl)
    s.decide(_summary())
    assert fake.last_messages is not None
    user_msg = next(m for m in fake.last_messages if m.role == "user")
    assert "AAPL" in user_msg.content
    assert fake.last_schema is not None


def test_strategist_raises_on_invalid_json(tpl: Path) -> None:
    fake = FakeLLMClient(canned_content="not json at all")
    s = PromptedLLMStrategist(fake, tpl)
    with pytest.raises(ValueError, match="LLM output"):
        s.decide(_summary())
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/decision/prompted_strategist.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from quanterback.adapters.decision.prompt import DECISION_RESPONSE_SCHEMA, render_prompt
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.interfaces.decision import ChatMessage, LLMClient


class PromptedLLMStrategist:
    """LLMStrategist that uses an LLMClient with a markdown prompt template."""

    def __init__(self, client: LLMClient, prompt_template_path: Path,
                 *, temperature: float = 0.0) -> None:
        self._client = client
        self._template_path = prompt_template_path
        self._temperature = temperature

    def decide(self, summary: CondensedSummary) -> StrategyDecision:
        summary_text = summary.to_prompt_text()
        system_text = render_prompt(self._template_path, "")
        user_text = (
            "Here is the CondensedSummary for the ticker. Respond with ONLY a JSON "
            "object matching the schema. Do not include any text outside the JSON.\n\n"
            f"{summary_text}"
        )

        messages = [
            ChatMessage(role="system", content=system_text),
            ChatMessage(role="user", content=user_text),
        ]
        resp = self._client.chat(
            messages,
            response_schema=DECISION_RESPONSE_SCHEMA,
            temperature=self._temperature,
        )
        try:
            data = json.loads(resp.content)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM output is not valid JSON: {e}") from e
        try:
            return StrategyDecision.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"LLM output failed schema validation: {e}") from e
```

- [ ] **Step 4: Run, verify pass**

Run: `make test`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/decision/prompted_strategist.py tests/unit/adapters/decision/test_prompted_strategist.py
git commit -m "feat(decision): PromptedLLMStrategist with JSON-schema-enforced output"
```

### Task 4.4: NoOpApprovalGate

**Files:**
- Create: `src/quanterback/adapters/decision/noop_approval_gate.py`
- Create: `tests/unit/adapters/decision/test_noop_approval_gate.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.domain.decision import MomentumParams, StrategyDecision


def test_noop_gate_always_approves() -> None:
    gate = NoOpApprovalGate()
    decision = StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish setup with confirming volume profile signal",
        confidence=0.7,
    )
    result = gate.review(decision)
    assert result.approved
    assert result.reason == "noop"
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/decision/noop_approval_gate.py`**

```python
from __future__ import annotations

from quanterback.domain.decision import StrategyDecision
from quanterback.interfaces.decision import ApprovalResult


class NoOpApprovalGate:
    """v0 ApprovalGate that always approves. v1 replaces with TelegramApprovalGate."""

    def review(self, decision: StrategyDecision) -> ApprovalResult:
        return ApprovalResult(approved=True, reason="noop", approver=None)
```

- [ ] **Step 4: Run, verify pass**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/decision/noop_approval_gate.py tests/unit/adapters/decision/test_noop_approval_gate.py
git commit -m "feat(decision): NoOpApprovalGate (v0 placeholder)"
```

**Phase 4 milestone reached.** Decision layer: LLMClient (Claude SDK), PromptedLLMStrategist (template + schema), NoOpApprovalGate. All testable without real API calls.

---

## Phase 5 — Risk Barrier Layer

**Goal:** Implement Backtester, RiskGate, OrderBuilder, Executor, and PositionStateService — the single-direction gate sitting between LLM and Alpaca.

**Exit milestone:** An integration test with `FakeLLMClient` → real `RuleBasedSummarizer` → real `VectorizedBacktester` (against fixture historical data) → real `ThresholdRiskGate` → real `ATRBracketOrderBuilder` → `InMemorySimulatorExecutor` submits a Bracket Order successfully.

### Task 5.1: HistoricalDataProvider interface + YFinanceProvider.fetch_historical

**Files:**
- Modify: `src/quanterback/interfaces/data.py`
- Modify: `src/quanterback/adapters/data/yfinance_provider.py`
- Modify: `tests/unit/adapters/data/test_yfinance_provider.py`

- [ ] **Step 1: Append failing test**

Add to `test_yfinance_provider.py`:
```python
def test_fetch_historical_returns_dataframe(provider: YFinanceProvider) -> None:
    df = provider.fetch_historical("AAPL", years=3)
    assert "close" in df.columns
    assert len(df) >= 250
```

- [ ] **Step 2: Run, verify fail**

Run: `make test tests/unit/adapters/data/test_yfinance_provider.py::test_fetch_historical_returns_dataframe`
Expected: AttributeError.

- [ ] **Step 3: Append `HistoricalDataProvider` to `src/quanterback/interfaces/data.py`**

Append after `Summarizer`:
```python
import pandas as pd


class HistoricalDataProvider(Protocol):
    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        """Return a normalized daily OHLCV DataFrame for the past `years` years."""
        ...
```

- [ ] **Step 4: Append `fetch_historical` to `YFinanceProvider`**

```python
    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        ticker = ticker.upper()
        path = self._cache / f"{ticker}_hist_{years}y.parquet"
        now = datetime.now(tz=timezone.utc)
        if path.exists():
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if age < timedelta(days=7):
                return pd.read_parquet(path)
        t = yf.Ticker(ticker)
        df = self._normalize(t.history(period=f"{years}y", interval="1d"))
        df.to_parquet(path)
        return df
```

- [ ] **Step 5: Run tests**

Run: `make test tests/unit/adapters/data/`
Expected: all PASS including the new one.

- [ ] **Step 6: Commit**

```bash
git add src/quanterback/interfaces/data.py src/quanterback/adapters/data/yfinance_provider.py tests/unit/adapters/data/test_yfinance_provider.py
git commit -m "feat(data): HistoricalDataProvider protocol and 3y historical fetch with long-TTL cache"
```

### Task 5.2: VectorizedBacktester (Momentum strategy)

**Files:**
- Create: `src/quanterback/adapters/risk/__init__.py`
- Create: `src/quanterback/adapters/risk/vectorized_backtester.py`
- Create: `tests/unit/adapters/risk/__init__.py`
- Create: `tests/unit/adapters/risk/test_vectorized_backtester.py`
- Create: `tests/fakes/historical_data.py`

- [ ] **Step 1: Write the fake historical-data provider**

`tests/fakes/historical_data.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class FakeHistoricalDataProvider:
    df_per_ticker: dict[str, pd.DataFrame]

    def fetch_historical(self, ticker: str, years: int) -> pd.DataFrame:
        return self.df_per_ticker[ticker.upper()].copy()
```

- [ ] **Step 2: Write failing test**

`tests/unit/adapters/risk/test_vectorized_backtester.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.domain.backtest import BacktestRequest
from tests.fakes.historical_data import FakeHistoricalDataProvider


def _smooth_uptrend(days: int = 1000) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=days, freq="B")
    closes = np.linspace(100, 250, days)
    noise = np.sin(np.linspace(0, days / 5, days)) * 2.0
    closes = closes + noise
    df = pd.DataFrame({
        "open":  closes, "high":  closes * 1.01,
        "low":   closes * 0.99, "close": closes,
        "volume": np.full(days, 1_000_000),
    }, index=idx)
    return df


def _whipsaw(days: int = 1000) -> pd.DataFrame:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=days, freq="B")
    closes = 100 + np.sin(np.linspace(0, 50, days)) * 25
    df = pd.DataFrame({
        "open":  closes, "high":  closes * 1.03,
        "low":   closes * 0.97, "close": closes,
        "volume": np.full(days, 1_000_000),
    }, index=idx)
    return df


def test_backtest_uptrend_produces_positive_return() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
        lookback_years=3,
    ))
    assert r.num_trades >= 1
    assert r.cumulative_return > 0
    assert r.max_drawdown < 0.30  # not catastrophic


def test_backtest_whipsaw_low_winrate() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _whipsaw()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 5, "momentum_threshold": 0.02},
        lookback_years=3,
    ))
    # Whipsaw should produce many losing momentum trades
    assert r.win_rate < 0.65
    assert r.num_trades >= 1


def test_backtest_metrics_are_finite() -> None:
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _smooth_uptrend()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
    ))
    assert np.isfinite(r.sharpe)
    assert np.isfinite(r.profit_factor) or r.profit_factor == 0


def test_backtest_zero_trades_returns_safe_report() -> None:
    # Impossible threshold → zero entries
    bt = VectorizedBacktester(FakeHistoricalDataProvider({"AAPL": _whipsaw()}))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.29},
    ))
    assert r.num_trades >= 0  # may be zero
    if r.num_trades == 0:
        assert r.win_rate == 0
        assert r.profit_factor == 0
```

- [ ] **Step 3: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 4: Write `src/quanterback/adapters/risk/__init__.py`** (empty)

- [ ] **Step 5: Write `src/quanterback/adapters/risk/vectorized_backtester.py`**

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from quanterback.adapters.data.indicators import atr_wilder
from quanterback.domain.backtest import BacktestReport, BacktestRequest, TradeRecord
from quanterback.interfaces.data import HistoricalDataProvider


SL_ATR_MULT_BT = 2.0
TP_ATR_MULT_BT = 4.0
TIMEOUT_BARS = 60


class VectorizedBacktester:
    """Pandas + numpy momentum backtest. No look-ahead bias: enter next bar open."""

    def __init__(self, hist: HistoricalDataProvider) -> None:
        self._hist = hist

    def run(self, request: BacktestRequest) -> BacktestReport:
        if request.strategy != "MOMENTUM":
            raise ValueError(f"Unsupported strategy: {request.strategy}")
        df = self._hist.fetch_historical(request.ticker, request.lookback_years)
        lookback = int(request.params["lookback_days"])
        threshold = float(request.params["momentum_threshold"])
        trades = self._simulate(df, lookback, threshold)
        return self._build_report(request, df, trades)

    # ------- simulation -------

    @staticmethod
    def _entry_signals(closes: pd.Series, lookback: int, threshold: float) -> pd.Series:
        rolling_ret = closes / closes.shift(lookback) - 1
        cond = rolling_ret > threshold
        # Trigger only on transition False -> True
        prev = cond.shift(1).fillna(False).astype(bool)
        return (cond & ~prev).astype(bool)

    def _simulate(self, df: pd.DataFrame, lookback: int, threshold: float) -> list[TradeRecord]:
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        opens = df["open"]
        atr = atr_wilder(df, 14)
        signals = self._entry_signals(closes, lookback, threshold)

        trades: list[TradeRecord] = []
        i = lookback + 14   # warmup: need lookback for momentum + 14 for ATR
        n = len(df)
        while i < n - 1:
            if signals.iloc[i] and not np.isnan(atr.iloc[i]):
                entry_idx = i + 1
                if entry_idx >= n:
                    break
                entry_price = float(opens.iloc[entry_idx])
                atr_val = float(atr.iloc[i])
                sl = entry_price - SL_ATR_MULT_BT * atr_val
                tp = entry_price + TP_ATR_MULT_BT * atr_val
                exit_idx, exit_price, reason = self._find_exit(
                    highs, lows, closes, entry_idx, sl, tp,
                )
                trades.append(TradeRecord(
                    entry_date=_to_date(df.index[entry_idx]),
                    exit_date=_to_date(df.index[exit_idx]),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    return_pct=exit_price / entry_price - 1,
                    bars_held=exit_idx - entry_idx,
                    exit_reason=reason,
                ))
                i = exit_idx + 1
            else:
                i += 1
        return trades

    @staticmethod
    def _find_exit(
        highs: pd.Series, lows: pd.Series, closes: pd.Series,
        entry_idx: int, sl: float, tp: float,
    ) -> tuple[int, float, str]:
        n = len(highs)
        end = min(entry_idx + TIMEOUT_BARS, n - 1)
        for j in range(entry_idx, end + 1):
            if lows.iloc[j] <= sl:
                return j, sl, "stop_loss"
            if highs.iloc[j] >= tp:
                return j, tp, "take_profit"
        # timeout — exit at close on last bar in window
        return end, float(closes.iloc[end]), "timeout"

    # ------- metrics -------

    @staticmethod
    def _build_report(
        request: BacktestRequest, df: pd.DataFrame, trades: list[TradeRecord],
    ) -> BacktestReport:
        if not trades:
            return BacktestReport(
                ticker=request.ticker, strategy=request.strategy,
                params=request.params,
                period_start=_to_date(df.index[0]),
                period_end=_to_date(df.index[-1]),
                num_trades=0, win_rate=0.0, max_drawdown=0.0, sharpe=0.0,
                profit_factor=0.0, cumulative_return=0.0,
                avg_trade_return=0.0, avg_bars_held=0.0, trades=[],
            )

        returns = np.array([t.return_pct for t in trades])
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        equity = (1 + returns).cumprod()
        drawdown = 1 - equity / np.maximum.accumulate(equity)
        max_dd = float(drawdown.max()) if len(drawdown) else 0.0

        # Sharpe: per-trade Sharpe, annualized by avg bars held
        mean_ret = float(returns.mean())
        std_ret = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        avg_bars = float(np.mean([t.bars_held for t in trades])) if trades else 1.0
        trades_per_year = 252 / max(avg_bars, 1.0)
        sharpe = (
            (mean_ret / std_ret) * np.sqrt(trades_per_year)
            if std_ret > 0 else 0.0
        )

        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(-losses.sum()) if len(losses) else 0.0
        pf = gross_profit / gross_loss if gross_loss > 0 else 0.0

        return BacktestReport(
            ticker=request.ticker, strategy=request.strategy, params=request.params,
            period_start=_to_date(df.index[0]),
            period_end=_to_date(df.index[-1]),
            num_trades=len(trades),
            win_rate=float(len(wins) / len(trades)),
            max_drawdown=max(max_dd, 0.0),
            sharpe=float(sharpe),
            profit_factor=float(pf),
            cumulative_return=float(equity[-1] - 1),
            avg_trade_return=float(mean_ret),
            avg_bars_held=avg_bars,
            trades=trades,
        )


def _to_date(ts) -> date:
    return pd.Timestamp(ts).date()
```

- [ ] **Step 6: Run tests, verify pass**

Run: `make test tests/unit/adapters/risk/`
Expected: 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/adapters/risk/ tests/unit/adapters/risk/ tests/fakes/historical_data.py
git commit -m "feat(risk): VectorizedBacktester for Momentum strategy (no look-ahead, ATR-based SL/TP)"
```

### Task 5.3: ThresholdRiskGate

**Files:**
- Create: `src/quanterback/adapters/risk/threshold_risk_gate.py`
- Create: `tests/unit/adapters/risk/test_threshold_risk_gate.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import date

from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskThresholds


def _report(**kw) -> BacktestReport:
    base = dict(
        ticker="AAPL", strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=50, win_rate=0.5, max_drawdown=0.05, sharpe=1.0,
        profit_factor=1.5, cumulative_return=0.2, avg_trade_return=0.005,
        avg_bars_held=10.0, trades=[],
    )
    base.update(kw)
    return BacktestReport(**base)


def test_all_thresholds_passed() -> None:
    a = ThresholdRiskGate().evaluate(_report(), RiskThresholds())
    assert a.passed
    assert a.failed_checks == []


def test_max_drawdown_failure_named() -> None:
    a = ThresholdRiskGate().evaluate(_report(max_drawdown=0.20), RiskThresholds())
    assert not a.passed
    assert "max_drawdown" in a.failed_checks


def test_multiple_failures_listed() -> None:
    a = ThresholdRiskGate().evaluate(
        _report(max_drawdown=0.20, sharpe=0.1, win_rate=0.20),
        RiskThresholds(),
    )
    assert not a.passed
    assert set(a.failed_checks) >= {"max_drawdown", "min_sharpe", "min_win_rate"}


def test_min_num_trades_failure_named() -> None:
    a = ThresholdRiskGate().evaluate(_report(num_trades=5), RiskThresholds())
    assert not a.passed
    assert "min_num_trades" in a.failed_checks
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/risk/threshold_risk_gate.py`**

```python
from __future__ import annotations

from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class ThresholdRiskGate:
    """All checks must pass. Lists every failed check name for transparency."""

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds
    ) -> RiskAssessment:
        failed: list[str] = []
        if report.max_drawdown > thresholds.max_drawdown:
            failed.append("max_drawdown")
        if report.sharpe < thresholds.min_sharpe:
            failed.append("min_sharpe")
        if report.win_rate < thresholds.min_win_rate:
            failed.append("min_win_rate")
        if report.profit_factor < thresholds.min_profit_factor:
            failed.append("min_profit_factor")
        if report.num_trades < thresholds.min_num_trades:
            failed.append("min_num_trades")
        return RiskAssessment(passed=not failed, failed_checks=failed)
```

- [ ] **Step 4: Run, verify pass**

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/risk/threshold_risk_gate.py tests/unit/adapters/risk/test_threshold_risk_gate.py
git commit -m "feat(risk): ThresholdRiskGate enforcing all 5 thresholds with named failures"
```

### Task 5.4: ATRBracketOrderBuilder

**Files:**
- Create: `src/quanterback/adapters/risk/atr_bracket_builder.py`
- Create: `tests/unit/adapters/risk/test_atr_bracket_builder.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.domain.decision import MomentumParams, StrategyDecision
from quanterback.domain.market import (
    CondensedSummary, FundamentalLite, MovingAverages, PriceSnapshot,
    TechnicalIndicators, TrendRegime, VolatilityProfile, VolatilityRegime,
    VolumeProfile, VolumeRegime,
)


def _summary(last_close: float = 100.0, atr_14: float = 2.0) -> CondensedSummary:
    return CondensedSummary(
        ticker="AAPL", as_of=datetime(2026, 5, 22, tzinfo=timezone.utc),
        price=PriceSnapshot(
            last_close=last_close, return_1d=0.0, return_5d=0.0, return_20d=0.0,
            return_60d=0.0, pct_from_52w_high=0.0, pct_from_52w_low=0.0,
        ),
        moving_averages=MovingAverages(
            sma_20=100, sma_50=100, sma_200=100,
            pct_above_sma_20=0, pct_above_sma_50=0, pct_above_sma_200=0,
            alignment="bullish",
        ),
        volatility=VolatilityProfile(
            realized_vol_20d_annualized=0.2, atr_14=atr_14,
            atr_pct_of_price=atr_14 / last_close, regime=VolatilityRegime.NORMAL,
        ),
        volume=VolumeProfile(last_volume=1_000_000, avg_volume_20d=1_000_000,
                             volume_ratio=1.0, regime=VolumeRegime.NORMAL),
        technicals=TechnicalIndicators(rsi_14=50, macd_signal="none"),
        fundamentals=FundamentalLite(days_to_next_earnings=None, market_cap_bucket="large"),
        trend_regime=TrendRegime.UPTREND,
    )


def _decision() -> StrategyDecision:
    return StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish setup with elevated volume confirming the trend strength",
        confidence=0.7,
    )


def test_sl_tp_uses_atr_multiples() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=10_000.0)
    assert spec.stop_loss_price == 96.0     # 100 - 2*2
    assert spec.take_profit_price == 108.0  # 100 + 4*2


def test_qty_uses_position_size_pct() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=10_000.0)
    # 5% of 10000 = 500; 500 / 100 = 5
    assert spec.qty == 5


def test_qty_at_least_one() -> None:
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.005,
    )
    spec = builder.build(_decision(), _summary(last_close=100.0, atr_14=2.0),
                         account_value=1_000.0)
    assert spec.qty >= 1
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/risk/atr_bracket_builder.py`**

```python
from __future__ import annotations

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.domain.order import BracketOrderSpec


class ATRBracketOrderBuilder:
    """Builds a Bracket Order spec from a StrategyDecision using ATR-based SL/TP."""

    def __init__(
        self, *, sl_atr_multiple: float, tp_atr_multiple: float,
        position_size_pct: float,
    ) -> None:
        if sl_atr_multiple <= 0 or tp_atr_multiple <= 0:
            raise ValueError("ATR multiples must be positive")
        if not 0 < position_size_pct <= 1:
            raise ValueError("position_size_pct must be in (0, 1]")
        self._sl_m = sl_atr_multiple
        self._tp_m = tp_atr_multiple
        self._size_pct = position_size_pct

    def build(
        self,
        decision: StrategyDecision,
        summary: CondensedSummary,
        account_value: float,
    ) -> BracketOrderSpec:
        if decision.action != "BUY":
            raise ValueError("OrderBuilder called for non-BUY decision")
        entry = summary.price.last_close
        atr = summary.volatility.atr_14
        sl = max(entry - self._sl_m * atr, 0.01)
        tp = entry + self._tp_m * atr
        dollar_size = account_value * self._size_pct
        qty = max(int(dollar_size // entry), 1)
        return BracketOrderSpec(
            ticker=decision.ticker, side="buy", qty=qty,
            entry_type="market", limit_price=None,
            stop_loss_price=round(sl, 2),
            take_profit_price=round(tp, 2),
        )
```

- [ ] **Step 4: Run, verify pass**

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/risk/atr_bracket_builder.py tests/unit/adapters/risk/test_atr_bracket_builder.py
git commit -m "feat(risk): ATRBracketOrderBuilder with config-driven multiples and sizing"
```

### Task 5.5: InMemorySimulatorExecutor + AlpacaPaperExecutor

**Files:**
- Create: `src/quanterback/adapters/execution/__init__.py`
- Create: `src/quanterback/adapters/execution/alpaca_paper_executor.py`
- Create: `tests/fakes/executor.py`
- Create: `tests/unit/adapters/execution/__init__.py`
- Create: `tests/unit/adapters/execution/test_alpaca_paper_executor.py`

- [ ] **Step 1: Write the in-memory simulator (used by both tests and integration)**

`tests/fakes/executor.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


@dataclass
class InMemorySimulatorExecutor:
    """Records every spec received. Returns a synthetic order id."""

    account_value: float = 100_000.0
    submitted: list[BracketOrderSpec] = field(default_factory=list)
    next_id: int = 1
    fail_next: bool = False

    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult:
        if self.fail_next:
            self.fail_next = False
            return ExecutionResult(submitted=False, order_id=None,
                                   error="simulated failure", raw_response={})
        if dry_run:
            return ExecutionResult(submitted=False, order_id=None, error=None,
                                   raw_response={"dry_run": True})
        self.submitted.append(spec)
        oid = f"sim-{self.next_id}"
        self.next_id += 1
        return ExecutionResult(submitted=True, order_id=oid, error=None,
                               raw_response={"id": oid})

    def get_account_value(self) -> float:
        return self.account_value
```

- [ ] **Step 2: Write failing test for AlpacaPaperExecutor**

`tests/unit/adapters/execution/__init__.py` (empty)

`tests/unit/adapters/execution/test_alpaca_paper_executor.py`:
```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from quanterback.adapters.execution.alpaca_paper_executor import AlpacaPaperExecutor
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
            return SimpleNamespace(equity="125000.50")

    monkeypatch.setattr(
        "quanterback.adapters.execution.alpaca_paper_executor.TradingClient",
        FakeClient,
    )
    return calls


def _spec() -> BracketOrderSpec:
    return BracketOrderSpec(
        ticker="AAPL", side="buy", qty=10, entry_type="market",
        limit_price=None, stop_loss_price=95.0, take_profit_price=110.0,
    )


def test_submit_returns_alpaca_order_id(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=False)
    assert r.submitted
    assert r.order_id == "alpaca-1"


def test_dry_run_does_not_call_broker(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    r = ex.submit(_spec(), dry_run=True)
    assert not r.submitted
    assert r.order_id is None
    assert fake_trading_client["orders"] == []


def test_account_value_returned_as_float(fake_trading_client: dict) -> None:
    ex = AlpacaPaperExecutor(api_key="k", secret="s")
    assert ex.get_account_value() == 125_000.50
```

- [ ] **Step 3: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 4: Write `src/quanterback/adapters/execution/__init__.py`** (empty)

- [ ] **Step 5: Write `src/quanterback/adapters/execution/alpaca_paper_executor.py`**

```python
from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from quanterback.domain.order import BracketOrderSpec, ExecutionResult


class AlpacaPaperExecutor:
    """Executor adapter for Alpaca Paper Trading. Bracket orders only."""

    def __init__(self, *, api_key: str, secret: str) -> None:
        self._client = TradingClient(api_key, secret, paper=True)

    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult:
        if dry_run:
            return ExecutionResult(
                submitted=False, order_id=None, error=None,
                raw_response={"dry_run": True, "spec": spec.model_dump()},
            )
        order_request = self._build_request(spec)
        try:
            order = self._client.submit_order(order_request)
            return ExecutionResult(
                submitted=True, order_id=str(order.id), error=None,
                raw_response={"id": str(order.id),
                              "status": getattr(order, "status", "unknown")},
            )
        except Exception as e:
            return ExecutionResult(
                submitted=False, order_id=None, error=str(e), raw_response={},
            )

    def get_account_value(self) -> float:
        acct = self._client.get_account()
        return float(acct.equity)

    @staticmethod
    def _build_request(spec: BracketOrderSpec):
        tp = TakeProfitRequest(limit_price=spec.take_profit_price)
        sl = StopLossRequest(stop_price=spec.stop_loss_price)
        common = dict(
            symbol=spec.ticker, qty=spec.qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class="bracket", take_profit=tp, stop_loss=sl,
        )
        if spec.entry_type == "market":
            return MarketOrderRequest(**common)
        return LimitOrderRequest(**common, limit_price=spec.limit_price)
```

- [ ] **Step 6: Run, verify pass**

Run: `make test tests/unit/adapters/execution/`
Expected: 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quanterback/adapters/execution/ tests/fakes/executor.py tests/unit/adapters/execution/
git commit -m "feat(execution): AlpacaPaperExecutor (Bracket Order) + InMemorySimulatorExecutor fake"
```

### Task 5.6: SqliteAlpacaSyncedPositionState

**Files:**
- Create: `src/quanterback/adapters/position/__init__.py`
- Create: `src/quanterback/adapters/position/sqlite_alpaca_synced_state.py`
- Create: `tests/unit/adapters/position/__init__.py`
- Create: `tests/unit/adapters/position/test_sqlite_alpaca_synced_state.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.position.sqlite_alpaca_synced_state import (
    SqliteAlpacaSyncedPositionState,
)
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import (
    PersistedBacktest, PersistedDecision, PersistedOrder, PersistedPosition, ScanRun,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, tzinfo=timezone.utc)


def _seed_active_position(store: SqliteStore, ticker: str) -> None:
    run = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run, ticker=ticker, summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    oid = store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))
    store.upsert_position(PersistedPosition(
        ticker=ticker, order_id=oid, state="bracket_active", opened_at=_now(),
    ))


def test_has_open_lifecycle_true_after_position(store: SqliteStore) -> None:
    _seed_active_position(store, "AAPL")
    svc = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    assert svc.has_open_lifecycle("AAPL") is True
    assert svc.has_open_lifecycle("MSFT") is False


def test_get_open_returns_lifecycle(store: SqliteStore) -> None:
    _seed_active_position(store, "AAPL")
    svc = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    lc = svc.get_open("AAPL")
    assert lc is not None
    assert lc.ticker == "AAPL"
    assert lc.state == "bracket_active"
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/position/__init__.py`** (empty)

- [ ] **Step 4: Write `src/quanterback/adapters/position/sqlite_alpaca_synced_state.py`**

```python
from __future__ import annotations

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.position import OpenLifecycle


class SqliteAlpacaSyncedPositionState:
    """Position-state service backed by SQLite. Optional reconciliation with Alpaca.

    v0 reconciliation is intentionally a stub. In v1 we can pass an `Executor` and
    cross-check `client.get_all_positions()` on construction.
    """

    def __init__(self, store: SqliteStore, *, alpaca_synced: bool = False) -> None:
        self._store = store
        self._alpaca_synced = alpaca_synced

    def has_open_lifecycle(self, ticker: str) -> bool:
        return self.get_open(ticker) is not None

    def get_open(self, ticker: str) -> OpenLifecycle | None:
        ticker = ticker.upper()
        for lc in self._store.query_open_lifecycles():
            if lc.ticker == ticker:
                return lc
        return None
```

- [ ] **Step 5: Run, verify pass**

Expected: 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quanterback/adapters/position/ tests/unit/adapters/position/
git commit -m "feat(position): SqliteAlpacaSyncedPositionState querying open lifecycles"
```

**Phase 5 milestone reached.** Risk Barrier layer complete: Backtester (Momentum), RiskGate (5 thresholds), OrderBuilder (ATR + sizing), Executor (Alpaca paper + dry-run + simulator fake), PositionStateService (open-lifecycle query).

---

## Phase 6 — Cross-cutting + ScanPipeline

**Goal:** Implement WatchlistEventSource, SqliteSystemStateService, TelegramNotifier (with retry queue), and the `ScanPipeline` that orchestrates a single end-to-end scan. End with integration tests covering all 10 scenarios in spec §7.3.

**Exit milestone:** `ScanPipeline.run()` with all-fake adapters produces correct state in SQLite for every spec §7.3 scenario.

### Task 6.1: WatchlistEventSource

**Files:**
- Create: `src/quanterback/adapters/events/__init__.py`
- Create: `src/quanterback/adapters/events/watchlist_event_source.py`
- Create: `tests/unit/adapters/events/__init__.py`
- Create: `tests/unit/adapters/events/test_watchlist_event_source.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from pathlib import Path

from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource


def test_stream_yields_one_event_per_line(tmp_path: Path) -> None:
    p = tmp_path / "wl.txt"
    p.write_text("AAPL\nmsft\n# comment\n\nGOOGL\n")
    src = WatchlistEventSource(p)
    events = list(src.stream())
    assert [e.ticker for e in events] == ["AAPL", "MSFT", "GOOGL"]
    assert all(e.source == "watchlist" for e in events)


def test_missing_file_yields_empty(tmp_path: Path) -> None:
    src = WatchlistEventSource(tmp_path / "missing.txt")
    assert list(src.stream()) == []
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write the source files**

`src/quanterback/adapters/events/__init__.py` (empty)

`src/quanterback/adapters/events/watchlist_event_source.py`:
```python
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from quanterback.domain.events import ScanEvent


class WatchlistEventSource:
    """Reads tickers from a text file, one per line. `#` lines and blanks ignored."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def stream(self) -> Iterable[ScanEvent]:
        if not self._path.exists():
            return
        now = datetime.now(tz=timezone.utc)
        for line in self._path.read_text().splitlines():
            ticker = line.strip()
            if not ticker or ticker.startswith("#"):
                continue
            yield ScanEvent(ticker=ticker, source="watchlist", requested_at=now)
```

- [ ] **Step 4: Run, verify pass**

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/events/ tests/unit/adapters/events/
git commit -m "feat(events): WatchlistEventSource reading newline-separated tickers"
```

### Task 6.2: SqliteSystemStateService

**Files:**
- Create: `src/quanterback/adapters/state/__init__.py`
- Create: `src/quanterback/adapters/state/sqlite_system_state.py`
- Create: `tests/unit/adapters/state/__init__.py`
- Create: `tests/unit/adapters/state/test_sqlite_system_state.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore


@pytest.fixture()
def svc(tmp_path: Path) -> SqliteSystemStateService:
    return SqliteSystemStateService(SqliteStore(tmp_path / "t.sqlite"))


def test_default_state_is_normal(svc: SqliteSystemStateService) -> None:
    s = svc.get_current()
    assert s.mode == "normal"
    assert s.updated_by == "bootstrap"


def test_set_persists_state(svc: SqliteSystemStateService) -> None:
    svc.set("frozen", "manual freeze", "tg-user-1")
    s = svc.get_current()
    assert s.mode == "frozen"
    assert s.reason == "manual freeze"
    assert s.updated_by == "tg-user-1"


def test_set_invalid_mode_rejected(svc: SqliteSystemStateService) -> None:
    with pytest.raises(ValueError):
        svc.set("paused", "x", "actor")
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write source**

`src/quanterback/adapters/state/__init__.py` (empty)

`src/quanterback/adapters/state/sqlite_system_state.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.state import SystemState


VALID_MODES = {"normal", "frozen", "halted"}


class SqliteSystemStateService:
    """Tiny adapter using the `system_state` table. Latest row wins."""

    def __init__(self, store: SqliteStore) -> None:
        self._conn = store._conn   # tight coupling is fine within a process

    def get_current(self) -> SystemState:
        row = self._conn.execute(
            "SELECT mode, reason, actor, updated_at FROM system_state "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return SystemState(
                mode="normal", updated_at=datetime.now(tz=timezone.utc),
                updated_by="bootstrap", reason=None,
            )
        return SystemState(
            mode=row["mode"], reason=row["reason"], updated_by=row["actor"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def set(self, mode: str, reason: str, actor: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode!r}")
        self._conn.execute(
            "INSERT INTO system_state (mode, reason, actor, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (mode, reason, actor, datetime.now(tz=timezone.utc).isoformat()),
        )
```

- [ ] **Step 4: Run, verify pass**

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quanterback/adapters/state/ tests/unit/adapters/state/
git commit -m "feat(state): SqliteSystemStateService with mode validation"
```

### Task 6.3: TelegramNotifier with retry queue

**Files:**
- Create: `src/quanterback/adapters/notify/__init__.py`
- Create: `src/quanterback/adapters/notify/telegram_notifier.py`
- Create: `tests/fakes/notifier.py`
- Create: `tests/unit/adapters/notify/__init__.py`
- Create: `tests/unit/adapters/notify/test_telegram_notifier.py`

- [ ] **Step 1: Write the fake (for pipeline tests later)**

`tests/fakes/notifier.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.domain.events import NotificationEvent


@dataclass
class FakeNotifier:
    pushed: list[NotificationEvent] = field(default_factory=list)

    def push(self, event: NotificationEvent) -> None:
        self.pushed.append(event)
```

- [ ] **Step 2: Write failing test for TelegramNotifier**

`tests/unit/adapters/notify/test_telegram_notifier.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.notify.telegram_notifier import TelegramNotifier
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _ev() -> NotificationEvent:
    return NotificationEvent(
        kind="decision",
        payload={"ticker": "AAPL", "action": "BUY"},
        timestamp=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )


def test_push_calls_telegram_api(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    def fake_post(url: str, json: dict, timeout: float) -> object:
        calls.append((url, json))
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post",
                         fake_post)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())
    assert len(calls) == 1
    assert "/bot t/" in calls[0][0]


def test_push_failure_does_not_raise(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("network down")
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", boom)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())  # MUST NOT raise


def test_failure_increments_retry_count(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("network down")
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", boom)
    n = TelegramNotifier(token="t", chat_ids=("123",), store=store)
    n.push(_ev())
    pending = store.query_pending_notifications()
    assert len(pending) == 1
    assert pending[0].retry_count == 1


def test_fan_out_to_multiple_chat_ids(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    def fake_post(url: str, json: dict, timeout: float) -> object:
        calls.append(json["chat_id"])
        class R:
            status_code = 200
            text = "ok"
        return R()
    monkeypatch.setattr("quanterback.adapters.notify.telegram_notifier.requests.post", fake_post)
    n = TelegramNotifier(token="t", chat_ids=("a", "b", "c"), store=store)
    n.push(_ev())
    assert calls == ["a", "b", "c"]
```

- [ ] **Step 3: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 4: Add `requests>=2.31` to pyproject.toml dependencies and rebuild**

Append to `pyproject.toml [project] dependencies`:
```
    "requests>=2.31",
```
Then run `make build` so the new dependency is installed.

- [ ] **Step 5: Write source**

`src/quanterback/adapters/notify/__init__.py` (empty)

`src/quanterback/adapters/notify/telegram_notifier.py`:
```python
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.events import NotificationEvent
from quanterback.domain.persisted import PersistedNotification

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Fire-and-forget Telegram notifier. Persists every event for retry."""

    def __init__(self, *, token: str, chat_ids: tuple[str, ...], store: SqliteStore) -> None:
        self._token = token
        self._chat_ids = chat_ids
        self._store = store
        self._endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    def push(self, event: NotificationEvent) -> None:
        nid = self._store.insert_notification(PersistedNotification(
            event_kind=event.kind, payload_json=json.dumps(event.payload),
        ))
        text = self._render(event)
        all_ok = True
        last_error: str | None = None
        for chat_id in self._chat_ids:
            try:
                r = requests.post(
                    self._endpoint,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
                if r.status_code >= 300:
                    all_ok = False
                    last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:
                all_ok = False
                last_error = str(e)
                log.warning("Telegram push failed: %s", e)

        existing = self._store.query_pending_notifications()
        match = next((p for p in existing if p.id == nid), None)
        if match is None:
            return
        match.sent_at = datetime.now(tz=timezone.utc)
        match.sent_ok = all_ok
        match.error = None if all_ok else last_error
        match.retry_count = 0 if all_ok else 1
        self._store.update_notification(match)

    @staticmethod
    def _render(event: NotificationEvent) -> str:
        head = f"*{event.kind}* @ {event.timestamp.isoformat()}"
        body = json.dumps(event.payload, indent=2)
        return f"{head}\n```\n{body[:3500]}\n```"
```

- [ ] **Step 6: Run, verify pass**

Expected: 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/quanterback/adapters/notify/ tests/fakes/notifier.py tests/unit/adapters/notify/
git commit -m "feat(notify): TelegramNotifier with fan-out, fire-and-forget, retry queue in SQLite"
```

### Task 6.4: ScanPipeline orchestration

**Files:**
- Create: `src/quanterback/pipeline.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_scan_pipeline.py`

- [ ] **Step 1: Write `src/quanterback/pipeline.py`**

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from quanterback.domain.events import NotificationEvent
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.domain.risk import RiskThresholds
from quanterback.interfaces.data import DataProvider, Summarizer
from quanterback.interfaces.decision import ApprovalGate, LLMStrategist
from quanterback.interfaces.events import EventSource
from quanterback.interfaces.execution import Executor
from quanterback.interfaces.notify import Notifier
from quanterback.interfaces.risk import (
    Backtester, OrderBuilder, PositionStateService, RiskGate,
)
from quanterback.interfaces.state import SystemStateService
from quanterback.interfaces.store import StateStore

log = logging.getLogger(__name__)


@dataclass
class ScanPipeline:
    event_source: EventSource
    data_provider: DataProvider
    summarizer: Summarizer
    strategist: LLMStrategist
    approval_gate: ApprovalGate
    position_state: PositionStateService
    backtester: Backtester
    risk_gate: RiskGate
    order_builder: OrderBuilder
    executor: Executor
    notifier: Notifier
    state_store: StateStore
    system_state: SystemStateService
    thresholds: RiskThresholds
    backtest_lookback_years: int
    max_concurrent_positions: int

    def run(self) -> None:
        st = self.system_state.get_current()
        if st.mode == "halted":
            log.info("System halted; exiting without scan.")
            return
        dry_run = (st.mode == "frozen")

        run = ScanRun(started_at=datetime.now(tz=timezone.utc), source="cron")
        run_id = self.state_store.insert_scan_run(run)
        run.id = run_id

        processed = 0
        errors = 0
        for event in self.event_source.stream():
            processed += 1
            try:
                self._process_event(event, run_id=run_id, dry_run=dry_run)
            except Exception as exc:
                errors += 1
                log.exception("Ticker %s failed in scan", event.ticker)
                self._notify("error", {"ticker": event.ticker, "error": str(exc)})
                self.state_store.insert_decision(PersistedDecision(
                    scan_run_id=run_id, ticker=event.ticker,
                    summary_json="{}", decision_json="{}", llm_model="n/a",
                    rejected_reason=f"exception: {exc}",
                    created_at=datetime.now(tz=timezone.utc),
                ))

        run.tickers_processed = processed
        run.errors_count = errors
        run.ended_at = datetime.now(tz=timezone.utc)
        self.state_store.update_scan_run(run)
        self._notify("scan_summary",
                     {"processed": processed, "errors": errors, "dry_run": dry_run})

    # ---------- per-event ----------

    def _process_event(self, event, *, run_id: int, dry_run: bool) -> None:
        # Concurrency limit
        if len(self.state_store.query_open_lifecycles()) >= self.max_concurrent_positions:
            self._persist_rejection(run_id, event.ticker,
                                     "max_concurrent_positions reached")
            return

        if self.position_state.has_open_lifecycle(event.ticker):
            self._persist_rejection(run_id, event.ticker,
                                     "ticker has open lifecycle")
            return

        window = self.data_provider.fetch(event.ticker)
        summary = self.summarizer.summarize(window)
        decision = self.strategist.decide(summary)

        dec_id = self.state_store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker=event.ticker,
            summary_json=summary.model_dump_json(),
            decision_json=decision.model_dump_json(),
            llm_model=getattr(self.strategist, "model_name", "unknown"),
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("decision", {
            "ticker": event.ticker, "action": decision.action,
            "rationale": decision.rationale,
        })

        if decision.action != "BUY":
            return

        approval = self.approval_gate.review(decision)
        if not approval.approved:
            self._update_rejection(dec_id, f"approval_gate: {approval.reason}")
            return

        assert decision.params is not None
        bt = self.backtester.run(_make_backtest_request(decision, self.backtest_lookback_years))
        assessment = self.risk_gate.evaluate(bt, self.thresholds)
        bt_id = self.state_store.insert_backtest(PersistedBacktest(
            decision_id=dec_id, report_json=bt.model_dump_json(),
            passed=assessment.passed,
            failed_checks=",".join(assessment.failed_checks) or None,
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("backtest", {
            "ticker": event.ticker, "passed": assessment.passed,
            "failed_checks": assessment.failed_checks,
        })
        if not assessment.passed:
            self._update_rejection(dec_id, f"risk_gate: {assessment.failed_checks}")
            return

        account_value = self.executor.get_account_value()
        spec = self.order_builder.build(decision, summary, account_value)
        result = self.executor.submit(spec, dry_run=dry_run)
        order_id = self.state_store.insert_order(PersistedOrder(
            decision_id=dec_id, backtest_id=bt_id,
            bracket_spec_json=spec.model_dump_json(),
            alpaca_order_id=result.order_id,
            submitted_at=datetime.now(tz=timezone.utc),
            dry_run=dry_run,
            raw_response_json=json.dumps(result.raw_response),
        ))
        self._notify("order", {
            "ticker": event.ticker, "submitted": result.submitted,
            "dry_run": dry_run, "order_id": result.order_id,
        })
        if result.submitted and result.order_id:
            self.state_store.upsert_position(PersistedPosition(
                ticker=event.ticker, order_id=order_id, state="pending",
                entry_price=None, sl=spec.stop_loss_price, tp=spec.take_profit_price,
                qty=spec.qty, opened_at=datetime.now(tz=timezone.utc),
            ))

    # ---------- helpers ----------

    def _persist_rejection(self, run_id: int, ticker: str, reason: str) -> None:
        self.state_store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker=ticker, summary_json="{}",
            decision_json="{}", llm_model="n/a",
            rejected_reason=reason,
            created_at=datetime.now(tz=timezone.utc),
        ))
        self._notify("decision",
                     {"ticker": ticker, "action": "REJECTED", "reason": reason})

    def _update_rejection(self, decision_id: int, reason: str) -> None:
        # Plain UPDATE for clarity; could be a StateStore method later.
        self.state_store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE decisions SET rejected_reason=? WHERE id=?",
            (reason, decision_id),
        )

    def _notify(self, kind: str, payload: dict) -> None:
        self.notifier.push(NotificationEvent(
            kind=kind, payload=payload,  # type: ignore[arg-type]
            timestamp=datetime.now(tz=timezone.utc),
        ))


def _make_backtest_request(decision, lookback_years: int):
    from quanterback.domain.backtest import BacktestRequest
    assert decision.params is not None
    return BacktestRequest(
        ticker=decision.ticker, strategy=decision.strategy,
        params=decision.params.model_dump(), lookback_years=lookback_years,
    )
```

- [ ] **Step 2: Write integration test scenarios (spec §7.3)**

`tests/integration/__init__.py` (empty)

`tests/integration/test_scan_pipeline.py`:
```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource
from quanterback.adapters.position.sqlite_alpaca_synced_state import (
    SqliteAlpacaSyncedPositionState,
)
from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.market import PriceWindow
from quanterback.domain.risk import RiskThresholds
from quanterback.pipeline import ScanPipeline
from tests.fakes.executor import InMemorySimulatorExecutor
from tests.fakes.historical_data import FakeHistoricalDataProvider
from tests.fakes.llm_client import FakeLLMClient
from tests.fakes.notifier import FakeNotifier


# ----- shared fakes for these scenarios -----

@dataclass
class FakeDataProvider:
    window: PriceWindow

    def fetch(self, ticker: str) -> PriceWindow:
        return self.window


@dataclass
class FakeStrategist:
    canned: object  # StrategyDecision instance
    model_name: str = "fake-strategist"

    def decide(self, summary):
        return self.canned


# ----- builders -----

def _smooth_uptrend_window() -> PriceWindow:
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=300, freq="B")
    closes = np.linspace(100, 150, 300) + np.sin(np.linspace(0, 60, 300)) * 1.0
    daily = pd.DataFrame({"open": closes, "high": closes * 1.01,
                          "low": closes * 0.99, "close": closes,
                          "volume": np.full(300, 1_000_000)}, index=idx)
    hourly = daily.iloc[-30:].copy()
    return PriceWindow(ticker="AAPL", daily=daily, hourly=hourly,
                       as_of=datetime(2026, 5, 22, tzinfo=timezone.utc))


def _make_pipeline(
    tmp_path: Path,
    *,
    decision,
    backtest_data: pd.DataFrame | None = None,
    mode: str = "normal",
    open_tickers: tuple[str, ...] = (),
    fail_executor: bool = False,
) -> tuple[ScanPipeline, SqliteStore, InMemorySimulatorExecutor, FakeNotifier]:
    wl = tmp_path / "wl.txt"
    wl.write_text("AAPL\n")
    store = SqliteStore(tmp_path / "t.sqlite")
    sys_state = SqliteSystemStateService(store)
    if mode != "normal":
        sys_state.set(mode, "test", "test")
    notifier = FakeNotifier()
    executor = InMemorySimulatorExecutor(fail_next=fail_executor)
    bt_data = backtest_data if backtest_data is not None else _smooth_uptrend_window().daily
    pipeline = ScanPipeline(
        event_source=WatchlistEventSource(wl),
        data_provider=FakeDataProvider(_smooth_uptrend_window()),
        summarizer=RuleBasedSummarizer(),
        strategist=FakeStrategist(decision),
        approval_gate=NoOpApprovalGate(),
        position_state=SqliteAlpacaSyncedPositionState(store, alpaca_synced=False),
        backtester=VectorizedBacktester(
            FakeHistoricalDataProvider({"AAPL": bt_data})
        ),
        risk_gate=ThresholdRiskGate(),
        order_builder=ATRBracketOrderBuilder(
            sl_atr_multiple=2.0, tp_atr_multiple=4.0, position_size_pct=0.05,
        ),
        executor=executor,
        notifier=notifier,
        state_store=store,
        system_state=sys_state,
        thresholds=RiskThresholds(),
        backtest_lookback_years=3,
        max_concurrent_positions=5,
    )
    # Seed open positions if requested
    if open_tickers:
        from quanterback.domain.persisted import (
            PersistedBacktest, PersistedDecision, PersistedOrder, PersistedPosition, ScanRun,
        )
        run_id = store.insert_scan_run(ScanRun(
            started_at=datetime.now(tz=timezone.utc), source="seed",
        ))
        for t in open_tickers:
            did = store.insert_decision(PersistedDecision(
                scan_run_id=run_id, ticker=t, summary_json="{}", decision_json="{}",
                llm_model="m", created_at=datetime.now(tz=timezone.utc),
            ))
            bid = store.insert_backtest(PersistedBacktest(
                decision_id=did, report_json="{}", passed=True,
                created_at=datetime.now(tz=timezone.utc),
            ))
            oid = store.insert_order(PersistedOrder(
                decision_id=did, backtest_id=bid, bracket_spec_json="{}",
                submitted_at=datetime.now(tz=timezone.utc),
            ))
            store.upsert_position(PersistedPosition(
                ticker=t, order_id=oid, state="bracket_active",
                opened_at=datetime.now(tz=timezone.utc),
            ))
    return pipeline, store, executor, notifier


def _buy_decision():
    from quanterback.domain.decision import MomentumParams, StrategyDecision
    return StrategyDecision(
        action="BUY", ticker="AAPL", strategy="MOMENTUM",
        params=MomentumParams(lookback_days=20, momentum_threshold=0.05),
        rationale="bullish alignment with elevated volume confirms momentum",
        confidence=0.7,
    )


def _pass_decision():
    from quanterback.domain.decision import StrategyDecision
    return StrategyDecision(
        action="PASS", ticker="AAPL", strategy="MOMENTUM", params=None,
        rationale="extended above SMA200 by more than acceptable threshold",
        confidence=0.4,
    )


# ----- scenarios from spec §7.3 -----

def test_scenario_1_buy_happy_path(tmp_path: Path) -> None:
    pipeline, store, executor, notifier = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.run()
    assert len(executor.submitted) == 1
    orders = store._conn.execute("SELECT alpaca_order_id FROM orders").fetchall()
    assert orders and orders[0][0].startswith("sim-")


def test_scenario_2_pass_short_circuit(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_pass_decision())
    pipeline.run()
    assert executor.submitted == []
    bts = store._conn.execute("SELECT COUNT(*) FROM backtests").fetchone()[0]
    assert bts == 0


def test_scenario_3_risk_gate_rejects_excessive_drawdown(tmp_path: Path) -> None:
    # Build whipsaw historical data so backtest has high MaxDD
    idx = pd.date_range(end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                        periods=1000, freq="B")
    closes = 100 + np.sin(np.linspace(0, 60, 1000)) * 40   # large swings
    bt_df = pd.DataFrame({"open": closes, "high": closes * 1.03,
                          "low": closes * 0.97, "close": closes,
                          "volume": np.full(1000, 1_000_000)}, index=idx)
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), backtest_data=bt_df,
    )
    pipeline.run()
    assert executor.submitted == []
    passed = store._conn.execute("SELECT passed FROM backtests").fetchone()
    assert passed is not None and passed[0] == 0


def test_scenario_4_invalid_llm_output_handled(tmp_path: Path) -> None:
    # Strategist that raises (simulating schema-invalid LLM output)
    @dataclass
    class BrokenStrategist:
        model_name: str = "broken"
        def decide(self, summary):
            raise ValueError("LLM output failed schema validation")

    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.strategist = BrokenStrategist()
    pipeline.run()
    rejected = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchall()
    assert rejected and "exception" in rejected[0][0]
    assert executor.submitted == []


def test_scenario_5_ticker_already_open_rejected(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), open_tickers=("AAPL",),
    )
    pipeline.run()
    assert executor.submitted == []
    rej = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchall()
    assert any("open lifecycle" in r[0] for r in rej)


def test_scenario_6_frozen_mode_dry_run(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), mode="frozen",
    )
    pipeline.run()
    # executor.submitted is only populated for live submissions
    assert executor.submitted == []
    dry = store._conn.execute("SELECT dry_run FROM orders").fetchone()
    assert dry is not None and dry[0] == 1


def test_scenario_7_halted_mode_exits_immediately(tmp_path: Path) -> None:
    pipeline, store, executor, _ = _make_pipeline(
        tmp_path, decision=_buy_decision(), mode="halted",
    )
    pipeline.run()
    # No decisions persisted
    n = store._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert n == 0


def test_scenario_8_data_fetch_exception_isolated(tmp_path: Path) -> None:
    @dataclass
    class BoomData:
        def fetch(self, ticker: str):
            raise RuntimeError("yfinance down")

    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.data_provider = BoomData()
    pipeline.run()
    rej = store._conn.execute(
        "SELECT rejected_reason FROM decisions WHERE rejected_reason IS NOT NULL"
    ).fetchone()
    assert rej and "yfinance down" in rej[0]
    assert executor.submitted == []


def test_scenario_9_notifier_failure_does_not_break_chain(tmp_path: Path) -> None:
    @dataclass
    class BrokenNotifier:
        attempts: int = 0
        def push(self, event) -> None:
            self.attempts += 1
            # Per Notifier contract, it MUST NOT raise — but if it did, pipeline
            # behaviour should still complete. Simulate "silently fails".
            return None
    pipeline, store, executor, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    bn = BrokenNotifier()
    pipeline.notifier = bn
    pipeline.run()
    assert len(executor.submitted) == 1
    assert bn.attempts > 0


def test_scenario_10_frozen_then_scan_records_dry_run(tmp_path: Path) -> None:
    pipeline, store, _, _ = _make_pipeline(tmp_path, decision=_buy_decision())
    pipeline.system_state.set("frozen", "tg user", "tg-actor")
    pipeline.run()
    dry = store._conn.execute("SELECT dry_run FROM orders").fetchone()
    assert dry is not None and dry[0] == 1
```

- [ ] **Step 3: Run integration tests**

Run: `make test tests/integration/`
Expected: 10 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/quanterback/pipeline.py tests/integration/
git commit -m "feat(pipeline): ScanPipeline with full §7.3 integration scenarios"
```

**Phase 6 milestone reached.** End-to-end pipeline tested across all 10 specified scenarios, all using fake/stub adapters (no real network calls).

---

## Phase 7 — Containerization + Production Wire-up + ControlBot

**Goal:** Real CLI commands wired via `wire()`, working `TelegramControlChannel` long-polling daemon, polished Dockerfile, and a manual smoke test using real Claude + Alpaca Paper.

**Exit milestone:** `make scan-once` runs an end-to-end scan against real services and persists results to SQLite; `docker compose up` keeps both `scan` and `control-bot` containers healthy.

### Task 7.1: TelegramControlChannel

**Files:**
- Create: `src/quanterback/adapters/control/__init__.py`
- Create: `src/quanterback/adapters/control/telegram_control_channel.py`
- Create: `tests/unit/adapters/control/__init__.py`
- Create: `tests/unit/adapters/control/test_telegram_control_channel.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quanterback.adapters.control.telegram_control_channel import (
    TelegramControlChannel,
    parse_command,
)


def test_parse_freeze() -> None:
    cmd = parse_command({
        "message": {"text": "/freeze just testing",
                    "from": {"id": 42, "username": "alice"}},
    })
    assert cmd is not None
    assert cmd.command == "freeze"
    assert cmd.actor == "42"


def test_parse_unknown_command_returns_none() -> None:
    assert parse_command({"message": {"text": "/foo", "from": {"id": 1}}}) is None


def test_parse_non_message_returns_none() -> None:
    assert parse_command({"edited_message": {}}) is None


def test_listen_yields_parsed_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {"update_id": 1, "message": {"text": "/freeze",
                                       "from": {"id": 42}}},
        {"update_id": 2, "message": {"text": "/halt",
                                       "from": {"id": 42}}},
        {"update_id": 3, "message": {"text": "/random",
                                       "from": {"id": 42}}},
    ]
    served = {"index": 0}

    def fake_get(url: str, params: dict, timeout: float) -> object:
        i = served["index"]
        served["index"] += 1
        class R:
            status_code = 200
            def json(self):
                if i == 0:
                    return {"ok": True, "result": updates}
                # signal end of stream by raising
                raise StopIteration
        return R()

    monkeypatch.setattr(
        "quanterback.adapters.control.telegram_control_channel.requests.get",
        fake_get,
    )
    ch = TelegramControlChannel(token="t", max_iterations=1)
    cmds = list(ch.listen())
    kinds = [c.command for c in cmds]
    assert kinds == ["freeze", "halt"]
```

- [ ] **Step 2: Run, verify fail**

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write `src/quanterback/adapters/control/__init__.py`** (empty)

- [ ] **Step 4: Write `src/quanterback/adapters/control/telegram_control_channel.py`**

```python
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone

import requests

from quanterback.domain.events import ControlCommand

log = logging.getLogger(__name__)

VALID_COMMANDS = {"freeze", "unfreeze", "halt", "unhalt", "status"}


def parse_command(update: dict) -> ControlCommand | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text") or ""
    if not text.startswith("/"):
        return None
    head = text.split()[0][1:].lower()
    if head not in VALID_COMMANDS:
        return None
    actor = str(msg.get("from", {}).get("id", "unknown"))
    return ControlCommand(
        command=head, actor=actor, received_at=datetime.now(tz=timezone.utc),
    )


class TelegramControlChannel:
    """Polls Telegram getUpdates; yields parsed control commands. Blocking."""

    def __init__(
        self, *, token: str, poll_timeout: int = 25, max_iterations: int | None = None,
    ) -> None:
        self._endpoint = f"https://api.telegram.org/bot{token}/getUpdates"
        self._poll_timeout = poll_timeout
        self._last_update_id = 0
        self._max_iterations = max_iterations

    def listen(self) -> Iterable[ControlCommand]:
        iters = 0
        while True:
            if self._max_iterations is not None and iters >= self._max_iterations:
                return
            iters += 1
            try:
                resp = requests.get(
                    self._endpoint,
                    params={"offset": self._last_update_id + 1,
                            "timeout": self._poll_timeout},
                    timeout=self._poll_timeout + 10,
                )
                payload = resp.json()
            except StopIteration:
                return
            except Exception as e:
                log.warning("TG getUpdates failed: %s", e)
                continue
            if not payload.get("ok"):
                continue
            for update in payload.get("result", []):
                uid = update.get("update_id", 0)
                if uid > self._last_update_id:
                    self._last_update_id = uid
                cmd = parse_command(update)
                if cmd is not None:
                    yield cmd
```

- [ ] **Step 5: Run, verify pass**

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quanterback/adapters/control/ tests/unit/adapters/control/
git commit -m "feat(control): TelegramControlChannel long-polling /freeze /halt etc."
```

### Task 7.2: CLI entrypoint with wire() composition root

**Files:**
- Modify: `src/quanterback/cli.py`

- [ ] **Step 1: Rewrite `src/quanterback/cli.py`**

```python
"""CLI entrypoint. Composes all adapters via wire() and dispatches subcommands."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from quanterback.adapters.control.telegram_control_channel import TelegramControlChannel
from quanterback.adapters.data.rule_based_summarizer import RuleBasedSummarizer
from quanterback.adapters.data.yfinance_provider import YFinanceProvider
from quanterback.adapters.decision.claude_client import ClaudeClient
from quanterback.adapters.decision.noop_approval_gate import NoOpApprovalGate
from quanterback.adapters.decision.prompted_strategist import PromptedLLMStrategist
from quanterback.adapters.events.watchlist_event_source import WatchlistEventSource
from quanterback.adapters.execution.alpaca_paper_executor import AlpacaPaperExecutor
from quanterback.adapters.notify.telegram_notifier import TelegramNotifier
from quanterback.adapters.position.sqlite_alpaca_synced_state import (
    SqliteAlpacaSyncedPositionState,
)
from quanterback.adapters.risk.atr_bracket_builder import ATRBracketOrderBuilder
from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.adapters.risk.vectorized_backtester import VectorizedBacktester
from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.config import AppConfig
from quanterback.pipeline import ScanPipeline


log = logging.getLogger("quanterback")


def wire(config: AppConfig) -> tuple[ScanPipeline, SqliteSystemStateService, str]:
    store = SqliteStore(config.db_path)
    sys_state = SqliteSystemStateService(store)
    notifier = TelegramNotifier(
        token=config.tg_token, chat_ids=config.tg_chat_ids, store=store,
    )
    data_provider = YFinanceProvider(
        cache_dir=config.cache_dir, cache_ttl_hours=config.cache_ttl_hours,
    )
    summarizer = RuleBasedSummarizer()
    llm_client = ClaudeClient(api_key=config.anthropic_key, model=config.llm_model)
    strategist = PromptedLLMStrategist(
        llm_client, prompt_template_path=config.prompt_template_path,
        temperature=config.llm_temperature,
    )
    approval_gate = NoOpApprovalGate()
    backtester = VectorizedBacktester(data_provider)
    risk_gate = ThresholdRiskGate()
    order_builder = ATRBracketOrderBuilder(
        sl_atr_multiple=config.sl_atr_multiple,
        tp_atr_multiple=config.tp_atr_multiple,
        position_size_pct=config.position_size_pct,
    )
    executor = AlpacaPaperExecutor(
        api_key=config.alpaca_key, secret=config.alpaca_secret,
    )
    position_state = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    event_source = WatchlistEventSource(config.watchlist_path)
    pipeline = ScanPipeline(
        event_source=event_source,
        data_provider=data_provider,
        summarizer=summarizer,
        strategist=strategist,
        approval_gate=approval_gate,
        position_state=position_state,
        backtester=backtester,
        risk_gate=risk_gate,
        order_builder=order_builder,
        executor=executor,
        notifier=notifier,
        state_store=store,
        system_state=sys_state,
        thresholds=config.risk_thresholds,
        backtest_lookback_years=config.backtest_lookback_years,
        max_concurrent_positions=config.max_concurrent_positions,
    )
    return pipeline, sys_state, config.tg_token


def _load_config() -> AppConfig:
    toml_paths = [
        Path("/config/quanterback.toml"),
        Path("/config/quanterback.local.toml"),
    ]
    return AppConfig.load(toml_paths=toml_paths)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def cmd_scan(_args) -> int:
    _setup_logging()
    config = _load_config()
    pipeline, _, _ = wire(config)
    pipeline.run()
    return 0


def cmd_control_bot(_args) -> int:
    _setup_logging()
    config = _load_config()
    _, sys_state, token = wire(config)
    channel = TelegramControlChannel(token=token)
    log.info("ControlBot listening for /freeze /unfreeze /halt /unhalt /status")
    for cmd in channel.listen():
        if cmd.command == "freeze":
            sys_state.set("frozen", "user-requested via Telegram", cmd.actor)
        elif cmd.command == "unfreeze":
            sys_state.set("normal", "user-requested via Telegram", cmd.actor)
        elif cmd.command == "halt":
            sys_state.set("halted", "user-requested via Telegram", cmd.actor)
        elif cmd.command == "unhalt":
            sys_state.set("normal", "user-requested via Telegram", cmd.actor)
        # status: no-op for v0 (could push a state summary in v1)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanterback")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="Run a single end-to-end watchlist scan")
    sub.add_parser("control-bot", help="Run the Telegram control daemon")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "control-bot":
        return cmd_control_bot(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run typecheck and tests**

Run: `make typecheck && make test`
Expected: all PASS, mypy clean.

- [ ] **Step 3: Commit**

```bash
git add src/quanterback/cli.py
git commit -m "feat(cli): production wire() composition root and scan / control-bot subcommands"
```

### Task 7.3: Polish Dockerfile and docker-compose for production-like layout

- [ ] **Step 1: Update `docker/Dockerfile`** so the build pulls a stable supercronic version and copies the prompt template:

Replace the existing `COPY src/ ./src/` block with:
```dockerfile
COPY pyproject.toml ./
RUN uv pip install --system -e ".[dev]"

COPY src/ ./src/
COPY config/ /config/
COPY tests/ ./tests/
COPY docker/crontab /app/docker/crontab
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# Re-install so the editable install picks up src/
RUN uv pip install --system -e ".[dev]"
```

- [ ] **Step 2: Verify `docker-compose.yml` mounts `./config:/config:ro`** — already added in Phase 0.

- [ ] **Step 3: Rebuild and run the unit + integration tests in the container**

Run:
```bash
make build
make test
```
Expected: all PASS.

- [ ] **Step 4: Commit any tooling-driven changes**

```bash
git add docker/
git commit -m "build: copy config/ and prompts into image for runtime mounts"
```

### Task 7.4: Smoke test against real Claude + Alpaca Paper

This task is **manual**. It is not automated CI — it is a verification step the operator runs once at the end of Phase 7.

- [ ] **Step 1: Fill in `.env` with real keys**

Make sure these four are set in `./.env`:
- `ANTHROPIC_API_KEY` (a valid Anthropic API key)
- `ALPACA_API_KEY` (a paper-trading API key from Alpaca)
- `ALPACA_SECRET` (the matching secret)
- `TELEGRAM_BOT_TOKEN` (a Telegram bot token from @BotFather)

- [ ] **Step 2: Add at least one chat_id to `config/quanterback.local.toml`**

```toml
[telegram]
chat_ids = ["YOUR_CHAT_ID_HERE"]
```

(Get your chat_id by sending /start to your bot, then visiting
`https://api.telegram.org/bot<TOKEN>/getUpdates` and looking for `"chat":{"id":...}`.)

- [ ] **Step 3: Build the image**

Run: `make build`
Expected: image built without errors.

- [ ] **Step 4: Run a single scan**

Run: `make scan-once`
Expected:
- Container exits 0
- A row appears in `data/quanterback.sqlite` under `scan_runs`
- 5 rows in `decisions` (one per watchlist entry); some may be PASS
- Telegram messages received for each decision + a scan_summary

If decisions all error: inspect `data/quanterback.sqlite` `decisions.rejected_reason`.

- [ ] **Step 5: Start the daemons**

Run: `make up`
Expected: both `scan` and `control-bot` containers `Up`.

- [ ] **Step 6: Test ControlBot via Telegram**

Send your bot:
```
/freeze
```
Then run `make scan-once`. Expected: scan completes but all orders are dry_run=1 (no Alpaca submission).

Send:
```
/unfreeze
```
Then run `make scan-once`. Expected: normal flow resumes; any BUY decisions that pass risk gate submit real bracket orders to Alpaca paper.

- [ ] **Step 7: Verify Alpaca paper account state**

Open the Alpaca paper dashboard and confirm any submitted orders appear with the expected SL/TP attached.

- [ ] **Step 8: Stop the daemons**

Run: `make down`

- [ ] **Step 9: Commit a milestone marker (no code changes; just an empty commit recording smoke pass)**

```bash
git commit --allow-empty -m "chore: phase 7 smoke test passed (Claude + Alpaca paper + Telegram round-trip)"
```

**Phase 7 milestone reached.** Full v0 system is live: cron-driven scans against real Claude and Alpaca Paper, with Telegram notifications and freeze/halt control.

---

## Definition of Done (v0)

Before declaring v0 complete:

- [ ] All Phase 0-7 milestones reached
- [ ] `make test` green (all unit + integration tests pass)
- [ ] `make lint && make typecheck` green
- [ ] At least one full end-to-end scan run against real services has been recorded (Task 7.4)
- [ ] `make logs` shows clean output (no stack traces from successful runs)
- [ ] Telegram notifications confirmed working bi-directionally
- [ ] Alpaca paper account shows submitted bracket orders with correct SL/TP
- [ ] `data/quanterback.sqlite` audit-readable: every BUY decision can be traced through summary → backtest → order

## Out of Scope for v0 (deferred to v1/v2 per spec §8)

- Mean Reversion strategy (v1)
- Multi-source EventSource + priority queue (v1)
- TelegramApprovalGate (v1)
- Telegram `/scan TICKER` user-triggered events (v1)
- Switching LLM providers / caching LLMClient (v1)
- vectorbt / backtrader backtest engine (v1)
- Grid strategy (v2)
- Live trading (v2 — see spec §9 Open Question #11 for PDT/GFV constraints)
- Portfolio-level RiskGate (v2)
- RL / fine-tuned strategist (v2)

