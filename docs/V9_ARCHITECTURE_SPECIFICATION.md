# V9 Architecture Specification — Complete System Redesign

> **Status**: DESIGN BLUEPRINT — All code references are forward-looking targets.
> **Scope**: This document defines the architecture for V9 of the Elite Breakout System.
> It is not documentation of V8. If V8 differs from this specification, V8 is wrong.

---

## Table of Contents

1. [Runtime Execution Model](#1-runtime-execution-model)
2. [Ownership Matrix](#2-ownership-matrix)
3. [Pipeline Architecture](#3-pipeline-architecture)
4. [Data Contracts](#4-data-contracts)
5. [Context Model](#5-context-model)
6. [Dependency Rules](#6-dependency-rules)
7. [Migration Plan](#7-migration-plan)

---

## 1. Runtime Execution Model

### 1.1 Daily Lifecycle

The system operates on a **24-hour cycle** tied to NSE trading hours (IST). Every
component is driven by exactly **one timing source**: the `Scheduler`.

```
 00:00 ┌────────────────────────────────────────────────────────────┐
       │  MIDNIGHT ROTATION                                        │
       │  → ApplicationContext.new_trading_day()                   │
       │  → Destroy previous SessionContext                        │
       │  → Release all SESSION-tier caches                        │
       │  → Reset daily telemetry counters                         │
       │  → gc.collect() + malloc_trim()                           │
 00:01 └────────────────────────────────────────────────────────────┘
       │
 01:00 ┌────────────────────────────────────────────────────────────┐
       │  DAILY BUILDER                                            │
       │  Owner: WatchlistService                                  │
       │  Input: TradingView API → NSE+BSE universe                │
       │  Output: elite_fundamental_watchlist.parquet               │
       │  Side effect: Updates DatasetRegistry["watchlist"]         │
       │  Creates: New SessionContext (generation N+1)              │
       └────────────────────────────────────────────────────────────┘
       │
 02:00 ┌────────────────────────────────────────────────────────────┐
       │  WEALTH ENGINE (INITIAL)                                  │
       │  Owner: WealthService                                     │
       │  Input: Watchlist + 1Y daily OHLCV + Fundamentals         │
       │  Output: elite_wealth_system.parquet + DB portfolio        │
       │  Consumes: DatasetRegistry["watchlist"]                    │
       │  Requires: SessionContext in WARMING state                 │
       └────────────────────────────────────────────────────────────┘
       │
 08:30 ┌────────────────────────────────────────────────────────────┐
       │  READINESS VERIFICATION                                   │
       │  Owner: Scheduler                                         │
       │  Checks: Watchlist freshness, Wealth system freshness     │
       │  Action: Rebuilds if stale, restores from DB if missing   │
       │  Transitions SessionContext → READY                       │
       └────────────────────────────────────────────────────────────┘
       │
 09:14 ┌────────────────────────────────────────────────────────────┐
       │  PRE-MARKET WARMUP (09:14:30)                             │
       │  Owner: Scheduler                                         │
       │  Action: Force fetches initial prices + indicators for    │
       │          Multi-TF Scanner to prevent 09:15 timeouts.      │
       └────────────────────────────────────────────────────────────┘
       │
 09:15 ┌────────────────────────────────────────────────────────────┐
       │  MARKET OPEN                                              │
       │  SessionContext transitions → MARKET_OPEN                 │
       │  Note: First evaluations (09:15) use prior day's close    │
       │  for intraday candles until the first bar fully forms.    │
       │                                                           │
       │  ┌──────── INTRADAY LOOP (sequential, locked) ─────────┐ │
       │  │                                                      │ │
       │  │  Every 5 min:                                        │ │
       │  │    → Wealth Engine Intraday Update (CMP + exits)     │ │
       │  │    → Performance Tracker                             │ │
       │  │                                                      │ │
       │  │  Every 15 min:                                       │ │
       │  │    → Wealth Engine Full Scan (BUY alerts)            │ │
       │  │    → Multibagger Exit Monitor                        │ │
       │  │    → Multi-TF Scanner (candle-aligned at :00/:15/    │ │
       │  │      :30/:45, stops at 14:59)                        │ │
       │  │                                                      │ │
       │  │  Continuous:                                         │ │
       │  │    → Scanner Staleness Monitor (every 15 min)        │ │
       │  │                                                      │ │
       │  └──────────────────────────────────────────────────────┘ │
       │                                                           │
 15:30 │  MARKET CLOSE                                             │
       │  SessionContext transitions → POST_MARKET                 │
       └────────────────────────────────────────────────────────────┘
       │
 18:00 ┌────────────────────────────────────────────────────────────┐
       │  EVENING BATCH (sequential, after Bhavcopy)               │
       │                                                           │
       │  1. Wait for Bhavcopy availability (poll every 5 min)     │
       │  2. EOD Scanner (max 10m hard timeout)                    │
       │  3. Reversal Scanner (max 10m hard timeout)               │
       │  4. Pullback Scanner (max 10m hard timeout)               │
       │  5. Post-batch memory purge                               │
       │  6. Verify all three succeeded via DB health records      │
       │  *CRITICAL GATE: All evening scanners MUST release locks  │
       │   by 18:55:00 to ensure Multibagger starts on time.       │
       └────────────────────────────────────────────────────────────┘
       │
 19:00 ┌────────────────────────────────────────────────────────────┐
       │  MULTIBAGGER SCANNER                                      │
       │  Owner: MultibaggerService                                │
       │  Input: Watchlist + Fundamentals + Technicals             │
       │  Output: DB alerts                                        │
       └────────────────────────────────────────────────────────────┘
       │
 ∞     ┌────────────────────────────────────────────────────────────┐
       │  BAYESIAN UPDATER (runs once per 24h, immediately on boot)│
       │  Owner: BayesianService                                   │
       │  Input: Historical trade outcomes                         │
       │  Output: Updated scoring weights in DB                    │
       └────────────────────────────────────────────────────────────┘
```

### 1.2 Timing Authority

**Rule**: The `Scheduler` is the ONLY component that decides when things run.
No scanner, engine, or service may self-schedule. No component may contain
`time.sleep()` for timing purposes — only the Scheduler sleeps.

| Component | Cadence | Timing Owner | Timing Source |
|-----------|---------|-------------|---------------|
| Daily Builder | Once at 01:00 | Scheduler | `now.hour == 1` |
| Wealth Engine (initial) | Once at 02:00 | Scheduler | `now.hour == 2` |
| Readiness Check | Once at 08:30 | Scheduler | `now.hour == 8, now.minute >= 30` |
| Wealth Engine (5m) | Every 5 min during market | Scheduler | `last_run + 300s` |
| Wealth Engine (15m full) | Every 15 min during market | Scheduler | `last_run + 900s` |
| Multi-TF Scanner | 15-min candle boundaries | Scheduler | `current_slot > last_multi_tf` |
| Performance Tracker | Every 5 min during market | Scheduler | `last_run + 300s` |
| Multibagger Exit | Every 15 min during market | Scheduler | `last_run + 900s` |
| Evening Scanners | Once after 18:00 | Scheduler | `now.hour >= 18` + Bhavcopy ready |
| Multibagger Scanner | Once at 19:00 | Scheduler | `now.hour == 19` |
| Bayesian Updater | Every 24h | Scheduler | `time.sleep(86400)` in loop |
| Scanner Staleness | Every 15 min during market | Scheduler | Throttled check |
| Midnight Rotation | Once at 00:00 | Scheduler | `now.hour == 0, now.minute == 0` |

### 1.3 State Machine

```
                  CREATED
                     │
                     ▼
                  WARMING ──────────► SHUTTING_DOWN
                     │                     │
                     ▼                     ▼
                   READY ──────────► DESTROYED
                     │
              ┌──────┴──────┐
              ▼              ▼
         MARKET_OPEN    POST_MARKET
              │              │
              └──────┬───────┘
                     ▼
              SHUTTING_DOWN
                     │
                     ▼
                 DESTROYED
```

**Transitions**:
- `CREATED → WARMING`: When Daily Builder starts
- `WARMING → READY`: When Readiness Verification passes
- `READY → MARKET_OPEN`: When `is_market_open()` returns True
- `MARKET_OPEN → POST_MARKET`: When market closes (15:30 IST)
- `POST_MARKET → SHUTTING_DOWN`: At midnight rotation
- `SHUTTING_DOWN → DESTROYED`: After all caches are released
- Any state → `SHUTTING_DOWN`: On unrecoverable error

### 1.4 Concurrency Model

```
Process: Single Python process (Railway container)

Threads:
  ┌─ Main Thread ─────────────────────────── run_system_scheduler()
  ├─ Thread: evening_scanners ─────────────  run_evening_scanners()
  ├─ Thread: bayesian_loop ────────────────  run_bayesian_loop()
  └─ Thread: health_monitor ───────────────  check_scanner_staleness()

Lock Hierarchy:
  scanner_execution_lock (InstrumentedLock)
    └── ProcessLock per scanner (thread + flock + pg_advisory_lock)
        └── price_cache._fetch_lock (prevents thundering herd)
            └── price_cache._lock (protects _cache dict)
```

**Rules**:
1. `scanner_execution_lock` serializes ALL intraday scanner work
2. Each scanner holds its own `ProcessLock` for distributed exclusion (Railway multi-container)
3. `_fetch_lock` ensures only one thread fetches from API at a time — others wait and hit cache
4. Lock acquisition order MUST be: `scanner_execution_lock` → `ProcessLock` → `_fetch_lock` → `_lock`
5. Violating this order causes deadlock. There are no exceptions.

**Locking Policy**:
- **Acquire lock → Run scanner → Release lock.**
- **Never hold the lock while**: polling, sleeping, waiting for APIs, waiting for Bhavcopy, or retrying.
- For deferred scanners (e.g., Multibagger waiting on Evening Batch): Use a timed/non-blocking acquisition, retry after a short delay, and emit telemetry indicating why it was deferred.

---

## 2. Ownership Matrix

### 2.1 Principle

**Every object has exactly ONE owner. The owner is responsible for:**
- Creating the object
- Refreshing the object
- Invalidating the object
- Destroying the object
- Deciding who may read the object

**No reader may mutate an object it does not own.**

### 2.2 Dataset Ownership

| Dataset | Owner | Storage Tier | Refresh Cadence | Consumers |
|---------|-------|-------------|-----------------|-----------|
| Fundamental Watchlist | `WatchlistService` | Ephemeral (parquet + RAM) | Daily at 01:00 | All scanners, Wealth Engine, Dashboard |
| OHLCV 1D (per symbol) | `PriceService` | Session (RAM cache) | Once per trading day | EOD, Reversal, Pullback, Wealth Engine |
| OHLCV 15m (per symbol) | `PriceService` | Session (RAM cache) | Every 15-min candle close | Multi-TF Scanner |
| OHLCV 5m (per symbol) | `PriceService` | Session (RAM cache) | Every 5-min candle close | Reversal (intraday snap), Wealth Engine |
| OHLCV 1H (per symbol) | `PriceService` | Session (RAM cache) | Every 1-hour candle close | Multi-TF Phase A |
| Technical Indicators | `PriceService` | Computed on write | Attached to OHLCV on fetch | All scanners |
| Delivery/Bhavcopy | `DeliveryService` | Ephemeral (RAM) | Daily after 18:00 | EOD, Reversal |
| Fundamentals Cache | `FundamentalsService` | Durable (Postgres) | Daily at 01:00 | Daily Builder, Wealth Engine |
| Market Regime | `MarketRegimeService` | Session (RAM) | Every 5 min (TTL-based) | All scanners |
| Sector Rankings | `MarketRegimeService` | Durable (Postgres) | Daily | EOD, Multi-TF (sector bonus) |
| RS Ratings | `MarketRegimeService` | Durable (Postgres) | Daily | EOD, Multi-TF (RS bonus) |
| Bayesian Weights | `BayesianService` | Durable (Postgres) | Daily | EOD, Multi-TF, Reversal scoring |
| Surveillance Blacklist | `SurveillanceService` | Ephemeral (RAM, TTL) | TTL-based (5 min) | All scanners |
| Block Deals | `InstitutionalService` | Ephemeral (RAM) | Daily | EOD, Reversal (inst bonus) |
| Scanner Health | `HealthService` | Durable (Postgres) | On every scanner completion | Dashboard, Staleness monitor |
| Alerts | `AlertService` | Durable (Postgres) | On every alert creation | Dashboard, Telegram |
| Notifications | `NotificationService` | Durable (Postgres) | On event | Dashboard, Push service |
| Wealth Portfolio | `WealthService` | Durable (Postgres + parquet) | Every 5m/15m during market | Dashboard |
| Breakout Watchlist | `MultiTFService` | Durable (Postgres) | Every 15 min during market | Multi-TF Phase D |
| Pledge Map | `FundamentalsService` | Durable (Postgres) | Daily | EOD, Reversal scoring |
| Nifty Cache (daily) | `MarketRegimeService` | Session (RAM, 5-min TTL) | TTL-based | MarketRegimeEngine |
| Nifty Cache (intraday) | `MarketRegimeService` | Session (RAM, 5-min TTL) | TTL-based | MarketRegimeEngine |
| Dead Symbols | `SessionContext.CacheManager` | Session (RAM) | On discovery | Price providers |
| Push Throttle | `SessionContext.CacheManager` | Session (RAM) | On send | Push service |

### 2.3 Cache Topology

```
ApplicationContext (process-lifetime singleton)
│
├── DatasetRegistry (process-lifetime)
│   └── {dataset_id → DatasetEntry}  # Metadata only, no data
│
└── SessionContext (trading-day lifetime, generation N)
    │
    ├── HistoricalDataManager
    │   ├── DailyStore     { symbol → DataFrame }     refresh=DAILY
    │   ├── IntradayStore   { symbol → DataFrame }     refresh=EVERY_5_MIN
    │   └── DeliveryStore   { symbol → delivery_pct }  refresh=ON_DEMAND
    │
    ├── MarketRegimeManager
    │   └── cache { ret_6m, dist_52w, ts }             refresh=EVERY_5_MIN
    │
    ├── CacheManager  (named slots)
    │   ├── dead_symbols    { symbol → expiry_ts }
    │   ├── push_throttle   { user_id → last_send_ts }
    │   ├── indices         { data, timestamp }
    │   ├── news            { feed_data }
    │   └── wealth_payload  { mtime, payload }
    │
    └── IndicatorManager (moved from session_context.py)
        └── bundles { (symbol, timeframe) → IndicatorBundle }

price_cache._cache (module-level, outside SessionContext)
│
└── { (interval, period) → { symbol → { data, ts, data_as_of, provider } } }
    │
    └── TTL = get_dynamic_cadence(interval)
        ├── 1d/1wk/1mo: until market close (then 12h)
        ├── 15m: 50% of interval (450s floor)
        ├── 5m: 50% of interval (150s floor)
        └── 1h: 50% of interval (1800s floor)
```

### 2.4 Service Ownership

| Service | Owns | Creates | Destroys | Reads |
|---------|------|---------|----------|-------|
| `Scheduler` | Timing, thread lifecycle | Scanner threads | Midnight rotation | SessionContext.state |
| `WatchlistService` | Watchlist parquet, RAM cache | Daily at 01:00 | End of day | TradingView API |
| `PriceService` | price_cache._cache, indicators | On cache miss | TTL expiry | Yahoo, Fyers APIs |
| `DeliveryService` | Delivery/Bhavcopy data | After 18:00 | End of scan | NSE Bhavcopy API |
| `MarketRegimeService` | Nifty caches, regime context | On TTL expiry | Session destroy | Price data (^NSEI) |
| `WealthService` | Wealth parquet, DB portfolio | 02:00 + every 5m/15m | End of day | Watchlist, OHLCV, Fundamentals |
| `AlertService` | Alert records | On pipeline completion | N/A (persisted) | Pipeline output |
| `HealthService` | Scanner health records | On scanner start/end | N/A (persisted) | Scheduler events |
| `SurveillanceService` | ASM/GSM blacklist | Hourly refresh | TTL expiry | NSE surveillance API |
| `FundamentalsService` | Fundamentals cache, pledge data | Daily | N/A (durable) | Yahoo Finance, NSE |
| `BayesianService` | Scoring weights | Daily | N/A (durable) | Trade outcomes |
| `TelemetryService` | Metrics, phase timers | Phase boundaries | Daily reset | Process state (psutil) |

---

## 3. Pipeline Architecture

### 3.1 Design Principle

**Every scanner follows the same execution pattern:**

```
Load Universe → Fetch Data → Compute Indicators → Apply Business Rules → Score → Risk → Persist → Notify
```

No scanner may invent its own execution flow. The pipeline is defined as a **list of steps**,
and each step comes from a shared **step library**.

### 3.1 Architecture Status Matrix

To prevent confusion between the currently deployed code and the V9 Target Architecture, the following matrix defines the implementation status of major subsystems.

| Component | Current Implementation | Target Architecture (V9) |
|-----------|------------------------|---------------------------|
| Scheduler | Procedural (`main.py`) | Orchestrator |
| Scanner | Functional/Script-based | `PipelineContext` / `PipelineStep` |
| Session | Time guards | `SessionContext` State Machine |
| Alerts | Mixed | Unified `AlertService` |
| Wealth | Monolithic (`wealth_engine.py`) | TBD |
| Cache | Mixed (Global dicts, partial classes) | `CacheManager` |

### 3.2 Current Implementation (Procedural Execution)

Today, scanners execute as procedural functions (e.g., `_start_wrapper`) directly scheduled by `main.py` using time guards (`is_market_open()`). The `PipelineContext` and `PipelineStep` abstractions defined below represent the **Target Architecture** (V9), intended to unify the currently disparate, script-based scanner implementations.

### 3.3 Target Architecture (Abstract Pipeline)

```python
class PipelineStep(ABC):
    """Base class for all pipeline steps."""

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> StepResult:
        """Execute this step. Returns CONTINUE, REJECT, or ERROR."""
        ...

class StepResult(Enum):
    CONTINUE = auto()    # Proceed to next step
    REJECT = auto()      # Symbol rejected, skip remaining steps
    ERROR = auto()       # Unexpected failure, log and continue

class ScannerPipeline:
    """Executes a declared list of steps for each symbol."""

    def __init__(self, name: str, steps: list[PipelineStep]):
        self.name = name
        self.steps = steps

    def run(self, universe: pd.DataFrame, ctx: PipelineContext) -> ScanResult:
        results = ScanResult(scanner=self.name)

        for _, row in universe.iterrows():
            symbol = row["Stock"]
            ctx.set_current_symbol(symbol, row)

            for step in self.steps:
                result = step.execute(ctx)
                if result == StepResult.REJECT:
                    results.record_rejection(symbol, step.name, ctx.rejection_reason)
                    break
                elif result == StepResult.ERROR:
                    results.record_error(symbol, step.name, ctx.error)
                    break
            else:
                results.record_success(symbol, ctx.alert)

        return results
```

### 3.3 Step Library

Every step is a pure function of `PipelineContext`. No step fetches data, accesses the
database, or calls external APIs. All data is pre-loaded into `PipelineContext` before
the per-symbol loop begins.

| Step | Input (from ctx) | Output (to ctx) | Used By |
|------|-------------------|------------------|---------|
| `BlacklistGateStep` | `ctx.blacklist`, `ctx.symbol` | CONTINUE or REJECT | All scanners |
| `CooldownGateStep` | `ctx.cooldown_set`, `ctx.symbol` | CONTINUE or REJECT | EOD, Reversal |
| `DataValidationStep` | `ctx.ohlcv[symbol]` | Validates bars, columns, staleness | All scanners |
| `TrendFilterStep` | `ctx.indicators[symbol]` | EMA/SMA alignment checks | Multi-TF Phase A, EOD |
| `BreakoutDetectionStep` | `ctx.indicators[symbol]` | Breakout signals | EOD, Multi-TF Phase D |
| `ReversalDetectionStep` | `ctx.indicators[symbol]` | RSI curl, MACD cross, drop % | Reversal |
| `PullbackDetectionStep` | `ctx.indicators[symbol]` | Pivot detection, impulse/pullback validation | Pullback |
| `CandleQualityStep` | `ctx.ohlcv[symbol]` | Body ratio, wick ratio, close position | EOD, Multi-TF |
| `VolumeConfirmationStep` | `ctx.ohlcv[symbol]` | Volume ratio vs 20d avg | All scanners |
| `ForensicRiskStep` | `ctx.fundamentals[symbol]` | Forensic tier gate | All scanners |
| `FundamentalFilterStep` | `ctx.fundamentals[symbol]` | ROE, revenue, OPM gates | Reversal, Wealth |
| `ScoringStep` | All prior step outputs | Composite score | All scanners |
| `BayesianAdjustmentStep` | `ctx.bayesian_weights`, score | Bayesian-adjusted score | All scanners |
| `RegimePolicyStep` | `ctx.regime_context`, score | Regime-adjusted threshold | All scanners |
| `ScoreThresholdStep` | Score, threshold | CONTINUE or REJECT | All scanners |
| `SLTargetStep` | `ctx.indicators[symbol]`, entry price | SL, T1-T4, R:R ratio | All scanners |
| `RiskRewardGateStep` | SL result | R:R minimum gate | All scanners |
| `RankingStep` | All passing symbols + scores | Ranked list (top N) | EOD, Multi-TF |
| `AlertCreationStep` | Ranked candidates | Alert payloads | All scanners |
| `DeduplicationStep` | `ctx.recent_alerts`, alert | CONTINUE or REJECT | All scanners |

#### DeduplicationStep Contract
The `DeduplicationStep` enforces cooldowns to prevent spam.
- **Key**: `(symbol, scanner_name, breakout_type)`
- **Scoping**: Cooldowns are isolated *per scanner*.
- **Creation Time**: The cooldown timer starts exactly at the alert creation timestamp.

### 3.4 Scanner Definitions

Each scanner is defined as a **composition of steps**:

```python
# EOD Scanner Pipeline
EOD_PIPELINE = ScannerPipeline("EOD", [
    BlacklistGateStep(),
    CooldownGateStep(scanner="EOD"),
    DataValidationStep(min_bars=50, timeframe="1d"),
    TrendFilterStep(require_above_ema20=True, require_above_sma50=True),
    BreakoutDetectionStep(),
    CandleQualityStep(
        min_body_ratio=0.60, min_close_position=0.70,
        max_upper_wick=0.20, require_bullish=True
    ),
    VolumeConfirmationStep(min_ratio=2.5, min_avg_volume=150_000),
    ForensicRiskStep(),
    ScoringStep(engine="breakout"),
    BayesianAdjustmentStep(),
    RegimePolicyStep(),
    ScoreThresholdStep(base_threshold=82),
    SLTargetStep(mode="EOD"),
    RiskRewardGateStep(min_rr=1.5),
    RankingStep(max_alerts=10),
    AlertCreationStep(scanner="EOD"),
    DeduplicationStep(cooldown_minutes=1440),
])

# Multi-TF Scanner Pipeline (Phase A: 1H Trend Filter)
MULTI_TF_PHASE_A = ScannerPipeline("MULTI_TF_1H", [
    BlacklistGateStep(),
    DataValidationStep(min_bars=20, timeframe="1h"),
    TrendFilterStep(
        require_above_200ema=True,
        require_ema_alignment=True,  # 9 > 20 > 50
        require_adx_above=20
    ),
    VolumeConfirmationStep(min_ratio=1.0, min_avg_volume=100_000),
])

# Multi-TF Scanner Pipeline (Phase D: 15m Trigger)
MULTI_TF_PHASE_D = ScannerPipeline("MULTI_TF_15M", [
    DataValidationStep(min_bars=30, timeframe="15m"),
    BreakoutDetectionStep(),
    CandleQualityStep(
        min_body_ratio=0.55, min_close_position=0.60,
        max_upper_wick=0.25, require_bullish=True
    ),
    VolumeConfirmationStep(min_ratio=2.0),
    ScoringStep(engine="breakout"),
    BayesianAdjustmentStep(),
    RegimePolicyStep(),
    ScoreThresholdStep(base_threshold=78),
    SLTargetStep(mode="MULTI_TF"),
    RiskRewardGateStep(min_rr=1.5),
    AlertCreationStep(scanner="MULTI_TF"),
    DeduplicationStep(cooldown_minutes=720),
])

# Reversal Scanner Pipeline
REVERSAL_PIPELINE = ScannerPipeline("REVERSAL", [
    BlacklistGateStep(),
    CooldownGateStep(scanner="REVERSAL"),
    DataValidationStep(min_bars=50, timeframe="1d"),
    FundamentalFilterStep(min_roe=5.0, min_avg_volume=100_000),
    ReversalDetectionStep(
        min_drop=20.0, max_drop=45.0,
        rsi_oversold=40, rsi_curl_min=35,
        macd_cross_lookback=10
    ),
    VolumeConfirmationStep(min_ratio=1.5),
    ForensicRiskStep(),
    ScoringStep(engine="reversal"),
    BayesianAdjustmentStep(),
    ScoreThresholdStep(base_threshold=62),
    SLTargetStep(mode="REVERSAL"),
    RiskRewardGateStep(min_rr=2.0),
    RankingStep(max_alerts=10),
    AlertCreationStep(scanner="REVERSAL"),
    DeduplicationStep(cooldown_minutes=7200),
])

# Pullback Scanner Pipeline
PULLBACK_PIPELINE = ScannerPipeline("PULLBACK", [
    BlacklistGateStep(),
    DataValidationStep(min_bars=100, timeframe="1d"),
    PullbackDetectionStep(
        impulse_min_pct=15.0,
        pullback_depth_min=23.6, pullback_depth_max=61.8,
        require_volume_contraction=True
    ),
    CandleQualityStep(
        require_bullish=True, min_body_ratio=0.50
    ),
    VolumeConfirmationStep(min_ratio=1.2),
    ForensicRiskStep(),
    ScoringStep(engine="pullback"),
    ScoreThresholdStep(base_threshold=70),
    SLTargetStep(mode="PULLBACK"),
    RiskRewardGateStep(min_rr=2.0),
    RankingStep(max_alerts=10),
    AlertCreationStep(scanner="PULLBACK"),
    DeduplicationStep(cooldown_minutes=7200),
])

# Wealth Engine (Exception to standard Pipeline)
# The Wealth Engine follows a monolithic execution model rather than discrete steps.
# - Ownership: Owned by WealthService, executed via `run_wealth_scan()`
# - Execution Path: Internal bulk loops (`_run_wealth_scan_wrapper`)
# - Persistence: Direct database writes to `wealth_portfolio` and `wealth_buy_alert` tables
# - Alert Flow: Generates internal BUY/HOLD/EXIT signals based on fundamentals + technicals.
# - Deviation Rationale: Wealth Engine manages a stateful, long-term portfolio requiring CMP
#   and exit monitoring, diverging from the stateless momentum trigger model of other scanners.
```

### 3.5 Pre-Loop Data Loading

Before the per-symbol loop starts, the scanner orchestrator pre-loads all data:

```python
class ScannerOrchestrator:
    """Loads data once, runs pipeline for each symbol."""

    def execute_scan(self, pipeline: ScannerPipeline, ctx: PipelineContext):
        # ── Phase 1: Load Universe ────────────────────────────
        ctx.universe = watchlist_service.get_watchlist()

        # ── Phase 2: Bulk Fetch (ONE network call per dataset) ─
        symbols = ctx.universe["Stock"].tolist()
        ctx.ohlcv = price_service.fetch_batch(symbols, ctx.interval, ctx.period)
        ctx.delivery = delivery_service.get_delivery_map(ctx.scan_date)
        ctx.blacklist = surveillance_service.get_blacklist()
        ctx.fundamentals = fundamentals_service.get_bulk(symbols)
        ctx.recent_alerts = alert_service.get_recent(pipeline.name, ctx.cooldown)

        # ── Phase 3: Compute Regime Context (once) ────────────
        ctx.regime_context = market_regime_service.get_context()
        ctx.bayesian_weights = bayesian_service.get_weights(ctx.regime_context.trend)

        # ── Phase 4: Run Pipeline (no network calls inside) ───
        result = pipeline.run(ctx.universe, ctx)

        # ── Phase 5: Persist Results ──────────────────────────
        alert_service.save_batch(result.alerts)
        health_service.update(pipeline.name, result.summary)
        notification_service.send(result.alerts)

        # ── Phase 6: Cleanup ─────────────────────────────────
        ctx.release_temporary_data()
        telemetry_service.log_phase_end(pipeline.name)
```

**Invariant**: There are ZERO network calls inside the per-symbol loop.

---

## 4. Data Contracts

### 4.1 Contract Format

Every pipeline step, service method, and data exchange has a defined contract:

```
Contract: <StepName>
  Input:         What the step reads from PipelineContext
  Output:        What the step writes to PipelineContext
  Side Effects:  External state changes (DB writes, file writes, notifications)
  Thread Safety: Whether concurrent execution is safe
  Failure Mode:  What happens on error (REJECT symbol, SKIP step, ABORT scan)
  Idempotency:   Whether re-execution produces identical results
```

### 4.2 Core Step Contracts

#### DataValidationStep

```
Contract: DataValidationStep
  Input:         ctx.ohlcv[symbol] (pd.DataFrame)
  Output:        ctx.ticker (validated, cleaned DataFrame)
                 ctx.latest (last row as pd.Series)
  Side Effects:  None
  Thread Safety: Safe (reads only)
  Failure Mode:  REJECT if data is None, empty, < min_bars, or missing columns
  Idempotency:   Yes
```

#### BreakoutDetectionStep

```
Contract: BreakoutDetectionStep
  Input:         ctx.ticker (with indicators pre-computed)
  Output:        ctx.breakout_signals (dict of signal_name → True/False)
  Side Effects:  None
  Thread Safety: Safe (pure computation)
  Failure Mode:  REJECT if signal count < MIN_SIGNALS
  Idempotency:   Yes
```

#### ScoringStep

```
Contract: ScoringStep
  Input:         ctx.breakout_signals OR ctx.reversal_signals
                 ctx.latest (indicators), ctx.category, ctx.delivery_pct
  Output:        ctx.raw_score (int 0-100)
                 ctx.model_version (str)
  Side Effects:  None
  Thread Safety: Safe (pure computation)
  Failure Mode:  CONTINUE with score=0 on unexpected error
  Idempotency:   Yes
```

#### SLTargetStep

```
Contract: SLTargetStep
  Input:         ctx.latest (indicators), ctx.entry_price (float)
  Output:        ctx.sl_result (dict with stop_loss, target_1..4, rr_ratio, method)
  Side Effects:  None
  Thread Safety: Safe (pure computation)
  Failure Mode:  REJECT if SL cannot be computed or R:R < minimum
  Idempotency:   Yes

  SL Result Schema:
    {
      "stop_loss":      float,    # Absolute SL price
      "target_1":       float,    # Conservative target
      "target_2":       float,    # Primary target
      "target_3":       float,    # Extended target
      "target_4":       float,    # Ambitious target
      "rr_ratio":       float,    # Risk:Reward ratio (entry - SL vs T2 - entry)
      "sl_method":      str,      # One of: ATR, SWING_LOW, SUPPORT, VWAP
      "target_method":  str,      # One of: ATR, SWING_HIGH, RESISTANCE, FIBONACCI
      "is_rejected":    bool,     # True if R:R < minimum threshold
      "rejection_reason": str     # Human-readable reason for rejection
    }
```

#### AlertCreationStep

```
Contract: AlertCreationStep
  Input:         ctx.symbol, ctx.score, ctx.sl_result, ctx.signals,
                 ctx.regime_context, ctx.category
  Output:        ctx.alert (dict — the complete alert payload)
  Side Effects:  None (persistence is in a separate phase)
  Thread Safety: Safe (pure construction)
  Failure Mode:  ERROR if required fields are missing
  Idempotency:   Yes

  Alert Schema:
    {
      "symbol":        str,
      "scanner":       str,       # EOD, MULTI_TF, REVERSAL, PULLBACK
      "category":      str,       # From watchlist
      "entry_price":   float,
      "score":         int,
      "signals":       list[str],
      "stop_loss":     float,
      "target_1":      float,
      "target_2":      float,
      "target_3":      float,
      "target_4":      float,
      "rr_ratio":      float,
      "regime":        str,       # BULL, BEAR, NEUTRAL
      "algo_version":  str,
      "created_at":    str,       # ISO 8601 IST
      "context": {
        "technicals": { ... },
        "session":    { ... },
        "execution":  { ... }
      }
    }
```

### 4.3 Service Contracts

#### PriceService.fetch_batch

```
Contract: PriceService.fetch_batch
  Input:         symbols: list[str], interval: str, period: str
  Output:        dict[str, pd.DataFrame]  (symbol → OHLCV+indicators)
  Side Effects:  Populates price_cache._cache, may call external APIs
  Thread Safety: Safe (internal locks: _fetch_lock, _lock)
  Failure Mode:  Returns ProviderResult enum for failed symbols
  Idempotency:   Yes (cache deduplicates)
  Guarantees:
    - Indicators are pre-computed on every returned DataFrame
    - Stale data is marked via df.attrs["is_stale"] = True
    - Cache TTL is candle-boundary-aligned via get_dynamic_cadence()
    - Forming candles are stripped for intraday intervals
```

#### MarketRegimeService.get_context

```
Contract: MarketRegimeService.get_context
  Input:         None (fetches Nifty data internally)
  Output:        dict with keys:
                   trend:           BULL | BEAR | NEUTRAL
                   strength:        STRONG | MODERATE | WEAK
                   volatility:      HIGH | NORMAL | LOW
                   market_phase:    EXPANSION | PULLBACK | DISTRIBUTION | CAPITULATION | CONSOLIDATION
                   trend_direction: IMPROVING | STABLE | WEAKENING
                   market_score:    int (0-100)
                   confidence:      { agreement: int, signals: int, score: int }
                   metrics:         { return20d, adx, price_vs_20dma, ... }
  Side Effects:  May refresh internal Nifty cache (TTL-based)
  Thread Safety: Safe (internal lock on MacroCache)
  Failure Mode:  Returns neutral fallback context on any error
  Idempotency:   Yes (deterministic for same market state)
```

### 4.4 Scoring Contracts

#### EOD/Breakout Scoring Formula

```
Base Score = breakout_engine.calculate_score(...)
  ├── Candle Quality:       0-15 pts  (body ratio, close position, wick)
  ├── Volume Surge:         0-20 pts  (volume ratio bands)
  ├── Trend Alignment:      0-15 pts  (EMA20, SMA50, SMA200, Golden Cross)
  ├── Base Tightness:       0-10 pts  (20-day range compression)
  ├── Breakout Margin:      0-10 pts  (close above prior 20D high)
  ├── RSI Quality:          0-10 pts  (52-70 optimal range)
  ├── Category Bonus:       0-10 pts  (from watchlist quality tier)
  ├── Delivery Bonus:       0-5 pts   (delivery % > 35%)
  └── Block Deal Bonus:     0-5 pts   (institutional footprint)

Regime Adjustments:
  ├── RS Bonus:             0-10 pts  (63-day RS ≥ 80th percentile)
  ├── Sector Bonus:         0-8 pts   (Top-3 sector with 3-session hysteresis)
  └── MAX_MOMENTUM_BONUS:   15 pts cap (RS + Sector combined)

Bayesian Adjustments:
  └── Per-weight multipliers from get_latest_weights(regime)

Penalties:
  ├── Extended Breakout:    -0 to -20 pts  (ATR extension > 1.5x)
  ├── OBV Divergence:       -5 pts          (OBV slope ≤ 0)
  └── Promoter Pledge:      -0 to -N pts    (pledge > 10%)

Final Score = max(0, min(100, Base + Regime + Bayesian - Penalties))
Threshold: ≥ 82 (1d), ≥ 78 (15m), ≥ 80 (1h)
```

#### Reversal Scoring Formula

```
Reversal Score = _score_reversal(...)
  ├── Trend Structure:      0-25 pts  (SMA50 + SMA200 reclaim)
  ├── SMA200 Proximity:     0-15 pts  (distance from SMA200)
  ├── Volume Confirmation:  0-15 pts  (ratio above MIN_VOLUME_RATIO)
  ├── MACD Momentum:        0-15 pts  (MACD_HIST / ATR normalization)
  ├── RSI Curl Quality:     0-15 pts  (recovery from oversold)
  ├── Category Quality:     0-10 pts  (fundamental tier)
  ├── Drop Sweet Spot:      -5 to +5 pts  (25-40% = max, >45% = penalty)
  ├── R:R Quality:          0-5 pts   (≥3.5 = 5 pts)
  ├── OBV Confirmation:     0-5 pts   (OBV rising = accumulation)
  ├── Delivery Conviction:  0-5 pts   (≥50% delivery)
  └── Block Deal Bonus:     0-N pts   (institutional footprint)

Penalties:
  └── Promoter Pledge:      -0 to -N pts  (pledge > 10%)

Final Score = min(100, raw_score + inst_bonus)
Threshold: ≥ 62
```

---

## 5. Context Model

### 5.1 PipelineContext

`PipelineContext` replaces dozens of scattered parameters. Every step reads from and writes
to this context. It is the **single carrier** of all state through the pipeline.

```python
@dataclass
class PipelineContext:
    """Immutable container for all data a pipeline step needs."""

    # ── Identity ────────────────────────────────────────────────
    scanner_name: str                       # "EOD", "MULTI_TF", "REVERSAL", etc.
    scan_id: str                            # UUID for this scan run
    scan_date: str                          # "2026-07-24"
    ist_now: datetime                       # Frozen timestamp at scan start

    # ── Universe ────────────────────────────────────────────────
    universe: pd.DataFrame                  # Full watchlist
    interval: str                           # "1d", "15m", etc.
    period: str                             # "1y", "6mo", etc.

    # ── Pre-loaded Datasets (populated before per-symbol loop) ──
    ohlcv: dict[str, pd.DataFrame]          # symbol → OHLCV+indicators
    delivery: dict[str, float]              # symbol → delivery_pct
    blacklist: set[str]                     # Blocked symbols
    fundamentals: dict[str, dict]           # symbol → {roe, opm, forensic_flags, ...}
    recent_alerts: set[tuple]               # (symbol, scanner) pairs in cooldown
    cooldown_symbols: set[str]              # Failed-reversal cooldown symbols
    pledge_map: dict[str, float]            # symbol → promoter_pledge_pct
    snapshots: dict[str, pd.DataFrame]      # symbol → intraday snapshot (5m)

    # ── Regime & Policy ─────────────────────────────────────────
    regime_context: dict                    # MarketRegimeEngine output
    strategy_policy: dict                   # StrategyPolicyEngine output
    bayesian_weights: Optional[dict]        # From DB via BayesianService
    bayesian_version: str                   # "v1", "v2", etc.
    nifty_ret_20d: float                    # Nifty 20-day return

    # ── Per-Symbol State (mutated during loop) ──────────────────
    symbol: str = ""                        # Current symbol being processed
    category: str = ""                      # Current symbol's watchlist category
    sector: str = ""                        # Current symbol's sector
    row: Optional[pd.Series] = None         # Current watchlist row

    ticker: Optional[pd.DataFrame] = None   # Validated OHLCV for current symbol
    latest: Optional[pd.Series] = None      # Last row of ticker

    breakout_signals: dict = field(default_factory=dict)
    reversal_signals: dict = field(default_factory=dict)
    pullback_signals: dict = field(default_factory=dict)

    raw_score: int = 0
    final_score: int = 0
    model_version: str = ""
    entry_price: float = 0.0
    sl_result: Optional[dict] = None
    alert: Optional[dict] = None

    rejection_reason: Optional[str] = None
    error: Optional[Exception] = None

    # ── Telemetry ───────────────────────────────────────────────
    funnel: dict = field(default_factory=dict)  # Step → count tracking
    provider_stats: dict = field(default_factory=dict)

    # ── Methods ─────────────────────────────────────────────────
    def set_current_symbol(self, symbol: str, row: pd.Series):
        """Reset per-symbol state for next iteration."""
        self.symbol = symbol
        self.category = row.get("Category", "")
        self.sector = row.get("sector", "")
        self.row = row
        self.ticker = None
        self.latest = None
        self.breakout_signals = {}
        self.reversal_signals = {}
        self.pullback_signals = {}
        self.raw_score = 0
        self.final_score = 0
        self.entry_price = 0.0
        self.sl_result = None
        self.alert = None
        self.rejection_reason = None
        self.error = None

    def release_temporary_data(self):
        """Release large datasets after scan completes."""
        self.ohlcv = {}
        self.snapshots = {}
        self.delivery = {}
```

### 5.2 Context Lifecycle

```
PipelineContext lifecycle:
  1. Created by ScannerOrchestrator at scan start
  2. Bulk datasets loaded (Phase 2 of orchestrator)
  3. Regime context loaded (Phase 3 of orchestrator)
  4. Passed to ScannerPipeline.run() (Phase 4)
  5. Per-symbol state reset via set_current_symbol() for each symbol
  6. Steps read/write per-symbol fields
  7. release_temporary_data() called after scan (Phase 6)
  8. Context goes out of scope, GC collects
```

### 5.3 Relationship to Existing Contexts

```
V8 Context Model:                    V9 Context Model:
─────────────────                    ─────────────────
ApplicationContext (singleton)   →   ApplicationContext (singleton, unchanged)
  └── SessionContext (daily)     →     └── SessionContext (daily, unchanged)
                                            └── provides data TO PipelineContext

PipelineRunner.execute(            →   ScannerOrchestrator.execute_scan(
    symbol, category, sector,              pipeline, PipelineContext)
    ticker, delivery_pct,
    pledge_pct, nifty_ret_20d,
    regime_ctx, bayesian_weights,
    bayesian_version, publisher)

12 positional parameters           →   1 context object
```

---

## 6. Dependency Rules

### 6.1 Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     INTERFACES                          │
│  dashboard_server.py, api_routes.py, telegram_engine.py │
│  push_service.py                                        │
│  May import: Application, Domain                        │
│  Must NOT import: Infrastructure (directly)             │
├─────────────────────────────────────────────────────────┤
│                    APPLICATION                          │
│  ScannerOrchestrator, WealthService, WatchlistService   │
│  Scheduler, HealthService, AlertService                 │
│  May import: Domain, Infrastructure (via interfaces)    │
│  Must NOT import: Interfaces                            │
├─────────────────────────────────────────────────────────┤
│                     DOMAIN                              │
│  PipelineStep subclasses, ScoringEngine, SLTargetEngine │
│  BreakoutEngine, ReversalEngine, PullbackEngine         │
│  strategy_policy.py, core_enums.py, core_models.py      │
│  May import: Nothing outside Domain layer               │
│  Must NOT import: Application, Infrastructure, Interfaces│
├─────────────────────────────────────────────────────────┤
│                  INFRASTRUCTURE                         │
│  database.py, price_cache.py, watchlist_cache.py        │
│  data_providers/ (yahoo, fyers, nse), lock_utils.py     │
│  telemetry_manager.py, memory_profiler.py               │
│  May import: Domain (for types/enums only)              │
│  Must NOT import: Application, Interfaces               │
└─────────────────────────────────────────────────────────┘
```

### 6.2 Import Rules

| FROM → TO | Allowed? | Rationale |
|-----------|----------|-----------|
| Domain → Domain | ✅ Yes | Steps may compose within the domain |
| Application → Domain | ✅ Yes | Orchestrators use domain steps |
| Application → Infrastructure | ✅ Yes | Orchestrators fetch data via services |
| Infrastructure → Domain | ✅ Yes (types only) | For ProviderResult, ScanFailure types |
| Interfaces → Application | ✅ Yes | Dashboard calls services |
| Interfaces → Domain | ✅ Yes | Dashboard reads domain types |
| Domain → Application | ❌ **NO** | Domain must be pure |
| Domain → Infrastructure | ❌ **NO** | Domain must not know about databases |
| Infrastructure → Application | ❌ **NO** | No upward dependency |
| Infrastructure → Interfaces | ❌ **NO** | No lateral dependency |
| Application → Interfaces | ❌ **NO** | No upward dependency |

### 6.3 File ↔ Layer Mapping

| Layer | Files |
|-------|-------|
| **Domain** | `pipeline_runner.py`, `breakout_engine.py`, `scoring_engine.py`, `sl_target_helper.py`, `strategy_policy.py`, `core_enums.py`, `core_models.py`, `core/events.py`, `core/models.py`, `core/policies.py`, `core/invariants.py`, `core/pipeline_invariants.py`, all `core/*_engine.py` |
| **Application** | `main.py` (scheduler only), `eod_scanner.py`, `multi_tf_scanner.py`, `reversal_scanner.py`, `pullback_pipeline.py`, `wealth_engine.py`, `daily_builder.py`, `bayesian_updater.py`, `session_context.py`, `application_context.py`, `data_registry.py` |
| **Infrastructure** | `database.py`, `price_cache.py`, `watchlist_cache.py`, `delivery_data.py`, `block_deal_detector.py`, `fundamentals_cache.py`, `macro_utils.py`, `surveillance.py`, `lock_utils.py`, `memory_profiler.py`, `telemetry_manager.py`, `symbol_master.py`, `data_providers/*.py` |
| **Interfaces** | `dashboard_server.py`, `api_routes.py`, `telegram_engine.py`, `push_service.py`, `email_engine.py` |

### 6.4 Prohibited Patterns

1. **No scanner may import `price_cache` directly.** Use `PriceService` via context.
2. **No domain step may call `database.*`.** Persistence is in the orchestrator.
3. **No module may import from `main.py`.** Scheduler is a leaf node.
4. **No `from X import *` anywhere.** Explicit imports only.
5. **No module-level global mutable state outside designated owners.** All caches belong to their owner service.

---

## 7. Migration Plan

### 7.1 Phased Approach

The migration from V8 → V9 is divided into **4 phases**. Each phase is independently
shippable. At no point does the system break between phases.

### Phase 1: Pipeline Abstraction (Weeks 1-2)

**Goal**: Extract the shared pipeline abstraction without changing any scanner behavior.

| Step | Action | Risk |
|------|--------|------|
| 1a | Create `PipelineStep` ABC, `StepResult` enum, `ScannerPipeline` class | None — new files |
| 1b | Create `PipelineContext` dataclass | None — new file |
| 1c | Extract `DataValidationStep` from `eod_scanner.py` lines 340-380 | Low — pure extraction |
| 1d | Extract `BreakoutDetectionStep` from `eod_scanner.py` lines 380-440 | Low — pure extraction |
| 1e | Extract `CandleQualityStep` from `eod_scanner.py` lines 440-500 | Low — pure extraction |
| 1f | Extract `ScoringStep` wrapping `calculate_score()` | Low — delegate call |
| 1g | Extract `SLTargetStep` wrapping `compute_sl_and_target()` | Low — delegate call |
| 1h | Extract `AlertCreationStep` from `eod_scanner.py` lines 700-800 | Low — pure extraction |
| 1i | Wire `EOD_PIPELINE` using extracted steps | Medium — integration |
| 1j | Run `EOD_PIPELINE` in shadow mode alongside V8 EOD scanner | None — parallel execution |

**Regression Test**: Compare V8 and V9 EOD scanner outputs for 30 consecutive trading days.
Must produce identical alerts (same symbols, same scores ±1, same SL/targets ±0.5%).

**Rollback**: Delete new files. V8 scanner is untouched.

### Phase 2: Scanner Migration (Weeks 3-5)

**Goal**: Migrate all 4 scanners to the pipeline abstraction.

| Step | Action | Risk |
|------|--------|------|
| 2a | Extract Reversal-specific steps (`ReversalDetectionStep`, `FundamentalFilterStep`) | Low |
| 2b | Wire `REVERSAL_PIPELINE` | Medium |
| 2c | Extract Multi-TF-specific steps (`TrendFilterStep` for Phase A) | Low |
| 2d | Wire `MULTI_TF_PHASE_A` and `MULTI_TF_PHASE_D` | Medium |
| 2e | Extract Pullback-specific steps (`PullbackDetectionStep`) | Low |
| 2f | Wire `PULLBACK_PIPELINE` | Medium |
| 2g | Create `ScannerOrchestrator` with bulk pre-loading | Medium — new pattern |
| 2h | Migrate EOD from direct execution to `ScannerOrchestrator` | Medium |
| 2i | Migrate all scanners to `ScannerOrchestrator` | Medium |

**Regression Test**: For each scanner, run V8 and V9 in parallel for 2 weeks.
Alert diff must be zero (excluding timing differences).

**Rollback**: Feature flag `USE_V9_PIPELINE=false` falls back to V8 scanner code.

### Phase 3: Context & Ownership Cleanup (Weeks 6-7)

**Goal**: Enforce ownership matrix. Eliminate global mutable state.

| Step | Action | Risk |
|------|--------|------|
| 3a | Move `price_cache._cache` ownership to `PriceService` | Medium |
| 3b | Move `_nifty_cache_fallback` to `MarketRegimeService` | Low |
| 3c | Move `_DELIVERY_DATA` from `daily_builder.py` to `DeliveryService` | Low |
| 3d | Move `_BLACKLIST_SYMBOLS` from `daily_builder.py` to `SurveillanceService` | Low |
| 3e | Remove all `from price_cache import ...` from scanner files | Medium |
| 3f | Remove all `from database import ...` from domain-layer files | Medium |
| 3g | Add `test_dependency_rules.py` enforcing import constraints | None |

**Regression Test**: All 326 existing tests pass. New `test_dependency_rules.py` passes.

**Rollback**: Revert service abstractions. Direct imports still work.

### Phase 4: Scheduler Decomposition (Week 8)

**Goal**: Reduce `main.py` from 1812 lines to <200 lines.

| Step | Action | Risk |
|------|--------|------|
| 4a | Extract `SchedulerConfig` (timing table from §1.2) | None |
| 4b | Extract `ScannerRunner` (the intraday loop body) | Medium |
| 4c | Extract `EveningBatchRunner` (EOD + Reversal + Pullback) | Medium |
| 4d | Extract `HealthMonitor` (staleness checks) | Low |
| 4e | `main.py` becomes: `Scheduler` → `ScannerRunner` → `ScannerOrchestrator` | Medium |

**Regression Test**: Full system test on Railway staging for 5 trading days.

**Rollback**: Restore monolithic `main.py` from git.

### 7.2 Migration Timeline

```
Week 1-2:  Phase 1 (Pipeline Abstraction)
           ├── New files only, zero risk
           └── Shadow mode validation

Week 3-5:  Phase 2 (Scanner Migration)
           ├── Feature-flagged
           └── Parallel V8/V9 execution

Week 6-7:  Phase 3 (Context & Ownership)
           ├── Service layer introduction
           └── Import rule enforcement

Week 8:    Phase 4 (Scheduler Decomposition)
           ├── main.py decomposition
           └── Production deployment
```

### 7.3 Compatibility Layer

During migration, a compatibility shim allows V8 code to work with V9 services:

```python
# compatibility.py — TEMPORARY (remove after Phase 4)

def fetch_watchlist_data(watchlist, period, interval):
    """V8 signature → V9 PriceService delegation."""
    from price_service import price_service
    return price_service.fetch_batch(
        symbols=watchlist["Stock"].tolist(),
        interval=interval,
        period=period
    )
```

### 7.4 Success Metrics

| Metric | V8 Baseline | V9 Target |
|--------|-------------|-----------|
| `main.py` line count | 1812 | < 200 |
| Scanner LOC (avg per scanner) | ~900 | < 100 (pipeline definition) |
| Global mutable state locations | 12 | 0 |
| Modules with `from database import` | 28 | ≤ 5 (infrastructure only) |
| Pipeline step reuse (shared steps / total) | 0% | > 60% |
| Time to add new scanner | ~2 weeks | ~2 days (compose steps) |
| Regression test coverage | 326 tests | 326 + scanner-specific pipeline tests |

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **Scanner** | A component that evaluates a universe of stocks against a specific strategy (EOD breakout, reversal, etc.) |
| **Pipeline** | An ordered list of steps that a scanner executes for each symbol |
| **Step** | A single unit of work in a pipeline (e.g., validate data, detect breakout, compute score) |
| **Context** | The `PipelineContext` object carrying all data through the pipeline |
| **Owner** | The single service responsible for creating, refreshing, and destroying a dataset |
| **Consumer** | A component that reads (but never mutates) a dataset |
| **Session** | A trading-day-scoped lifecycle managed by `SessionContext` |
| **Regime** | The current market condition (BULL/BEAR/NEUTRAL) derived from Nifty 50 |
| **Cadence** | The frequency at which a cache or scanner refreshes |
| **TTL** | Time-to-live for a cache entry, after which it is considered stale |
| **Bhavcopy** | NSE's end-of-day delivery and volume data file |

## Appendix B: Decision Log (Architecture Decision Records)

### ADR-001: Single Process, Not Microservices
**Decision**: V9 remains a single-process Python application.
**Rationale**: The system runs on Railway with 500MB RAM. Microservices would add network
overhead, deployment complexity, and memory duplication. The current process handles
~500 symbols per scan in < 3 minutes. There is no scaling problem to solve with microservices.

### ADR-002: Lazy Indicator Computation (Rejected for V9)
**Decision**: Indicators are computed eagerly at fetch time (inside `PriceService`).
**Rationale**: Moving indicators to a lazy `ComputeIndicatorsStep` would require every
scanner to specify which indicators it needs, adding step-level configuration complexity.
The current approach (compute all indicators once at fetch time, cache the result) is
simpler and produces fewer total computations since multiple scanners share the same
cached DataFrame with indicators already applied.

### ADR-003: Per-Symbol Cache Granularity
**Decision**: The price cache uses `(interval, period) → { symbol → { data, ts } }`.
Each symbol has its own freshness timestamp.
**Rationale**: This allows partial cache refreshes (only fetch stale symbols), independent
eviction, and straightforward debugging. Batch timestamps would create ambiguity about
which symbols were actually fresh.

### ADR-004: Sequential Scanner Execution
**Decision**: All scanners within a window run sequentially under `scanner_execution_lock`.
**Rationale**: The 500MB RAM constraint on Railway makes concurrent scanner execution
dangerous (peak memory for 2 concurrent scanners ≈ 800MB). Sequential execution also
prevents API rate-limit violations and simplifies cache reasoning.

### ADR-005: Event Publisher as Observer, Not Command Bus
**Decision**: `EventPublisher` emits observation events (e.g., `ScannerCompleted`).
It does NOT trigger subsequent actions. The orchestrator controls flow.
**Rationale**: Command buses introduce hidden control flow. The pipeline's explicit
step-by-step execution is easier to debug, test, and reason about.


## 8. Exact Technical Thresholds & Scanning Rules

### 8.1 Hard Disqualifiers (All Scanners)
1. **Illiquidity**: 20-bar average volume must be > 50,000 (adjusts per timeframe).
2. **Distribution Candle**: If current bar volume > 2.0x average AND close is in bottom 50% of candle.
3. **Rejection Wick**: Upper wick > 40% of total candle range.
4. **Weak Trend (ADX)**:
   - Daily: ADX < 25 (strict)
   - 1H: ADX < 20
   - 15m: ADX < 18
5. **RSI Divergence**: Price makes a higher high over 14 bars, but RSI makes a lower high.
6. **Volume-less Extension**: Price > Bollinger Upper Band with volume < 2.0x (1d), 1.5x (1h), 1.3x (15m).
7. **Exhaustion**: 3 dojis/narrow-range candles on low volume in the last 4 bars.
8. **Climax Top**: Highest volume in lookback window, but upper wick > 25% and close in bottom 40%.
9. **Lower-High Pattern**: Breakout failed retest (current high < 3-bar ago high < 6-bar ago high).
10. **Thin Spread Trap**: Candle range < 0.3% of price (or < 5 ticks).

### 8.2 EOD Breakout Scoring (Max 100)
- **Category Base (Max 30)**:
  - Wealth Compounder, Debt-Free Cash Generator, Top Bank: +30 pts.
  - Long Term Compounder: +28 pts.
  - Dividend Aristocrat: +27 pts.
  - Capital Efficient: +26 pts.
  - Recovery Play: +8 pts.
- **Breakout Signals (Max 25)**: +8 per technical signal. +4 bonus for 52W breakout, +2 for Monthly breakout.
- **RSI Quality (Max 15)**: Sweet spot 60–70 (15 pts). Ranges taper to 0 outside 57–82.
- **Volume Quality (Max 20)**: >4.0x (20 pts), >3.0x (15 pts), >2.5x (12 pts), >2.0x (7 pts), >1.5x (3 pts).
- **Trend Strength (Max 10)**: EMA20 > SMA50 (+3), SMA50 > SMA200 (+3), ADX >= 30 (+2), MACD Bullish (+8).
- **Bonuses**:
  - +5 RSI Rising
  - +5 Nifty Relative Strength Outperformance
  - +4 Tight Base Consolidation
  - +3 Sustained Volume (3-bar avg > 1.5x 20-bar avg)
  - +6 Delivery Conviction (>60% same-day delivery)
  - Institutional Footprint (+6 to +8 from block deals)
- **Penalties**:
  - -6 Extended above SMA50 (>5% gap)
  - -5 Extreme RSI (> 78)
  - -4 Unsustained volume drop-off
  - -4 Choppy approach (Base width > volatility threshold)
  - -15 Late Stage Base (Declining 200 SMA YoY)

### 8.3 Risk Management (SL & Targets)
**Buffers**: Stop Losses are placed below structural support (Swing Low, VWAP, S1) with an anti-trap buffer.
- EOD Buffer: `max(0.80 * ATR, 0.75% of price)`
- Multi-TF Buffer: `max(0.50 * ATR, 0.50% of price)`
- Reversal Buffer: `max(1.00 * ATR, 1.00% of price)`
- Max Stop Loss Cap: 3.0x ATR.

**Minimum Required R:R**:
- EOD/Reversal/Pullback: 2.0
- Multi-TF: 1.5

## 9. Complete Database Schema (Postgres)

Below are the critical schemas required to reconstruct the backend.

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    breakout_type TEXT NOT NULL,
    alert_time TEXT NOT NULL,
    alert_date TEXT NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS breakout_watchlist (
    symbol TEXT PRIMARY KEY,
    category TEXT,
    current_state TEXT,
    h1_status TEXT,
    m30_status TEXT,
    m15_status TEXT,
    m5_status TEXT,
    breakout_level REAL,
    support_level REAL,
    invalidated_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    session_date TEXT,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scanner_health (
    scanner_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'IDLE',
    last_success TEXT,
    today_alerts INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    is_acknowledged BOOLEAN DEFAULT TRUE,
    updated_at TEXT NOT NULL,
    error_severity TEXT DEFAULT NULL,
    error_count INTEGER DEFAULT 0,
    first_error_at TEXT DEFAULT NULL,
    retry_count INTEGER DEFAULT 0,
    scheduled_for TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS funnel_telemetry (
    id SERIAL PRIMARY KEY,
    scanner TEXT NOT NULL,
    run_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stage TEXT NOT NULL,
    gate TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    observed_value REAL,
    threshold_value REAL,
    comparator TEXT,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bayesian_model_updates (
    id SERIAL PRIMARY KEY,
    regime TEXT NOT NULL,
    proposed_version TEXT NOT NULL,
    current_version TEXT NOT NULL,
    current_weights JSONB NOT NULL,
    proposed_weights JSONB NOT NULL,
    trades_analyzed INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    admin_comment TEXT,
    approved_by TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    applied_at TEXT,
    created_at TEXT NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wealth_buy_alert (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    alert_price REAL NOT NULL,
    alert_date TEXT NOT NULL DEFAULT CURRENT_DATE
);
```
*(Note: Refer to `app/database.py` for all 42 schemas. The above are the critical path schemas.)*

## 10. Dashboard & UI/UX Specifications

### 10.1 Aesthetics (Glassmorphism & Dark Mode)
- **Background**: Deep space dark mode (`#0B0E14` or similar).
- **Cards**: Glassmorphism effect with `backdrop-filter: blur(12px)`, background `rgba(255,255,255,0.03)`, and 1px borders with linear-gradients.
- **Typography**: `Inter` or `Outfit` for modern sans-serif feel.
- **Colors**:
  - Bullish/Success: Neon Emerald (`#10b981` with glow).
  - Bearish/Danger: Crimson Red (`#ef4444`).
  - Warning/Neutral: Amber (`#f59e0b`).
  - Brand Primary: Electric Blue (`#3b82f6`).
- **Micro-animations**: Hover states must lift cards by `translateY(-2px)` and increase box-shadow intensity. Transitions should be `0.2s ease-in-out`.

### 10.2 Data Streaming Contracts
- **Scanner Health**: Dashboard polls `/api/health` every 15 seconds. Payload maps `scanner_name` to `status` (OK/ERROR/RUNNING).
- **Live Alerts**: Dashboard subscribes via WebSockets. Payload is the `AlertSchema` defined in Section 4.2.
- **Funnel Telemetry**: Dashboard polls `/api/funnel` on demand to render D3.js/Recharts funnel diagrams showing drop-off at each stage of the pipeline.

## 11. External Integration Contracts

### 11.1 Environment Variables Required
- `DATABASE_URL`: Postgres connection string (Railway managed).
- `TELEGRAM_BOT_TOKEN`: Token from BotFather for alert broadcasting.
- `TELEGRAM_CHAT_ID`: Channel ID where alerts are routed.
- `FYERS_CLIENT_ID` / `FYERS_SECRET_KEY`: For real-time intraday data fetching.
- `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`: For browser Push API notifications.
- `REDIS_URL`: (Optional) for distributed locking if multi-container.

### 11.2 External APIs
- **NSE Bhavcopy**: Downloaded daily post-market for institutional delivery data. Must handle 403s with proper user-agents.
- **Yahoo Finance (yfinance)**: Used as fallback for EOD data. Throttle to 5 req/sec to prevent IP bans.
- **TradingView**: Webhooks are received on `/api/webhooks/tv`. Payload contains `{{ticker}}` and `{{strategy.order.action}}`.



## 12. Complete Repository Module Inventory
 (`app/`)

### 2.1 Core Orchestration & Scheduler
- `app/main.py`: Main system entrypoint and 24/7 background scheduler (`run_system_scheduler()`). Manages daily task state, locks, and scanner execution triggers.
- `app/application_context.py`: Singleton context (`ApplicationContext`) holding process-wide state, market regime managers, and dataset registries.
- `app/session_context.py`: Session rotation context (`SessionContext`) managing midnight state tear-down and daily trade boundary tracking.

### 2.2 Quantitative Scanner Engines
- `app/wealth_engine.py`: Fundamental screening, BUY signal generation, and 5m/15m market-hours portfolio CMP and exit monitoring.
- `app/multi_tf_scanner.py`: Intraday 4-stage cascade scanner (1H Phase A → 30m Phase B → 15m Phase C → 5m Phase D).
- `app/eod_scanner.py`: Post-market daily momentum breakout scanner operating post-Bhavcopy delivery.
- `app/reversal_scanner.py`: Deep discount mean-reversion scanner with oversold RSI curl and MACD crossover filters.
- `app/pullback_pipeline.py`: Uptrend pullback continuation pipeline detecting orderly swing pullbacks in established trends.
- `app/multibagger.py`: Long-term fundamental compounder screener and 15-minute market-hours exit monitor.

### 2.3 Analytics, Risk & Scoring Engines
- `app/scoring_engine.py`: Centralized 0–100 candidate scoring engine evaluating category quality, technical momentum, volume expansion, and RSI location.
- `app/sl_target_helper.py`: Dynamic stop-loss/target calculation engine (`compute_sl_and_target`) and invariant validation (`TradeStructureValidator`).
- `app/trade_ranking_engine.py`: Multi-factor candidate sorter ranking setups by quality, volume expansion, risk-reward, and regime alignment.
- `app/macro_utils.py`: Macro market regime engine (`MarketRegimeEngine`) evaluating Nifty 6-month returns and 52W high distance.
- `app/strategy_policy.py`: Strategy policy engine (`StrategyPolicyEngine`) supplying regime-specific filter adjustments.
- `app/valuation_utils.py`: Peer valuation calculator (`compute_peer_medians`) computing sector P/E, P/B, and EV/EBITDA medians.

### 2.4 Data Acquisition & Fetchers
- `app/data_provider.py`: High-level data provider boundary managing candidate symbol canonicalization and fetch routing.
- `app/data_providers/unified_fetcher.py`: Unified fetcher (`UnifiedFetcher`) enforcing primary/secondary fallback chains.
- `app/data_providers/fyers_fetcher.py`: Fyers REST API client (`FyersFetcher`) with 99-day range capping for intraday resolutions.
- `app/price_provider.py`: Price provider (`PriceProvider`) enforcing BSE `.BO` fallback mappings and rate limit backoffs.
- `app/yf_rate_limiter.py`: YFinance rate limiter enforcing circuit breakers on HTTP 429 throttling.
- `app/pledge_scraper.py` & `app/pledge_worker.py`: ScraperAPI residential proxy worker scraping NSE promoter pledge datasets.
- `app/delivery_data.py`: NSE Bhavcopy delivery metrics scraper with 0-to-4 day lookback fallback logic.

### 2.5 Cache & Storage Infrastructure
- `app/price_cache.py`: Centralized price cache managing in-memory `_cache` dict, dynamic TTLs, and disk parquet persistence.
- `app/dataset_registry.py`: Centralized memory registry (`DatasetRegistry`) managing `PERSISTENT`, `SESSION`, and `EPHEMERAL` dataset lifecycles.
- `app/database.py`: Primary PostgreSQL interface containing pool initialization (`DB_MAXCONN=50`), schema migrations, and CRUD helpers.
- `app/watchlist_cache.py`: Watchlist disk parquet cache manager.

---


## 13. MATHEMATICAL FORMULAS & QUANTITATIVE ALGORITHMS

## 1. Vectorized Technical Indicator Formulas

### 1.1 Relative Strength Index (RSI - 14 Period)
Let $\Delta P_t = \text{Close}_t - \text{Close}_{t-1}$.
$$\text{Gain}_t = \max(\Delta P_t, 0), \quad \text{Loss}_t = \max(-\Delta P_t, 0)$$
Using Wilder's Exponential Smoothing over period $N = 14$:
$$\text{AvgGain}_t = \frac{\text{AvgGain}_{t-1} \times 13 + \text{Gain}_t}{14}, \quad \text{AvgLoss}_t = \frac{\text{AvgLoss}_{t-1} \times 13 + \text{Loss}_t}{14}$$
$$\text{RS}_t = \frac{\text{AvgGain}_t}{\text{AvgLoss}_t}, \quad \text{RSI}_t = 100 - \frac{100}{1 + \text{RS}_t}$$

### 1.2 Average Directional Index (ADX - 14 Period)
Let $\text{TR}_t = \max(\text{High}_t - \text{Low}_t, |\text{High}_t - \text{Close}_{t-1}|, |\text{Low}_t - \text{Close}_{t-1}|)$.
$$\text{+DM}_t = \text{High}_t - \text{High}_{t-1} \quad \text{if } \text{High}_t - \text{High}_{t-1} > \text{Low}_{t-1} - \text{Low}_t \text{ else } 0$$
$$\text{-DM}_t = \text{Low}_{t-1} - \text{Low}_t \quad \text{if } \text{Low}_{t-1} - \text{Low}_t > \text{High}_t - \text{High}_{t-1} \text{ else } 0$$
Smoothed via Wilder's formula over $N = 14$:
$$\text{+DI}_{14} = 100 \times \frac{\text{Smooth(+DM)}}{\text{Smooth(TR)}}, \quad \text{-DI}_{14} = 100 \times \frac{\text{Smooth(-DM)}}{\text{Smooth(TR)}}$$
$$\text{DX} = 100 \times \frac{|\text{+DI} - \text{-DI}|}{\text{+DI} + \text{-DI}}, \quad \text{ADX}_{14} = \text{WilderSmooth}(\text{DX}, 14)$$

### 1.3 Exponential Moving Average (EMA)
$$\alpha = \frac{2}{N + 1}, \quad \text{EMA}_t = (\text{Close}_t \times \alpha) + (\text{EMA}_{t-1} \times (1 - \alpha))$$

---

## 2. Fundamental & Candidate Scoring Logic

### 2.1 Fundamental Quality Score (`FM_Score`)
Calculated from financial metrics in `data/watchlist.parquet`:
- **Financial Sector Rule (Banks & NBFCs):**
  $$\text{Financial\_Pass} = (\text{ROE} \ge 15.0) \land (\text{DebtToEquity} \le 3.0) \land (\text{YoY\_Revenue\_Growth} \ge 10.0)$$
- **Non-Financial Sector Rule:**
  $$\text{NonFinancial\_Pass} = (\text{ROCE} \ge 15.0) \land (\text{DebtToEquity} \le 1.0) \land (\text{YoY\_Revenue\_Growth} \ge 10.0)$$
$$\text{FM\_Score} = \text{BasePoints}(40) + \min(\text{ROE}, 30) \times 1.0 + \min(\text{RevenueGrowth}, 30) \times 0.5 - (\text{PledgePct} \times 2.0)$$

### 2.2 Centralized Candidate Scoring Engine (`scoring_engine.py`)
Outputs a normalized score $S \in [0, 100]$:
$$S = S_{\text{Category}} + S_{\text{Momentum}} + S_{\text{Volume}} + S_{\text{RSI\_Location}}$$
- $S_{\text{Category}}$: `DEBT_FREE_CASH` = 30 pts, `WEALTH_COMPOUNDER` = 25 pts, `BLUE_CHIP` = 20 pts.
- $S_{\text{Momentum}}$: $+15$ pts if Close > EMA9 > EMA20 > SMA50.
- $S_{\text{Volume}}$: $+25 \times \min\left(\frac{\text{Volume}_t}{\text{SMA20(Volume)}}, 3.0\right) / 3.0$.
- $S_{\text{RSI\_Location}}$: $+20$ pts if $55 \le \text{RSI} \le 68$.

---

## 3. Dynamic Stop-Loss & Target Engine (`sl_target_helper.py`)

$$\text{Initial\_SL} = \min(\text{SwingPivotLow}_{10}, \text{Entry} - (1.5 \times \text{ATR}_{14}))$$
$$\text{Target}_1 = \text{Entry} + 1.5 \times (\text{Entry} - \text{SL})$$
$$\text{Target}_2 = \text{Entry} + 2.5 \times (\text{Entry} - \text{SL})$$
$$\text{Target}_3 = \text{Entry} + 4.0 \times (\text{Entry} - \text{SL})$$
$$\text{Risk-Reward Ratio } (R:R) = \frac{\text{Target}_1 - \text{Entry}}{\text{Entry} - \text{SL}} \ge 2.0$$

---


## 14. COMPLETE REST API SPECIFICATIONS

| Endpoint URL | Method | Auth Level | Description | Response JSON Schema |
| :--- | :--- | :--- | :--- | :--- |
| `/api/scanner_status` | `GET` | Public | Status & health of all 6 scanners. | `{"status": "ok", "scanners": [{"scanner_name": "MULTI_TF", "status": "HEALTHY", "duration_seconds": 5.2}]}` |
| `/api/trigger-scanner` | `POST` | Admin | Async trigger for a specific scanner. | `{"status": "success", "message": "Scanner MULTI_TF triggered"}` |
| `/api/lock-stats` | `GET` | Admin | Mutex contention telemetry. | `{"acquisitions": 142, "max_wait_sec": 0.12, "contention_events": 0}` |
| `/api/wealth_data` | `GET` | Public | Parsed Wealth Engine portfolio data. | `{"status": "ok", "data": [{"Stock": "RELIANCE", "CMP": 2450.0, "HoldScore": 88}]}` |
| `/api/multi_tf_data` | `GET` | Public | Intraday cascade stage tables. | `{"hourly_passed": [...], "setup_armed": [...], "entry_ready": [...]}` |

---


## 15. TARGET VERSION 9 (v9.0.0) CLEAN ARCHITECTURE SPECIFICATION

## 1. Clean 5-Layer Layout (`src/`)
- `src/domain/`: Pure business logic models, indicators, risk, and strategy rules.
- `src/application/`: Application services, pipeline steps (`IPipelineStep`), and context objects (`PipelineContext`).
- `src/infrastructure/`: API fetchers, PostgreSQL repositories (`AlertRepository`, `HealthRepository`), and `PriceRepository`.
- `src/interfaces/`: Flask REST API server and 24/7 background scheduler (`TaskScheduler`).
- `src/common/`: Lock instrumentations, IEEE 754 float sanitizers, and exceptions.

## 2. Encapsulated Repository Contracts
```python
from abc import ABC, abstractmethod
from typing import Optional, Dict
import pandas as pd

class IPriceRepository(ABC):
    @abstractmethod
    def get(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        ...

    @abstractmethod
    def fetch_watchlist_data(
        self, watchlist: pd.DataFrame, period: str, interval: str
    ) -> Dict[str, Optional[pd.DataFrame]]:
        ...

    @abstractmethod
    def evict(self, symbol: str, interval: str, period: str) -> None:
        ...

    @abstractmethod
    def purge(self) -> int:
        ...
```

---


### parquet_cache
```sql
CREATE TABLE IF NOT EXISTS parquet_cache (
    name TEXT NOT NULL,
    date DATE NOT NULL,
    data BYTEA NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (name, date)
);
```

---

## 8. Scanner Execution Guarantees & Cache Rules

### 8.1 Scanner Engine Contracts
1. **Full-Universe Candidate Collection**: All scanners (`EOD`, `MULTIBAGGER`, `REVERSAL`, `PULLBACK`) must accumulate candidates across all 50-stock chunks before executing global sorting, `SCANNER_MAX_ALERTS` truncation (top 10), and DB persistence.
2. **Safe Live Price Stitching**: Live intraday price stitching must only mutate daily candles when `snap_df` contains a valid, non-null close. Missing intraday snapshots must never overwrite `Close` with `None` or cause row drops during `dropna()`.
3. **ProviderResult Handling**: All data consumers must verify `isinstance(df, pd.DataFrame)` before dereferencing `.empty` or `.iloc` to prevent `AttributeError` crashes on provider status enums (`ProviderResult.NOT_FOUND`).
4. **Cooldown Deduplication**: All scanners must check `cooldown_alerts` (`(symbol, scanner_name) in cooldown_alerts`) prior to candidate alerting to prevent duplicate alerts on consecutive days.
5. **Timestamp Normalization**: `validate_ohlcv_structure()` and `_download_all_robust()` enforce `pd.to_datetime(..., errors='coerce')`, NaN removal, deduplication, and chronological sorting before structure validation to prevent string sorting errors on provider timestamps.

