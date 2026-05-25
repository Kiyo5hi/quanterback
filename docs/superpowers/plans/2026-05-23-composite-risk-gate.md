# Composite Risk Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dysfunctional absolute-threshold RiskGate with a composite mechanism that evaluates relative backtest performance (vs buy-and-hold), walk-forward out-of-sample metrics, and returns a size_multiplier ∈ [0.25, 1.0] instead of binary pass/fail.

**Architecture:** 
1. Extend BacktestReport with buy_and_hold metrics and walk-forward (OOS) metrics computed on the final 33% of data.
2. Extend RiskAssessment with size_multiplier field.
3. Create CompositeRiskGate that applies: (a) sanity caps for catastrophic strategies, (b) relative gates comparing strategy vs B&H, (c) sizing score for accepted strategies.
4. Update OrderBuilder to accept size_multiplier and scale position qty accordingly.
5. Update pipeline to thread size_multiplier through to order building.
6. Wire CompositeRiskGate as default in CLI (with PDT wrapping preserved).
7. Update existing tests for new defaults and add comprehensive CompositeRiskGate tests.

**Tech Stack:** Pydantic models, pandas, numpy; no new dependencies

---

## File Structure

**Modified files:**
- `src/quanterback/domain/backtest.py` — add relative + OOS fields to BacktestReport
- `src/quanterback/domain/risk.py` — add size_multiplier to RiskAssessment; update RiskThresholds defaults (these are now SANITY caps)
- `src/quanterback/adapters/risk/vectorized_backtester.py` — compute new metrics in _build_report
- `src/quanterback/adapters/risk/atr_bracket_builder.py` — accept size_multiplier in build()
- `src/quanterback/pipeline.py` — thread size_multiplier from assessment to order_builder
- `src/quanterback/cli.py` — wire CompositeRiskGate instead of ThresholdRiskGate
- `config/quanterback.toml` — update risk.thresholds defaults + add comments explaining they are sanity caps

**New files:**
- `src/quanterback/adapters/risk/composite_risk_gate.py` — the new gate implementation

**Test files modified:**
- `tests/unit/domain/test_risk.py` — update assertions for new defaults
- `tests/unit/test_config.py` — update assertions for new defaults
- `tests/unit/adapters/risk/test_threshold_risk_gate.py` — adjust 4 tests for new defaults
- `tests/unit/adapters/risk/test_atr_bracket_builder.py` — add 2 size_multiplier tests
- `tests/unit/adapters/risk/test_vectorized_backtester.py` — add test for new fields
- `tests/integration/test_scan_pipeline.py` — verify scenario_3 still rejects

**New test files:**
- `tests/unit/adapters/risk/test_composite_risk_gate.py` — comprehensive CompositeRiskGate tests

---

## Task 1: Extend BacktestReport with Relative Metrics

**Files:**
- Modify: `src/quanterback/domain/backtest.py:28-43`

### Step 1.1: Add new fields to BacktestReport

Read the file, then add these fields:
```python
buy_and_hold_return: float = 0.0
buy_and_hold_max_drawdown: float = 0.0
excess_return: float = 0.0
drawdown_ratio: float = 0.0
```

These fields represent:
- `buy_and_hold_return`: Return of a B&H strategy from the same period
- `buy_and_hold_max_drawdown`: MaxDD of B&H
- `excess_return`: strategy cumulative_return - buy_and_hold_return
- `drawdown_ratio`: strategy max_drawdown / max(buy_and_hold_max_drawdown, 0.01)

### Step 1.2: Add OOS walk-forward fields

Add these fields:
```python
oos_num_trades: int = 0
oos_win_rate: float = 0.0
oos_max_drawdown: float = 0.0
oos_sharpe: float = 0.0
oos_cumulative_return: float = 0.0
oos_excess_return: float = 0.0
```

These represent metrics computed only on trades entering in the final 33% of the data (out-of-sample / test window).

---

## Task 2: Extend RiskAssessment with size_multiplier

**Files:**
- Modify: `src/quanterback/domain/risk.py:26-29`

### Step 2.1: Add size_multiplier field

Add:
```python
size_multiplier: float = 1.0  # 0 = rejected, 0.25-1.0 = sized position
```

This field represents the fraction of the calculated position size to actually submit. Hard reject (size=0) happens only for truly broken strategies; mediocre strategies get 0.25×-1.0×.

### Step 2.2: Update RiskThresholds defaults and add docstring

Update `RiskThresholds` class docstring to explain these are SANITY caps now, not hard thresholds:

```python
class RiskThresholds(BaseModel):
    """SANITY caps for catastrophic strategies (used by CompositeRiskGate).
    
    NOT hard rejection thresholds. CompositeRiskGate uses these only to
    reject truly broken strategies (e.g. MaxDD > 50%, too few OOS trades).
    Real evaluation is relative (vs B&H) and uses walk-forward OOS metrics.
    """
    model_config = ConfigDict(frozen=True)
    
    max_drawdown: float = Field(default=0.50, ge=0, le=1)
    min_sharpe: float = Field(default=-0.5)
    min_win_rate: float = Field(default=0.0, ge=0, le=1)
    min_profit_factor: float = Field(default=0.0, ge=0)
    min_num_trades: int = Field(default=5, ge=0)
```

This changes defaults: max_drawdown 0.25 → 0.50, min_sharpe 0.5 → -0.5, min_win_rate 0.40 → 0.0, min_profit_factor 1.2 → 0.0, min_num_trades 10 → 5.

---

## Task 3: Compute Relative Metrics in VectorizedBacktester

**Files:**
- Modify: `src/quanterback/adapters/risk/vectorized_backtester.py:26-40` (run method)
- Modify: `src/quanterback/adapters/risk/vectorized_backtester.py:118-167` (_build_report method)

### Step 3.1: Pass lookback to _build_report

Change the `run()` method's call to `_build_report()`:

Current:
```python
return self._build_report(request, df, trades)
```

Change to:
```python
return self._build_report(request, df, trades, lookback)
```

### Step 3.2: Update _build_report signature

Change signature from:
```python
@staticmethod
def _build_report(
    request: BacktestRequest, df: pd.DataFrame, trades: list[TradeRecord],
) -> BacktestReport:
```

To:
```python
@staticmethod
def _build_report(
    request: BacktestRequest, df: pd.DataFrame, trades: list[TradeRecord],
    lookback: int,
) -> BacktestReport:
```

### Step 3.3: Compute buy-and-hold metrics in the empty trades case

In the early return (when no trades):
```python
if not trades:
    warmup_idx = lookback + 14
    bh_window = df["close"].iloc[warmup_idx:]
    bh_return = float(bh_window.iloc[-1] / bh_window.iloc[0] - 1) if len(bh_window) > 0 else 0.0
    bh_max_dd = float((1 - bh_window / bh_window.cummax()).max()) if len(bh_window) > 0 else 0.0
    
    return BacktestReport(
        ticker=request.ticker, strategy=request.strategy,
        params=request.params,
        period_start=_to_date(df.index[0]),
        period_end=_to_date(df.index[-1]),
        num_trades=0, win_rate=0.0, max_drawdown=0.0, sharpe=0.0,
        profit_factor=0.0, cumulative_return=0.0,
        avg_trade_return=0.0, avg_bars_held=0.0, trades=[],
        buy_and_hold_return=bh_return,
        buy_and_hold_max_drawdown=bh_max_dd,
        excess_return=0.0 - bh_return,
        drawdown_ratio=0.0 / max(bh_max_dd, 0.01),
        oos_num_trades=0,
        oos_win_rate=0.0,
        oos_max_drawdown=0.0,
        oos_sharpe=0.0,
        oos_cumulative_return=0.0,
        oos_excess_return=0.0,
    )
```

### Step 3.4: Compute metrics in the normal case

At the end of _build_report (before the return statement), add:

```python
warmup_idx = lookback + 14
bh_window = df["close"].iloc[warmup_idx:]
bh_return = float(bh_window.iloc[-1] / bh_window.iloc[0] - 1) if len(bh_window) > 0 else 0.0
bh_max_dd_arr = 1 - bh_window / bh_window.cummax()
bh_max_dd = float(bh_max_dd_arr.max()) if len(bh_max_dd_arr) > 0 else 0.0
excess = cumulative_return - bh_return
dd_ratio = max_dd / max(bh_max_dd, 0.01)

# Walk-forward: last 33% of data
split_idx = warmup_idx + int((len(df) - warmup_idx) * 0.67)
split_date = _to_date(df.index[split_idx])
oos_trades = [t for t in trades if t.entry_date >= split_date]

oos_num = len(oos_trades)
oos_returns = np.array([t.return_pct for t in oos_trades])
oos_wins = oos_returns[oos_returns > 0]
oos_equity = (1 + oos_returns).cumprod() if len(oos_returns) > 0 else np.array([])
oos_dd = 1 - oos_equity / np.maximum.accumulate(oos_equity) if len(oos_equity) > 0 else np.array([])
oos_max_dd = float(oos_dd.max()) if len(oos_dd) > 0 else 0.0

oos_cum_ret = float(oos_equity[-1] - 1) if len(oos_equity) > 0 else 0.0
oos_mean_ret = float(oos_returns.mean()) if len(oos_returns) > 0 else 0.0
oos_std_ret = float(oos_returns.std(ddof=1)) if len(oos_returns) > 1 else 0.0
oos_avg_bars = float(np.mean([t.bars_held for t in oos_trades])) if oos_trades else 1.0
oos_trades_per_year = 252 / max(oos_avg_bars, 1.0)
oos_sharpe_val = (
    (oos_mean_ret / oos_std_ret) * np.sqrt(oos_trades_per_year)
    if oos_std_ret > 0 else 0.0
)

# B&H for OOS window
bh_oos_window = df["close"].iloc[split_idx:]
bh_oos_return = float(bh_oos_window.iloc[-1] / bh_oos_window.iloc[0] - 1) if len(bh_oos_window) > 0 else 0.0
oos_excess = oos_cum_ret - bh_oos_return

# Update the return statement to include these new fields:
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
    buy_and_hold_return=bh_return,
    buy_and_hold_max_drawdown=bh_max_dd,
    excess_return=excess,
    drawdown_ratio=dd_ratio,
    oos_num_trades=oos_num,
    oos_win_rate=float(len(oos_wins) / len(oos_trades)) if oos_trades else 0.0,
    oos_max_drawdown=max(oos_max_dd, 0.0),
    oos_sharpe=float(oos_sharpe_val),
    oos_cumulative_return=oos_cum_ret,
    oos_excess_return=oos_excess,
)
```

---

## Task 4: Create CompositeRiskGate

**Files:**
- Create: `src/quanterback/adapters/risk/composite_risk_gate.py`

### Step 4.1: Write the file

Create a new file with the complete implementation:

```python
from __future__ import annotations

from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class CompositeRiskGate:
    """Sanity caps + relative gate + sizing. Replaces hard absolute threshold rejection.
    
    This gate uses three layers:
    1. Sanity caps: reject catastrophic strategies (MaxDD > threshold, too few OOS trades, etc.)
    2. Relative gates: reject if strategy underperforms B&H or has bad OOS decay
    3. Sizing: for strategies that pass, compute a size_multiplier ∈ [0.25, 1.0]
    """

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        failed: list[str] = []

        # ---- Sanity caps (catastrophic strategies) ----
        if report.num_trades < thresholds.min_num_trades:
            failed.append("sanity_min_num_trades")
        if report.max_drawdown > thresholds.max_drawdown:
            failed.append("sanity_max_drawdown")
        if report.oos_num_trades < 2:
            failed.append("sanity_min_oos_trades")
        if report.sharpe < thresholds.min_sharpe:
            failed.append("sanity_min_sharpe")

        if failed:
            return RiskAssessment(passed=False, failed_checks=failed,
                                   size_multiplier=0.0)

        # ---- Relative gates ----
        if report.excess_return < 0 and report.cumulative_return < 0:
            failed.append("strategy_worse_than_bh_and_negative")
        if (report.oos_excess_return < 0
            and report.oos_cumulative_return < -0.10):
            failed.append("oos_loss_relative_and_absolute")
        if report.drawdown_ratio >= 1.2:
            failed.append("strategy_dd_worse_than_bh")

        if failed:
            return RiskAssessment(passed=False, failed_checks=failed,
                                   size_multiplier=0.0)

        # ---- Sizing (passed all gates) ----
        score = 0.0
        # Excess return: capped at ±0.30, weight 1.5 → range ±0.45
        score += min(max(report.excess_return, -0.30), 0.30) * 1.5
        # DD ratio: 0.0 → +0.5, 1.0 → 0.0, 1.2+ → negative (already rejected)
        score += (1.0 - min(report.drawdown_ratio, 1.2)) * 0.5
        # OOS Sharpe: capped at [-1, 2], weight 0.15 → range -0.15..+0.30
        score += min(max(report.oos_sharpe, -1.0), 2.0) * 0.15
        size_multiplier = max(0.25, min(1.0, 0.5 + score))

        return RiskAssessment(passed=True, failed_checks=[],
                               size_multiplier=size_multiplier)
```

---

## Task 5: Update OrderBuilder to Accept size_multiplier

**Files:**
- Modify: `src/quanterback/adapters/risk/atr_bracket_builder.py:23-42`

### Step 5.1: Update build() signature and implementation

Change:
```python
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

To:
```python
def build(
    self,
    decision: StrategyDecision,
    summary: CondensedSummary,
    account_value: float,
    *,
    size_multiplier: float = 1.0,
) -> BracketOrderSpec:
    if decision.action != "BUY":
        raise ValueError("OrderBuilder called for non-BUY decision")
    entry = summary.price.last_close
    atr = summary.volatility.atr_14
    sl = max(entry - self._sl_m * atr, 0.01)
    tp = entry + self._tp_m * atr
    dollar_size = account_value * self._size_pct * size_multiplier
    qty = max(int(dollar_size // entry), 1)
    return BracketOrderSpec(
        ticker=decision.ticker, side="buy", qty=qty,
        entry_type="market", limit_price=None,
        stop_loss_price=round(sl, 2),
        take_profit_price=round(tp, 2),
    )
```

---

## Task 6: Thread size_multiplier Through Pipeline

**Files:**
- Modify: `src/quanterback/pipeline.py:140`

### Step 6.1: Update order_builder.build() call

Change:
```python
spec = self.order_builder.build(decision, summary, account_value)
```

To:
```python
spec = self.order_builder.build(
    decision, summary, account_value,
    size_multiplier=assessment.size_multiplier,
)
```

---

## Task 7: Wire CompositeRiskGate in CLI

**Files:**
- Modify: `src/quanterback/cli.py:32,91`

### Step 7.1: Add import

Add to the import section:
```python
from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
```

### Step 7.2: Update wire() function

Change:
```python
risk_gate: RiskGate = ThresholdRiskGate()
if config.pdt_protection_enabled:
    risk_gate = PdtAwareRiskGate(
        inner=risk_gate, executor=executor,
        min_equity=config.pdt_min_equity,
        max_day_trades=config.pdt_max_day_trades,
    )
```

To:
```python
risk_gate: RiskGate = CompositeRiskGate()
if config.pdt_protection_enabled:
    risk_gate = PdtAwareRiskGate(
        inner=risk_gate, executor=executor,
        min_equity=config.pdt_min_equity,
        max_day_trades=config.pdt_max_day_trades,
    )
```

---

## Task 8: Update Config Defaults

**Files:**
- Modify: `config/quanterback.toml:12-19`

### Step 8.1: Update [risk.thresholds] section

Change:
```toml
[risk.thresholds]
# Single-stock momentum strategies routinely produce 10-20% MaxDD over a
# 3-year backtest — 8% is unrealistic. 15% is the practical floor.
max_drawdown = 0.25
min_sharpe = 0.5
min_win_rate = 0.40
min_profit_factor = 1.2
min_num_trades = 10
```

To:
```toml
[risk.thresholds]
# SANITY CAPS for catastrophic strategies (used by CompositeRiskGate).
# NOT hard rejection thresholds. CompositeRiskGate uses these only to
# reject truly broken strategies. Real evaluation is relative (vs B&H) and
# uses walk-forward OOS metrics to defeat overfitting.
max_drawdown = 0.50
min_sharpe = -0.5
min_win_rate = 0.0
min_profit_factor = 0.0
min_num_trades = 5
```

---

## Task 9: Update Tests for New Defaults

**Files:**
- Modify: `tests/unit/domain/test_risk.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/adapters/risk/test_threshold_risk_gate.py:22-46`

### Step 9.1: Find and update test_risk.py

Read the file first to find assertions that check defaults. Update any assertion checking `max_drawdown == 0.25` to `== 0.50`, etc.

### Step 9.2: Find and update test_config.py

Read the file and update assertions that check RiskThresholds defaults from the config.

### Step 9.3: Update ThresholdRiskGate tests

The 4 tests in test_threshold_risk_gate.py need adjustment because the defaults changed:

- `test_all_thresholds_passed`: _report() uses max_drawdown=0.05 which is fine. Should still pass.
- `test_max_drawdown_failure_named`: Uses max_drawdown=0.20. With new default 0.50, this would NOT fail anymore. Change the test to either:
  - Set explicit RiskThresholds(max_drawdown=0.10) in the test to expect a fail, OR
  - Use max_drawdown=0.60 in _report() to trigger the new default
  - Recommendation: use RiskThresholds(max_drawdown=0.10) to keep the intent clear
  
- `test_multiple_failures_listed`: Uses max_drawdown=0.20. Same issue. Update to RiskThresholds(max_drawdown=0.10).
- `test_min_num_trades_failure_named`: Uses num_trades=5. With new default min=5, this would NOT fail. Change num_trades to 4.

---

## Task 10: Add VectorizedBacktester Test for New Fields

**Files:**
- Modify: `tests/unit/adapters/risk/test_vectorized_backtester.py`

### Step 10.1: Add a test

Find the test file, then add:

```python
def test_new_fields_populated_on_uptrend() -> None:
    """Verify that new relative + OOS fields are computed."""
    # Use an uptrend fixture (e.g., _smooth_uptrend() if it exists, or create one)
    bt = VectorizedBacktester(FakeHistoricalDataProvider({
        "AAPL": _uptrend_data()  # must have ~200+ bars to allow warmup + split
    }))
    r = bt.run(BacktestRequest(
        ticker="AAPL", strategy="MOMENTUM",
        params={"lookback_days": 20, "momentum_threshold": 0.05},
    ))
    # Verify new fields exist and are reasonable
    assert r.buy_and_hold_return >= 0  # uptrend should have positive return
    assert r.buy_and_hold_max_drawdown >= 0
    assert r.excess_return is not None
    assert r.drawdown_ratio >= 0
    assert r.oos_num_trades >= 0
    assert r.oos_win_rate >= 0
    assert r.oos_max_drawdown >= 0
    assert r.oos_sharpe is not None  # may be negative
    assert r.oos_cumulative_return is not None
    assert r.oos_excess_return is not None
```

(If the test file doesn't have a FakeHistoricalDataProvider or uptrend fixture, adapt as needed based on the file's existing structure.)

---

## Task 11: Add ATRBracketOrderBuilder Tests for size_multiplier

**Files:**
- Modify: `tests/unit/adapters/risk/test_atr_bracket_builder.py`

### Step 11.1: Add two tests

Append to the file:

```python
def test_size_multiplier_half_qty() -> None:
    """size_multiplier=0.5 reduces qty by half."""
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0, position_size_pct=0.05,
    )
    spec_full = builder.build(
        _decision(), _summary(last_close=100, atr_14=2),
        account_value=10_000, size_multiplier=1.0
    )
    spec_half = builder.build(
        _decision(), _summary(last_close=100, atr_14=2),
        account_value=10_000, size_multiplier=0.5
    )
    # 5% × 1.0 × 10000 / 100 = 5
    # 5% × 0.5 × 10000 / 100 = 2.5 → int(2.5) = 2
    assert spec_full.qty == 5
    assert spec_half.qty == 2


def test_size_multiplier_qty_floor_one() -> None:
    """qty is always at least 1."""
    builder = ATRBracketOrderBuilder(
        sl_atr_multiple=1.5, tp_atr_multiple=3.0, position_size_pct=0.05,
    )
    spec = builder.build(
        _decision(), _summary(last_close=100, atr_14=2),
        account_value=1_000, size_multiplier=0.25
    )
    # 5% × 0.25 × 1000 / 100 = 0.125 → max(int(0.125), 1) = 1
    assert spec.qty == 1
```

(Adjust the test to use the existing _decision(), _summary() helpers from the file.)

---

## Task 12: Create CompositeRiskGate Tests

**Files:**
- Create: `tests/unit/adapters/risk/test_composite_risk_gate.py`

### Step 12.1: Write the full test file

```python
from __future__ import annotations

from datetime import date

from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskThresholds


def _report(**kw) -> BacktestReport:
    """Helper: build a BacktestReport with reasonable defaults."""
    base = dict(
        ticker="X", strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=30, win_rate=0.5, max_drawdown=0.15, sharpe=1.0,
        profit_factor=1.5, cumulative_return=0.30, avg_trade_return=0.01,
        avg_bars_held=10.0, trades=[],
        buy_and_hold_return=0.20, buy_and_hold_max_drawdown=0.25,
        excess_return=0.10, drawdown_ratio=0.60,
        oos_num_trades=10, oos_win_rate=0.55, oos_max_drawdown=0.12,
        oos_sharpe=1.2, oos_cumulative_return=0.15, oos_excess_return=0.05,
    )
    base.update(kw)
    return BacktestReport(**base)


def test_sanity_reject_too_few_trades() -> None:
    """Sanity cap: reject if num_trades < min_num_trades."""
    r = _report(num_trades=3)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "sanity_min_num_trades" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_sanity_reject_catastrophic_max_dd() -> None:
    """Sanity cap: reject if max_drawdown > threshold."""
    r = _report(max_drawdown=0.65)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "sanity_max_drawdown" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_sanity_reject_no_oos_evidence() -> None:
    """Sanity cap: reject if oos_num_trades < 2."""
    r = _report(oos_num_trades=0)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "sanity_min_oos_trades" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_sanity_reject_sharpe_too_low() -> None:
    """Sanity cap: reject if sharpe < min_sharpe."""
    r = _report(sharpe=-0.6)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "sanity_min_sharpe" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_relative_reject_strategy_worse_than_bh_and_negative() -> None:
    """Relative gate: reject if excess_return < 0 AND cumulative_return < 0."""
    r = _report(excess_return=-0.05, cumulative_return=-0.10)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "strategy_worse_than_bh_and_negative" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_relative_reject_oos_loss_absolute_and_relative() -> None:
    """Relative gate: reject if oos_excess_return < 0 AND oos_cumulative_return < -0.10."""
    r = _report(oos_excess_return=-0.15, oos_cumulative_return=-0.20)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "oos_loss_relative_and_absolute" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_relative_reject_dd_ratio_too_high() -> None:
    """Relative gate: reject if drawdown_ratio >= 1.2."""
    r = _report(drawdown_ratio=1.25)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert "strategy_dd_worse_than_bh" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_pass_with_high_size_multiplier_for_strong_strategy() -> None:
    """Sizing: strong strategy (high excess, low DD ratio, good OOS Sharpe) gets 1.0x."""
    r = _report(excess_return=0.25, drawdown_ratio=0.4, oos_sharpe=1.8)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert a.passed
    assert a.failed_checks == []
    assert a.size_multiplier > 0.8  # close to 1.0


def test_pass_with_lower_multiplier_for_mediocre_strategy() -> None:
    """Sizing: mediocre strategy gets 0.25x-0.7x."""
    r = _report(excess_return=0.02, drawdown_ratio=0.95, oos_sharpe=0.3)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert a.passed
    assert a.failed_checks == []
    assert 0.25 <= a.size_multiplier <= 0.7


def test_size_multiplier_floor_is_025() -> None:
    """Sizing: size_multiplier is always >= 0.25 for accepted strategies."""
    r = _report(excess_return=-0.10, drawdown_ratio=1.0, oos_sharpe=-0.5)
    # This passes relative gate (excess negative but cumulative_return=0.30 > 0)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert a.passed
    assert a.size_multiplier >= 0.25


def test_size_multiplier_ceiling_is_10() -> None:
    """Sizing: size_multiplier is always <= 1.0."""
    r = _report(excess_return=0.50, drawdown_ratio=0.1, oos_sharpe=3.0)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert a.passed
    assert a.size_multiplier <= 1.0


def test_excess_return_capped_at_plus_030_for_sizing() -> None:
    """Sizing score: excess_return > 0.30 is capped (not all upside counts)."""
    r_huge = _report(excess_return=0.50, drawdown_ratio=0.5, oos_sharpe=0.0)
    r_good = _report(excess_return=0.30, drawdown_ratio=0.5, oos_sharpe=0.0)
    a_huge = CompositeRiskGate().evaluate(r_huge, RiskThresholds())
    a_good = CompositeRiskGate().evaluate(r_good, RiskThresholds())
    # Both should have similar size_multiplier (capped at 0.30)
    assert abs(a_huge.size_multiplier - a_good.size_multiplier) < 0.05


def test_multiple_sanity_failures_listed() -> None:
    """Multiple sanity failures all get listed."""
    r = _report(num_trades=2, max_drawdown=0.60, oos_num_trades=0)
    a = CompositeRiskGate().evaluate(r, RiskThresholds())
    assert not a.passed
    assert set(a.failed_checks) == {
        "sanity_min_num_trades",
        "sanity_max_drawdown",
        "sanity_min_oos_trades",
    }
```

---

## Task 13: Verify Integration Test Still Rejects

**Files:**
- Review: `tests/integration/test_scan_pipeline.py` (specifically test_scenario_3_risk_gate_rejects_excessive_drawdown)

### Step 13.1: Read and understand test_scenario_3

Read the test. Verify that the whipsaw data it creates produces either:
- MaxDD > 0.50 (sanity cap), OR
- drawdown_ratio >= 1.2 (relative gate), OR
- num_trades < 5 (sanity cap), OR
- oos_num_trades < 2 (sanity cap)

If the test data already satisfies one of these conditions, it will still reject (with CompositeRiskGate). If not, you may need to adjust the synthetic data.

### Step 13.2: Run the test and verify it still passes (rejects as expected)

```bash
make test -k test_scenario_3_risk_gate_rejects_excessive_drawdown
```

Expected: PASS (the rejection still happens, just via a different gate condition).

---

## Task 14: Run Full Test Suite and Fix Failures

**Files:**
- Run full suite: `make build && make test && make lint && make typecheck`

### Step 14.1: Build and test

Run:
```bash
make build && make test 2>&1 | tail -50
```

Expected: ~185 tests passing (174 baseline + ~10 new), 0 failures.

### Step 14.2: Fix any failures

If tests fail, identify which ones and apply minimal fixes (likely just assertions on size_multiplier bounds or assertions on default thresholds).

### Step 14.3: Lint and typecheck

```bash
make lint && make typecheck
```

Expected: Clean.

---

## Task 15: Commit

**Files:**
- All modified + new files

### Step 15.1: Stage and commit

```bash
cd /home/kiyoshi/Source/quanterback
git add -A
git commit -m "feat(risk): A+B+D — relative backtest + position sizing + walk-forward

Replaces absolute-threshold RiskGate with a composite mechanism that
actually works for single-stock momentum.

A. Relative metrics (策略 vs buy-and-hold):
   BacktestReport now includes buy_and_hold_return,
   buy_and_hold_max_drawdown, excess_return, drawdown_ratio.

B. Position sizing replaces hard reject:
   RiskAssessment.size_multiplier ∈ [0.25, 1.0]. Strong strategies get
   full size; mediocre get 0.5×; borderline get 0.25×.
   OrderBuilder.build() takes size_multiplier kwarg.
   Hard reject only for truly broken strategies (sanity gates).

D. Walk-forward OOS metrics:
   Train/test split 67/33. OOS metrics computed from trades entering
   in the test slice. Gate uses OOS where possible to defeat overfitting.

RiskThresholds defaults now ARE sanity caps (MaxDD 0.50, Sharpe -0.5,
num_trades 5) — not rejection criteria. Real evaluation is relative +
OOS inside CompositeRiskGate.

Also fixes 3 previously-failing tests from the 0.15→0.25 MaxDD bump.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Execution Path

This plan is structured for **subagent-driven development** with 15 tasks:

1. **Tasks 1-2** (15 min): Domain model changes (BacktestReport + RiskAssessment)
2. **Task 3** (20 min): Backtester metrics computation (relative + OOS)
3. **Task 4** (15 min): CompositeRiskGate implementation
4. **Tasks 5-7** (15 min): Plumbing (OrderBuilder, pipeline, CLI)
5. **Task 8** (5 min): Config defaults
6. **Tasks 9-12** (40 min): Test updates + new tests
7. **Tasks 13-15** (10 min): Verification + commit

**Total:** ~2 hours for complete implementation + testing.

Each task is independently testable (run task tests before moving to the next). This allows parallel work if needed.
