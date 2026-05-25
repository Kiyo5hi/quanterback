# quanterback — LLM-Driven Autonomous Trading Harness (v0 Design)

> **Status**: Draft v1, brainstorming-approved, pending implementation plan
> **Date**: 2026-05-22
> **Target environment**: Alpaca Paper Trading
> **Repo layout**: monorepo under `/home/kiyoshi/Source/quanterback/`

---

## 1. 目标与边界

构建一个把 LLM 当作"高层策略选择器"接入 Alpaca Paper Trading 的自主交易 harness。系统由三层组成：

1. **Data Layer** — 拉取行情，压缩成 LLM 友好的紧凑 summary
2. **LLM Decision Layer** — LLM 输出一个严格 JSON 的策略决策（不预测价格、不下单）
3. **Execution & Risk Barrier Layer** — 本地代码拦截 LLM JSON，跑 3 年快速回测验证；通过则以 Bracket Order 提交，止盈止损是交易所级硬护栏

### 1.1 LLM 的角色（非常重要的约束）

- LLM **不预测**价格、**不直接**下单、**不**访问已有持仓
- LLM 只回答"对这只票，现在用什么策略参数开仓 / 不开仓"
- LLM 输出从根上经过 JSON schema 强制（Claude structured output + Pydantic 双层校验）
- "permanent exchange-level Stop-Loss and Take-Profit bounds" 由代码层基于 ATR 自动计算，**不**交给 LLM

### 1.2 v0 范围 vs 未来扩展

| 维度 | v0 范围 | 未来扩展 |
|---|---|---|
| 触发节奏 | Watchlist + cron 定时扫描 | v1: multi-source（Telegram 用户触发 + 财报日历事件）+ priority queue |
| 策略类型 | 仅 Momentum | v1: + Mean Reversion；v2: + Grid（需独立 Execution 框架）|
| LLM 输出范围 | strategy + params；SL/TP/仓位由代码计算 | 接口已支持 LLM 输出全部参数 |
| 干预通道 | Telegram 被动通知 + freeze/halt 命令 | v1: 用户触发 scan；同步 approve 决策 |
| 交易品种 | 美股（Alpaca paper） | v2: 实盘、可能扩展期权/期货 |

完整路线图见第 8 节。

---

## 2. 整体架构与数据流

### 2.1 进程模型

v0 采用 **"cron-invoked scan + 常驻 control-bot"** 双进程模型，两个进程通过 SQLite 解耦：

- `scan` 进程：每次 cron 触发跑完一次完整 watchlist 扫描后退出
- `control-bot` 进程：长驻 Telegram long-polling，写入 SQLite `system_state` 表
- 共享 SQLite 文件（启用 WAL 模式以支持并发读写）

### 2.2 数据流图

```
┌─────────────────────────────────────────────────────────────────────┐
│ ① cron trigger:  `quanterback scan`                                 │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ ② SystemStateCheck  (read SQLite `system_state`)                    │
│    halted  → exit immediately                                       │
│    frozen  → continue but Executor switches to dry-run mode         │
│    normal  → continue                                               │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ ③ EventSource  (interface)                                          │
│    v0:  WatchlistEventSource                                        │
│    v1+: UserTriggerEventSource (TG), EarningsCalendarEventSource    │
│    Yields ScanEvent { ticker, source, priority, requested_at }      │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌── for each ScanEvent  (sequential, fail-isolated per ticker) ──────┐
│                                                                     │
│  ┌─ Layer 1: Data ────────────────────────────────────────────┐    │
│  │ DataProvider (yfinance)  → raw OHLCV (1y daily + 30d 1h)   │    │
│  │ Summarizer               → CondensedSummary                │    │
│  └────────────────────────────────────────────────────────────┘    │
│                            ▼                                        │
│  ┌─ Layer 2: LLM Decision ────────────────────────────────────┐    │
│  │ LLMStrategist (Claude)  → StrategyDecision                 │    │
│  │   {action, ticker, strategy, params, rationale, confidence}│    │
│  │ JSON-schema validated                                      │    │
│  │ action=PASS → short-circuit → notify + persist + done      │    │
│  └────────────────────────────────────────────────────────────┘    │
│                            ▼  (BUY only)                            │
│  ┌─ Layer 2.5: ApprovalGate  (v0 = NoOpApprovalGate) ────────┐     │
│  │ v1+: TelegramApprovalGate (sync approve via TG)            │     │
│  └────────────────────────────────────────────────────────────┘     │
│                            ▼                                        │
│  ┌─ Layer 3: Risk Barrier  (single-direction gate) ──────────┐    │
│  │ 1. PositionStateCheck   reject if ticker has open lifecycle│    │
│  │ 2. Backtester           BacktestReport using LLM's params  │    │
│  │ 3. RiskGate             evaluate against RiskThresholds    │    │
│  │ 4. OrderBuilder         entry/SL/TP via ATR; sizing via %  │    │
│  │ 5. Executor                                                │    │
│  │    normal → Alpaca.submit_bracket_order                    │    │
│  │    frozen → record as "frozen_skip", DO NOT call Alpaca    │    │
│  └────────────────────────────────────────────────────────────┘    │
│                            ▼                                        │
│  ┌─ Notifier (interface, async, fire-and-forget) ────────────┐    │
│  │ v0: TelegramNotifier                                       │    │
│  │ Notifier failure NEVER fails the main chain                │    │
│  └────────────────────────────────────────────────────────────┘    │
│                            ▼                                        │
│  ┌─ StateStore (SQLite, WAL mode, repository pattern) ───────┐    │
│  │ scan_runs / decisions / backtests / orders / positions /   │    │
│  │ system_state / notifications                               │    │
│  └────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘

Outside cron lifecycle — independent long-polling process:
┌─────────────────────────────────────────────────────────────────────┐
│ ④ TelegramControlBot                                                │
│    Listens: /freeze /unfreeze /halt /unhalt /status                 │
│    Writes:  SQLite system_state table                               │
│    Decoupled from scan process; if it crashes, cron scans still run │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 核心架构决策

| # | 决策 | 取值 | 理由 |
|---|---|---|---|
| 1 | PASS 短路 | 启用 | LLM 说不买就不跑回测，节省 token + backtest 时间；PASS 决策仍然落库 + 推送，信息无丢失 |
| 2 | 单只票失败隔离 | 启用 | 任一只票任一层失败只 reject 该票，记录 `rejected_reason`，下一只票继续 |
| 3 | frozen 模式下 LLM 仍跑 | 是（dry-run）| freeze 期间仍能观察"如果不 freeze 系统会做什么"；Executor 是唯一被切断的环节 |
| 4 | ApprovalGate 抽象 | 接口留位，v0 = NoOp | 未来开启 Telegram 同步 approve 不动其他模块 |
| 5 | ControlBot 与 scan 解耦 | 是，通过 SQLite system_state | ControlBot 崩了 cron 仍能用上次 state；cron 崩了 ControlBot 不受影响 |
| 6 | Notifier 失败不阻断主链路 | 是（fire-and-forget）| Telegram API 抖动不能影响交易；失败落 `notifications` 表，下次 scan 启动时重发 |
| 7 | LLM 是 strategist 不是 predictor | 输出 strategy + params，**不** 输出 SL/TP/size | spec 原约束，且 ATR-based SL/TP 可审计、有金融意义 |

---

## 3. 模块接口（Python Protocol 签名）

所有可演进的能力都用 `typing.Protocol` 定义，v0 只提供一个具体实现，但 spec 列出已识别的 v1+ 替代实现。

**核心原则**：模块只依赖 interface，不 import 具体实现；所有具体类的实例化集中在唯一的 `wire()` 函数（composition root）。

### 3.1 Trigger & State Layer

```python
class ScanEvent(BaseModel):
    ticker: str
    source: str                                   # "watchlist" | "user_trigger" | ...
    priority: int = 0
    requested_at: datetime

class EventSource(Protocol):
    def stream(self) -> Iterable[ScanEvent]: ...
```
- v0: `WatchlistEventSource(path: Path)`
- v1+: `UserTriggerEventSource`(TG), `EarningsCalendarEventSource`, `CompositePrioritizedEventSource`

```python
class SystemState(BaseModel):
    mode: Literal["normal", "frozen", "halted"]
    updated_at: datetime
    updated_by: str

class SystemStateService(Protocol):
    def get_current(self) -> SystemState: ...
    def set(self, mode: str, reason: str, actor: str) -> None: ...
```
- v0: `SqliteSystemStateService` —— scan 读，ControlBot 写

```python
class ControlCommand(BaseModel):
    command: Literal["freeze", "unfreeze", "halt", "unhalt", "status"]
    actor: str
    received_at: datetime

class ControlChannel(Protocol):
    def listen(self) -> Iterable[ControlCommand]: ...    # blocking, yields on receipt
```
- v0: `TelegramControlChannel`(long-polling)
- v1+: `CLIControlChannel`, `WebUIControlChannel`

### 3.2 Data Layer

```python
class PriceWindow(BaseModel):
    ticker: str
    daily: pd.DataFrame      # ~1y OHLCV daily
    hourly: pd.DataFrame     # ~30d OHLCV hourly
    as_of: datetime

class DataProvider(Protocol):
    def fetch(self, ticker: str) -> PriceWindow: ...
```
- v0: `YFinanceProvider`（本地 Parquet 缓存）
- v1+: `AlpacaMarketDataProvider`, `PolygonProvider`, `CompositeFallbackProvider`

```python
class Summarizer(Protocol):
    def summarize(self, window: PriceWindow) -> CondensedSummary: ...
```
- v0: `RuleBasedSummarizer`（确定性技术指标计算）
- v1+: `LLMAssistedSummarizer`（叠 LLM 压缩新闻/财报）

### 3.3 Decision Layer

```python
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class ChatResponse(BaseModel):
    content: str
    model: str
    usage: dict

class LLMClient(Protocol):
    """Pure I/O abstraction; knows nothing about trading."""
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse: ...
```
- v0: `ClaudeClient`（anthropic SDK）
- v1+: `OpenAIClient`, `OllamaClient`, `VLLMClient`, `CachedLLMClient`(decorator)

```python
class LLMStrategist(Protocol):
    """Business abstraction; doesn't have to be LLM-backed."""
    def decide(self, summary: CondensedSummary) -> StrategyDecision: ...
```
- v0: `PromptedLLMStrategist(client: LLMClient, prompt_template: Path)`
- v1+: `RuleBasedStrategist`(纯规则 fallback), `FinetunedLLMStrategist`, `EnsembleStrategist`

```python
class ApprovalResult(BaseModel):
    approved: bool
    reason: str
    approver: str | None = None

class ApprovalGate(Protocol):
    def review(self, decision: StrategyDecision) -> ApprovalResult: ...
```
- v0: `NoOpApprovalGate`
- v1+: `TelegramApprovalGate`, `MultiSignApprovalGate`

### 3.4 Risk Barrier Layer

```python
class OpenLifecycle(BaseModel):
    ticker: str
    order_id: str
    state: Literal["pending", "filled", "bracket_active"]
    opened_at: datetime

class PositionStateService(Protocol):
    def has_open_lifecycle(self, ticker: str) -> bool: ...
    def get_open(self, ticker: str) -> OpenLifecycle | None: ...
```
- v0: `SqliteAlpacaSyncedPositionState`（启动时与 Alpaca 双向对账）

```python
class BacktestRequest(BaseModel):
    ticker: str
    strategy: str
    params: dict
    lookback_years: int = 3

class Backtester(Protocol):
    def run(self, request: BacktestRequest) -> BacktestReport: ...
```
- v0: `VectorizedBacktester`（pandas + numpy，零外部库依赖）
- v1+: `VectorbtBacktester`, `BacktraderBacktester`, `WalkForwardBacktester`

```python
class RiskThresholds(BaseModel):
    max_drawdown: float = 0.08
    min_sharpe: float = 0.5
    min_win_rate: float = 0.40
    min_profit_factor: float = 1.2
    min_num_trades: int = 30

class RiskAssessment(BaseModel):
    passed: bool
    failed_checks: list[str]

class RiskGate(Protocol):
    def evaluate(self, report: BacktestReport, thresholds: RiskThresholds) -> RiskAssessment: ...
```
- v0: `ThresholdRiskGate`（**全票通过**，任一项失败即 reject）
- v1+: `AdaptiveRiskGate`, `PortfolioLevelRiskGate`

```python
class BracketOrderSpec(BaseModel):
    ticker: str
    side: Literal["buy"]
    qty: int
    entry_type: Literal["market", "limit"]
    limit_price: float | None
    stop_loss_price: float
    take_profit_price: float

class OrderBuilder(Protocol):
    def build(
        self,
        decision: StrategyDecision,
        summary: CondensedSummary,
        account_value: float,
    ) -> BracketOrderSpec: ...
```
- v0: `ATRBracketOrderBuilder`（SL = entry − k_sl·ATR，TP = entry + k_tp·ATR）
- v1+: `KellySizedOrderBuilder`, `VolatilityScaledOrderBuilder`

```python
class ExecutionResult(BaseModel):
    submitted: bool
    order_id: str | None
    error: str | None
    raw_response: dict

class Executor(Protocol):
    def submit(self, spec: BracketOrderSpec, *, dry_run: bool) -> ExecutionResult: ...
```
- v0: `AlpacaPaperExecutor`
- v1+: `AlpacaLiveExecutor`, `IBKRExecutor`, `InMemorySimulatorExecutor`（测试用）
- `dry_run=True`（frozen 模式）：构造 spec 并落库，不调 broker

### 3.5 Cross-cutting

```python
class NotificationEvent(BaseModel):
    kind: Literal["decision","backtest","order","fill","scan_summary","error"]
    payload: dict
    timestamp: datetime

class Notifier(Protocol):
    def push(self, event: NotificationEvent) -> None:
        """MUST NOT raise. Internal failures caught and logged."""
```
- v0: `TelegramNotifier`
- v1+: `SlackNotifier`, `EmailNotifier`, `MultiplexNotifier`

```python
class StateStore(Protocol):
    """Repository pattern. v0 = raw SQL + dataclass, no ORM."""
    def insert_scan_run(self, run: ScanRun) -> int: ...
    def insert_decision(self, decision: PersistedDecision) -> int: ...
    def insert_backtest(self, report: PersistedBacktest) -> int: ...
    def insert_order(self, order: PersistedOrder) -> int: ...
    def update_position(self, position: PersistedPosition) -> int: ...
    def insert_notification(self, n: PersistedNotification) -> int: ...
    def query_open_lifecycles(self) -> list[OpenLifecycle]: ...
    def query_recent_decisions(self, ticker: str, limit: int) -> list[PersistedDecision]: ...
```
- v0: `SqliteStore`（单文件，WAL 模式）
- v1+: `PostgresStore`, `DuckDBStore`

### 3.6 Composition Root

```python
def wire(config: AppConfig) -> ScanPipeline:
    """The ONLY place where concrete classes are instantiated."""
    state_store    = SqliteStore(config.db_path)
    notifier       = TelegramNotifier(config.tg_token, config.tg_chat_ids)
    data_provider  = YFinanceProvider(cache_dir=config.cache_dir)
    summarizer     = RuleBasedSummarizer()
    llm_client     = ClaudeClient(api_key=config.anthropic_key, model=config.llm_model)
    strategist     = PromptedLLMStrategist(llm_client, prompt_template_path=config.prompt_template_path)
    approval_gate  = NoOpApprovalGate()
    backtester     = VectorizedBacktester(data_provider)
    risk_gate      = ThresholdRiskGate()
    order_builder  = ATRBracketOrderBuilder(
        sl_atr_multiple=config.sl_atr_multiple,
        tp_atr_multiple=config.tp_atr_multiple,
        position_size_pct=config.position_size_pct,
    )
    executor       = AlpacaPaperExecutor(config.alpaca_key, config.alpaca_secret)
    position_state = SqliteAlpacaSyncedPositionState(state_store, executor)
    event_source   = WatchlistEventSource(config.watchlist_path)
    system_state   = SqliteSystemStateService(state_store)

    return ScanPipeline(
        event_source, data_provider, summarizer,
        strategist, approval_gate, position_state,
        backtester, risk_gate, order_builder,
        executor, notifier, state_store, system_state,
        thresholds=config.risk_thresholds,
    )
```

`ScanPipeline.run()` 是主循环（顺序处理 ScanEvent，调用各 interface），它不知道具体类是谁。测试时把任意依赖换成 fake/stub 即可。

---

## 4. 数据 Schema

### 4.1 CondensedSummary（LLM 输入）

紧凑、self-explanatory、token 友好。LLM 实际看到的是 `to_prompt_text()` 渲染的紧凑文本。

```python
class TrendRegime(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"

class VolatilityRegime(str, Enum):
    LOW = "low"; NORMAL = "normal"; HIGH = "high"; EXTREME = "extreme"

class VolumeRegime(str, Enum):
    BELOW_AVG = "below_avg"; NORMAL = "normal"; ELEVATED = "elevated"; EXTREME = "extreme"

class PriceSnapshot(BaseModel):
    last_close: float
    return_1d: float; return_5d: float; return_20d: float; return_60d: float
    pct_from_52w_high: float    # < 0 表示距 52w 高点的折扣
    pct_from_52w_low: float

class MovingAverages(BaseModel):
    sma_20: float; sma_50: float; sma_200: float
    pct_above_sma_20: float; pct_above_sma_50: float; pct_above_sma_200: float
    alignment: Literal["bullish", "bearish", "mixed"]    # SMA20>50>200 = bullish

class VolatilityProfile(BaseModel):
    realized_vol_20d_annualized: float    # 年化 = 日收益 stddev × √252
    atr_14: float                          # 平均真实波幅，14 日 Wilder 平滑
    atr_pct_of_price: float                # ATR / price
    regime: VolatilityRegime

class VolumeProfile(BaseModel):
    last_volume: int
    avg_volume_20d: int
    volume_ratio: float                    # 当日 / 20d 均值
    regime: VolumeRegime

class TechnicalIndicators(BaseModel):
    rsi_14: float                          # 相对强弱指数 0-100
    macd_signal: Literal["bullish_cross", "bearish_cross", "none"]

class FundamentalLite(BaseModel):
    days_to_next_earnings: int | None
    market_cap_bucket: Literal["large", "mid", "small", "unknown"]

class CondensedSummary(BaseModel):
    ticker: str
    as_of: datetime
    price: PriceSnapshot
    moving_averages: MovingAverages
    volatility: VolatilityProfile
    volume: VolumeProfile
    technicals: TechnicalIndicators
    fundamentals: FundamentalLite
    trend_regime: TrendRegime

    def to_prompt_text(self) -> str: ...
```

**LLM 看到的 prompt 文本样例**：

```
[AAPL @ 2026-05-22 14:30 ET]
Price: $185.42 (+0.8% 1d / +3.2% 5d / +8.5% 20d / -2.1% 60d)
52w range: -4.2% from high, +35.1% from low
Trend: UPTREND  (above SMA20 +2.1%, SMA50 +4.3%, SMA200 +12.8%)
                SMA stack: 20 > 50 > 200 (bullish alignment)
Volatility: NORMAL  (20d realized 22% ann.; ATR14 = $3.40 = 1.83% of price)
Volume: ELEVATED  (today 1.6× 20d avg)
RSI(14): 58.3
MACD: bullish_cross within last 5 days
Earnings: 38 days away
Market cap: large
```

### 4.2 StrategyDecision（LLM 输出）

```python
class MomentumParams(BaseModel):
    lookback_days: int = Field(..., ge=5, le=60,
        description="动量回看窗口")
    momentum_threshold: float = Field(..., ge=0.0, le=0.30,
        description="lookback 窗口内累计收益率最低要求")

class StrategyDecision(BaseModel):
    action: Literal["BUY", "PASS"]
    ticker: str
    strategy: Literal["MOMENTUM"]              # v0 只支持 MOMENTUM
    params: MomentumParams | None = None       # action=PASS 时为 None
    rationale: str = Field(..., min_length=20, max_length=600,
        description="决策理由，用于审计与事后分析")
    confidence: float = Field(..., ge=0.0, le=1.0,
        description="LLM 主观置信度；v0 仅记录")

    @model_validator(mode="after")
    def params_required_for_buy(self):
        if self.action == "BUY" and self.params is None:
            raise ValueError("BUY action requires non-null params")
        return self
```

**Schema 强制约束**：LLM 调用使用 structured output（response_schema），LLM 从根上无法输出非法 JSON。Pydantic 客户端再次校验防御 schema 失效的极端情况。

### 4.3 BacktestReport（回测产出）

```python
class TradeRecord(BaseModel):
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    return_pct: float
    bars_held: int
    exit_reason: Literal["stop_loss", "take_profit", "timeout"]

class BacktestReport(BaseModel):
    ticker: str
    strategy: str
    params: dict
    period_start: date
    period_end: date

    # 核心风控指标（RiskGate 消费）
    num_trades: int
    win_rate: float
    max_drawdown: float
    sharpe: float
    profit_factor: float

    # 辅助指标
    cumulative_return: float
    avg_trade_return: float
    avg_bars_held: float

    # 完整交易序列（用于推送预览 + 审计）
    trades: list[TradeRecord]
```

**RiskGate 阈值定义**（v0 默认值）：

| 字段 | 默认阈值 | 含义 | 不达标 = |
|---|---|---|---|
| `max_drawdown` | **< 0.08** | 历史最大回撤 8% | 历史最坏跌过 8%，再险拒绝 |
| `sharpe` | **> 0.5** | 风险调整年化收益 | 收益太"颠簸"，性价比不行 |
| `win_rate` | **> 0.40** | 盈利交易占比 40% | 太低 = 押大反弹，不稳健 |
| `profit_factor` | **> 1.2** | 总盈利 / 总亏损 | < 1 长期不赚钱 |
| `num_trades` | **≥ 30** | 统计样本数 | < 30 笔疑似过拟合 |

**全票通过**：任一项失败即 reject，记录到 `RiskAssessment.failed_checks`。

### 4.4 SQLite Schema

```sql
-- 每次 cron scan 的元信息
scan_runs(
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  source TEXT,                       -- "cron" | "manual" | ...
  tickers_processed INTEGER,
  errors_count INTEGER DEFAULT 0
);

-- LLM 决策（包含 PASS），一只票一行
decisions(
  id INTEGER PRIMARY KEY,
  scan_run_id INTEGER REFERENCES scan_runs(id),
  ticker TEXT NOT NULL,
  summary_json TEXT NOT NULL,        -- CondensedSummary serialized
  decision_json TEXT NOT NULL,       -- StrategyDecision serialized
  llm_model TEXT NOT NULL,
  llm_usage_json TEXT,               -- tokens, cost
  rejected_reason TEXT,              -- nullable; reason if rejected anywhere downstream
  created_at TEXT NOT NULL
);

-- 回测报告
backtests(
  id INTEGER PRIMARY KEY,
  decision_id INTEGER REFERENCES decisions(id),
  report_json TEXT NOT NULL,         -- BacktestReport serialized
  passed INTEGER NOT NULL,           -- bool
  failed_checks TEXT,                -- CSV of failed threshold names
  created_at TEXT NOT NULL
);

-- 提交到 Alpaca 的订单
orders(
  id INTEGER PRIMARY KEY,
  decision_id INTEGER REFERENCES decisions(id),
  backtest_id INTEGER REFERENCES backtests(id),
  bracket_spec_json TEXT NOT NULL,
  alpaca_order_id TEXT,
  submitted_at TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 0,
  raw_response_json TEXT
);

-- 持仓生命周期（一只票同时只有一个 active row）
positions(
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL,
  order_id INTEGER REFERENCES orders(id),
  state TEXT NOT NULL,               -- "pending" | "filled" | "bracket_active" | "closed"
  entry_price REAL,
  sl REAL, tp REAL, qty INTEGER,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  exit_reason TEXT                   -- "stop_loss" | "take_profit" | "manual"
);
CREATE UNIQUE INDEX idx_one_active_per_ticker
  ON positions(ticker) WHERE state != 'closed';

-- 系统状态（ControlBot 写、scan 读）
system_state(
  id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL,                -- "normal" | "frozen" | "halted"
  reason TEXT,
  actor TEXT,                        -- telegram user id
  updated_at TEXT NOT NULL
);

-- 推送事件审计
notifications(
  id INTEGER PRIMARY KEY,
  event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  sent_at TEXT,
  sent_ok INTEGER DEFAULT 0,
  retry_count INTEGER DEFAULT 0,
  error TEXT
);
```

每张表 v0 用 raw SQL + dataclass mapping，**不引入 ORM**。13 张以内的 schema 不值得 ORM 学习成本，原生 SQL 更可控，迁移 Postgres 也更直接。

---

## 5. 配置策略（TOML 分层）

### 5.1 加载优先级（低 → 高，后者覆盖前者）

```
1. 内嵌默认值       (Python dataclass with defaults)
2. 项目配置        ./config/quanterback.toml             (git-tracked)
3. 本地覆盖        ./config/quanterback.local.toml       (gitignored)
4. 环境变量        仅 secrets (4 个)
5. CLI flags       临时覆盖, e.g. `--dry-run`
```

**env vars 严格限于以下 4 个 secret**：
- `ANTHROPIC_API_KEY`
- `ALPACA_API_KEY`
- `ALPACA_SECRET`
- `TELEGRAM_BOT_TOKEN`

### 5.2 `config/quanterback.toml` 示例（提交进 git）

```toml
[scan]
# Watchlist 路径
watchlist_path = "/config/watchlist.txt"

[position]
# 单笔仓位占账户净值百分比. 调高 = 单笔暴露更大. 默认 5% 配合 5 只票上限 = 最多 25% 总暴露
position_size_pct = 0.05

# 全账户同时持仓上限. 调高 = 集中度风险更高
max_concurrent_positions = 5

[risk.sl_tp]
# 止损距离 = entry - sl_atr_multiple × ATR. 调小易被噪音洗掉,调大撑不住护栏
sl_atr_multiple = 2.0
# 止盈距离 = entry + tp_atr_multiple × ATR. 与 SL 比例决定 risk/reward
tp_atr_multiple = 4.0

[risk.thresholds]
# 任一项不达标即 reject (全票通过). 见 Section 4.3 详细解释
max_drawdown = 0.08
min_sharpe = 0.5
min_win_rate = 0.40
min_profit_factor = 1.2
min_num_trades = 30

[backtest]
# 回测期年数. 调大覆盖更多市场周期,但 yfinance 数据精度对久远历史下降
lookback_years = 3

[llm]
# Claude 模型选择. Sonnet 4.6 是性价比最优;Opus 4.7 推理更强但贵 ~5×
model = "claude-sonnet-4-6"
temperature = 0.0
prompt_template_path = "/config/prompts/momentum_strategist.md"

[data]
# yfinance 数据本地 Parquet 缓存
cache_dir = "/data/cache"
cache_ttl_hours = 4

[telegram]
# 通知接收 chat_id (允许多个,fan-out)
chat_ids = ["123456789"]
# Notifier 失败重试窗口
retry_window_hours = 1

[storage]
db_path = "/data/quanterback.sqlite"
```

### 5.3 `crontab`（supercronic 读取）

```
# 美东时间每小时跑一次（容器内时区设为 America/New_York）
0 9-16 * * 1-5    quanterback scan
# 开盘后 30 min 多跑一次以捕捉开盘方向
30 9 * * 1-5      quanterback scan
```

---

## 6. 容器化部署

### 6.1 目录布局

```
quanterback/
├── docker/
│   ├── Dockerfile              # python:3.12-slim + uv + supercronic
│   ├── crontab                 # supercronic 读取
│   └── entrypoint.sh
├── docker-compose.yml
├── config/
│   ├── quanterback.toml        # git
│   ├── quanterback.local.toml  # gitignored
│   ├── watchlist.txt           # git (default starter list)
│   └── prompts/
│       └── momentum_strategist.md
├── data/                       # gitignored, mount to containers
│   ├── quanterback.sqlite
│   └── cache/
├── .env                        # gitignored, secrets only
├── Makefile
├── pyproject.toml              # uv-managed
├── src/quanterback/
│   ├── __init__.py
│   ├── cli.py                  # entrypoint with wire()
│   ├── pipeline.py             # ScanPipeline orchestration
│   ├── config.py               # TOML loader + AppConfig dataclass
│   ├── interfaces/             # all Protocol definitions
│   ├── adapters/               # concrete implementations per port
│   │   ├── data/yfinance_provider.py
│   │   ├── data/rule_based_summarizer.py
│   │   ├── decision/claude_client.py
│   │   ├── decision/prompted_strategist.py
│   │   ├── risk/vectorized_backtester.py
│   │   ├── risk/threshold_risk_gate.py
│   │   ├── risk/atr_bracket_builder.py
│   │   ├── exec/alpaca_paper_executor.py
│   │   ├── store/sqlite_store.py
│   │   ├── notify/telegram_notifier.py
│   │   └── control/telegram_control_channel.py
│   └── domain/                 # DTOs (BaseModel)
└── tests/
    ├── fakes/                  # fake implementations per Protocol
    ├── fixtures/               # cached real-data slices for integration
    ├── unit/
    └── integration/
```

### 6.2 `docker-compose.yml`

```yaml
services:
  scan:
    build: ./docker
    command: supercronic /app/docker/crontab
    volumes:
      - ./data:/data
      - ./config:/config:ro
    env_file: .env
    environment:
      - TZ=America/New_York
    restart: unless-stopped

  control-bot:
    build: ./docker
    command: quanterback control-bot
    volumes:
      - ./data:/data
      - ./config:/config:ro
    env_file: .env
    restart: unless-stopped
```

### 6.3 `Makefile`（开发常用命令）

```makefile
.PHONY: build up down scan-once test logs shell

build:    ; docker compose build
up:       ; docker compose up -d
down:     ; docker compose down
scan-once:; docker compose run --rm scan quanterback scan
test:     ; docker compose run --rm scan pytest -v
logs:     ; docker compose logs -f
shell:    ; docker compose run --rm scan bash
```

宿主机只需要 Docker。所有 Python / 依赖 / cron 都在容器里。

---

## 7. 测试策略

### 7.1 三类测试

| 类型 | 数据 | 范围 | 触发 |
|---|---|---|---|
| **Unit** | 合成 OHLCV（手写已知答案的序列）| 单模块，依赖全 fake | 每次 commit |
| **Integration** | 缓存的真实数据切片（`tests/fixtures/`，git-tracked，每只票 ~50KB Parquet）| 跨模块，仅 broker 用 InMemorySimulator | 每次 commit |
| **Manual smoke** | 真实 yfinance + Alpaca paper | 全链路，包括外部服务 | 手动 `make scan-once` |

**禁止**单测里真实调用 Alpaca / yfinance / Claude API（用 fake）。

### 7.2 TDD 工作流

对所有新模块（特别是 Backtester / Summarizer / RiskGate / OrderBuilder）采用 Red-Green-Refactor：

1. **Red**: 写测试 "给定合成 OHLCV X，回测应得到 MaxDD = Y"（测试失败，因为还没实现）
2. **Green**: 写最少代码让测试通过
3. **Refactor**: 测试还通过的前提下，优化结构

理由：金融逻辑有大量 silent bug 风险（算错了但不报错）。先写"期望输出"再写实现，避免"边写边改公式凑结果"。

### 7.3 集成测试场景（必须覆盖）

1. ScanEvent → LLM 输出 BUY → 回测通过 → Bracket Order 提交（happy path）
2. ScanEvent → LLM 输出 PASS → 短路落库（PASS short-circuit）
3. ScanEvent → LLM 输出 BUY → 回测 reject（MaxDD 超限）→ 不下单（RiskGate path）
4. ScanEvent → LLM 输出无法解析的 JSON → schema reject → 不进入 Risk Barrier
5. ScanEvent → 该票已有 open lifecycle → PositionStateCheck reject
6. ScanEvent in frozen mode → BUY 决策 + 回测通过 → dry_run，不调用 broker
7. ScanEvent in halted mode → scan 进程立即退出
8. yfinance 抛异常 → 该票 rejected_reason 记 exception → 下一只票继续
9. Notifier push 失败 → 不影响主流程 → 写入 notifications.retry_count，下次 scan 重发
10. ControlBot 收到 /freeze → system_state 更新 → 下一次 scan 进入 dry-run

### 7.4 回测引擎验证

用合成数据验证回测算法本身：
- 已知一段连续上涨序列 → 验证 MaxDD = 0、win_rate = 1.0、profit_factor = ∞
- 已知一段震荡序列 → 验证 win_rate ≈ 期望值
- 已知一段单 trade 序列 → 验证 entry/exit/return_pct 准确

---

## 8. 路线图

### 8.1 v1（接口已留位，无需大改）

| 增量 | 修改范围 |
|---|---|
| Mean Reversion 策略 | 新增 `MeanReversionParams`，更新 `StrategyDecision.strategy` Literal，新增策略代码；Backtester/RiskGate/OrderBuilder 不动 |
| Multi-source EventSource + priority | 新增 `UserTriggerEventSource`(TG), `EarningsCalendarEventSource`, `CompositePrioritizedEventSource`。Approach 从纯 cron 升级到 daemon |
| Telegram 同步 approve | `NoOpApprovalGate` → `TelegramApprovalGate`；其他模块不动 |
| Telegram 用户触发 scan | 扩展 ControlBot 接收 `/scan TICKER` 命令，写入 `pending_user_triggers` 表，cron 启动时读取 |
| 换 LLM provider | 新增 `OpenAIClient`/`OllamaClient`，改一行 wire() |
| 切换到 vectorbt 回测 | 新增 `VectorbtBacktester`，改一行 wire() |
| 缓存 LLM 调用 | 新增 `CachedLLMClient(decorator)` 包装现有 client |

### 8.2 v2（需要架构变动）

| 主题 | 为什么需要架构变动 |
|---|---|
| Grid 策略 | 与单笔 Bracket Order 冲突，需要"多腿订单状态机" |
| 实盘交易 | 表面上换 `AlpacaLiveExecutor` 是一行，但需要合规、资金安全、断网应急、人工 kill switch、监管报告，整套独立 review |
| Portfolio-level RiskGate | 当前 RiskGate 只看单票；组合相关性/行业集中度/Beta 需要全账户 view |
| RL / fine-tune 决策层 | 积累足够 decision + outcome 后，可以用历史 fine-tune 专属 strategist |

---

## 9. 已识别的开放问题

设计阶段已发现但 v0 不深究的问题，标记以便实现阶段对齐：

1. **财报日历数据源**：`days_to_next_earnings` v0 用 `yfinance.calendar()`，可靠性一般。实现阶段需要降级处理（拿不到时设 None）。
2. **ATR 计算公式**：True Range 有 Wilder smoothing 和 simple moving average 两个流派。v0 选 Wilder（更主流）。
3. **PriceWindow 时区**：daily 是 trading-day 概念，hourly 是 UTC vs ET 概念。统一存 UTC，渲染时转 ET。
4. **PASS 反向校验**：v0 不做。LLM 说 PASS 但回测显示"该票该策略历史很赚"时是否要 flag。留 v1 思考。
5. **SQLite WAL 配置细节**：`journal_mode=WAL`, `synchronous=NORMAL`（实现阶段确认 ControlBot 写频率下是否够安全）。
6. **yfinance 容器内 SSL / user-agent**：实现阶段验证容器内 yfinance 是否需要配置 user-agent。
7. **Telegram chat_id 白名单**：v0 默认单一 chat_id 配置。多用户/多 chat_id 留 v1。
8. **回测的 slippage / commission 模型**：v0 假设 zero slippage、zero commission（paper trading）。实盘前必须加。
9. **Backtester 使用何种入场/出场逻辑**：v0 假定 "next bar open" 入场，避免 look-ahead bias。SL/TP 在每根 bar 内判断触发（intra-bar fill）。
10. **`AppConfig` dataclass 与 TOML 之间的映射**：实现阶段决定用 pydantic-settings 还是手写 loader。
11. **PDT / GFV 实盘合规（v2 主题）**：v0/v1 paper trading 不受 FINRA PDT 规则约束。但 v2 实盘时,若本金 < $25,000:
    - **margin account**: 5 工作日内 ≥4 笔 day trade 会被标记 Pattern Day Trader,账户锁 90 天
    - **cash account**: 不受 PDT,但 settlement 期内重复买卖产生 Good Faith Violation,3 次 GFV → 90 天 cash-only 限制
    - Momentum 策略本身是 swing trade(多日持仓),正常情况不算 day trade
    - 但入场当天 SL 被触发(gap down 等)算 day trade,会累计 PDT 计数
    - v2 实盘前 RiskGate 需新增 "PDT-aware check"(读 Alpaca 过去 5 工作日 day_trade_count) + "settlement-aware check"(若 cash account)
    - 已识别但不在 v0 范围内,纳入 §8.2 v2 路线图的"实盘交易"条目

---

## 10. 实现的下一步

1. 创建实现计划（`docs/superpowers/plans/...`），按模块拆分 TDD 节奏
2. `pyproject.toml` + 依赖（anthropic, alpaca-py, yfinance, pydantic, pandas, numpy, pytest, python-telegram-bot, tomllib）
3. Dockerfile + docker-compose 上跑
4. 按 ports → 一个一个 adapter 实现 + 测试

实现阶段必须遵守的约束（已固化为 memory）：
- 所有模块依赖 Protocol，不依赖具体类
- 量化具体数值由代码硬编码默认 + TOML 可调
- 测试用 fake/stub 注入，不真实调用外部
- secret 走 env vars，其余走 TOML

— end of spec —
