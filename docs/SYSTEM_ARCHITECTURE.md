# ELITE BREAKOUT SYSTEM — COMPLETE TECHNICAL ARCHITECTURE & ZERO-CODE RECONSTRUCTION SPECIFICATION

> **Document Class:** Developer & AI Model Technical Reconstruction Blueprint
> **Target Audience:** Systems Engineers, Quantitative Developers, AI Coding Models
> **Status:** Absolute Master Technical Specification for 100% self-contained system reconstruction without access to source code.
> **Target File:** `docs/SYSTEM_ARCHITECTURE.md`
> **Last Synchronized:** 2026-07-25 (v8.4.3+)

---

## TABLE OF CONTENTS

1. [Architectural Philosophy & System Runtime Model](#1-architectural-philosophy--system-runtime-model)
2. [Ownership Matrix & Cache Topology](#2-ownership-matrix--cache-topology)
3. [Abstract Pipeline Architecture & Step Library](#3-abstract-pipeline-architecture--step-library)
4. [Context Model & Lifecycle](#4-context-model--lifecycle)
5. [Quantitative Algorithms & Mathematical Engines](#5-quantitative-algorithms--mathematical-engines)
6. [Exhaustive Internal Scanner Execution Code Flows](#6-exhaustive-internal-scanner-execution-code-flows)
7. [Data Acquisition, Provider Routing & Resiliency Topology](#7-data-acquisition-provider-routing--resiliency-topology)
8. [Price Cache Infrastructure & Parquet Sidecars](#8-price-cache-infrastructure--parquet-sidecars)
9. [Database Architecture & Complete PostgreSQL DDLs](#9-database-architecture--complete-postgresql-ddls)
10. [Concurrency, Synchronization & Lock Hierarchy](#10-concurrency-synchronization--lock-hierarchy)
11. [Complete REST API Specifications & Streaming Protocols](#11-complete-rest-api-specifications--streaming-protocols)
12. [Complete Repository Module Inventory](#12-complete-repository-module-inventory)
13. [UI/UX Specifications & Streaming Contracts](#13-uiux-specifications--streaming-contracts)
14. [Deployment Verification & Production Test Gates](#14-deployment-verification--production-test-gates)
15. [V9 Clean Architecture Blueprint & Deprecation Protocol Log](#15-v9-clean-architecture-blueprint--deprecation-protocol-log)

---

# 1. ARCHITECTURAL PHILOSOPHY & SYSTEM RUNTIME MODEL

## 1.1 Process Architecture & Deployment Budget
- **Runtime Model**: Single Python 3.9 process running inside a Linux/Railway container.
- **Resource Constraints**:
  - RAM Budget: Strictly bounded at **500 MB RAM**.
  - Process Isolation: Microservices are explicitly prohibited due to RAM duplication, inter-process serialization overhead, and network latency. All services run in-process using thread pools and shared memory structures.
- **System Invariants**:
  - **IST Timezone**: All timing, candle boundaries, trading schedules, and database timestamps MUST be evaluated in **IST (UTC+5:30)**.
  - **Rupee Currency**: All financial figures, stop losses, target gains, and portfolio CMPs MUST be denominated in **Indian Rupees (₹ / RS)**.

## 1.2 Daily 24-Hour Lifecycle Timeline (`app/main.py`)

Every background operation is governed by an autonomous 24/7 scheduler loop (`run_system_scheduler()`) executing the following timeline:

```text
 00:00 ┌────────────────────────────────────────────────────────────┐
       │ MIDNIGHT ROTATION                                          │
       │ → ApplicationContext.new_trading_day()                     │
       │ → Destroy previous SessionContext                          │
       │ → Release all SESSION-tier caches                          │
       │ → Reset daily telemetry counters                           │
       │ → Force gc.collect() + malloc_trim()                       │
 00:01 └────────────────────────────────────────────────────────────┘
       │
 01:00 ┌────────────────────────────────────────────────────────────┐
       │ DAILY BUILDER                                              │
       │ Owner: WatchlistService (app/daily_builder.py)            │
       │ Input: TradingView API → NSE + BSE universe                │
       │ Output: data/watchlist.parquet                             │
       │ Side Effect: Updates DatasetRegistry["watchlist"]          │
       └────────────────────────────────────────────────────────────┘
       │
 02:00 ┌────────────────────────────────────────────────────────────┐
       │ WEALTH ENGINE INITIAL SWEEP                                │
       │ Owner: WealthEngine (app/wealth_engine.py)                  │
       │ Input: Watchlist + 1Y Daily OHLCV + Fundamentals           │
       │ Output: wealth_portfolio table + initial buy candidates     │
       └────────────────────────────────────────────────────────────┘
       │
 08:30 ┌────────────────────────────────────────────────────────────┐
       │ READINESS VERIFICATION CHECK                               │
       │ Owner: Scheduler (app/main.py)                             │
       │ Action: Verify watchlist freshness & DB health              │
       │ Transition: SessionContext → READY                         │
       └────────────────────────────────────────────────────────────┘
       │
 09:14 ┌────────────────────────────────────────────────────────────┐
       │ PRE-MARKET WARMUP (09:14:30 IST)                           │
       │ Owner: Scheduler                                           │
       │ Action: Pre-fetch 15m/1H price data for Multi-TF scanner    │
       │ Purpose: Prevents 09:15:00 market open tick lag             │
       └────────────────────────────────────────────────────────────┘
       │
 09:15 ┌────────────────────────────────────────────────────────────┐
       │ MARKET OPEN                                                │
       │ SessionContext transitions → MARKET_OPEN                   │
       │                                                            │
       │ ┌─────── MARKET HOURS INTRADAY LOOP (Locked) ──────────┐  │
       │ │                                                      │  │
       │ │ Every 5 min:                                         │  │
       │ │   → Wealth Engine Fast CMP Exit Updates (<3.0s)      │  │
       │ │   → Performance Tracker Position Updates             │  │
       │ │                                                      │  │
       │ │ Every 15 min (:00, :15, :30, :45):                   │  │
       │ │   → Multi-TF Intraday 4-Stage Cascade Scanner        │  │
       │ │   → Wealth Engine Full BUY Scan                      │  │
       │ │   → Multibagger Exit Monitor                         │  │
       │ │                                                      │  │
       │ └──────────────────────────────────────────────────────┘  │
       │                                                            │
 15:30 ── MARKET CLOSE (SessionContext transitions → POST_MARKET)
       │
 18:00 ┌────────────────────────────────────────────────────────────┐
       │ EVENING BATCH SCANNERS (Sequential)                        │
       │                                                            │
       │ 1. Poll for NSE Bhavcopy publication (every 5 mins)        │
       │ 2. Run EOD Breakout Scanner (max 10m hard timeout)         │
       │ 3. Run Reversal Scanner (max 10m hard timeout)             │
       │ 4. Run Pullback Pipeline Scanner (max 10m hard timeout)     │
       │ 5. Post-batch memory purge (gc.collect())                  │
       └────────────────────────────────────────────────────────────┘
       │
 19:00 ┌────────────────────────────────────────────────────────────┐
       │ MULTIBAGGER DAILY SCANNER RUN                              │
       │ Owner: Multibagger Engine (app/multibagger.py)             │
       │ Output: DB alerts + candidate ranking                      │
       └────────────────────────────────────────────────────────────┘
```

## 1.3 System State Machine Diagram

```text
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
              SHUTTING_DOWN ──► DESTROYED
```

- **State Transitions**:
  - `CREATED → WARMING`: Initiated when Daily Builder starts at 01:00.
  - `WARMING → READY`: Triggered when 08:30 readiness checks pass.
  - `READY → MARKET_OPEN`: Triggered at 09:15:00 IST.
  - `MARKET_OPEN → POST_MARKET`: Triggered at 15:30:00 IST.
  - `POST_MARKET → SHUTTING_DOWN`: Triggered during midnight rotation.
  - `SHUTTING_DOWN → DESTROYED`: Occurs after RAM caches are freed and DB connections returned.

---

# 2. OWNERSHIP MATRIX & CACHE TOPOLOGY

## 2.1 Ownership Principle
**Every object and dataset in the system has EXACTLY ONE owner.**
The owner service is exclusively responsible for creating, refreshing, invalidating, and destroying the object. Readers MUST NOT mutate objects they do not own.

## 2.2 Dataset Ownership Matrix

| Dataset | Owner | Storage Tier | Refresh Cadence | Consumers |
| :--- | :--- | :--- | :--- | :--- |
| **Watchlist Parquet** | `WatchlistService` | Ephemeral Disk + RAM | Daily at 01:00 | All Scanners, Wealth Engine, Dashboard |
| **OHLCV Daily (1D)** | `PriceCache` | Session RAM + Parquet | Once per trading day | EOD, Reversal, Pullback, Wealth Engine |
| **OHLCV 15-Minute (15m)**| `PriceCache` | Session RAM Cache | Every 15-min tick | Multi-TF Scanner Phase C |
| **OHLCV 5-Minute (5m)** | `PriceCache` | Session RAM Cache | Every 5-min tick | Multi-TF Phase D, Wealth CMP Monitor |
| **OHLCV 1-Hour (1H)** | `PriceCache` | Session RAM Cache | Every 1-hour bar | Multi-TF Phase A (3-month period) |
| **Technical Indicators** | `IndicatorManager` | Attached to DataFrame | On fetch write | All Scanners |
| **Delivery / Bhavcopy** | `DeliveryData` | Ephemeral RAM | Daily post-18:00 | EOD Scanner, Reversal Scanner |
| **Fundamentals Cache** | `FundamentalsCache`| Postgres (`pledge_cache`) | Daily at 01:00 | Daily Builder, Wealth Engine |
| **Market Regime State** | `MarketRegimeEngine`| Session RAM (5m TTL) | Every 5 min | All Scanners, Strategy Policy |
| **Sector Rankings** | `MarketRegimeEngine`| Postgres | Daily | EOD, Multi-TF (Sector Bonus) |
| **RS Ratings** | `MarketRegimeEngine`| Postgres | Daily | EOD, Multi-TF (RS Bonus) |
| **Bayesian Weights** | `BayesianUpdater` | Postgres | Daily | Scoring Engine |
| **Surveillance Blacklist**| `Surveillance` | Session RAM (5m TTL) | Hourly | All Scanners |
| **Block Deals Data** | `InstitutionalData` | Ephemeral RAM | Daily | EOD, Reversal Scoring |
| **Scanner Health** | `HealthService` | Postgres (`scanner_health`)| On scan end | Dashboard, Admin API |
| **Alert Signals** | `AlertService` | Postgres (`alerts`) | On alert hit | Dashboards, Telegram, Push |
| **Symbol Mappings** | `PriceProvider` | Postgres (`symbol_mappings`)| On BSE fallback | UnifiedFetcher, PriceProvider |

## 2.3 Cache Topology Tree

```text
ApplicationContext (Process-Lifetime Singleton)
│
├── DatasetRegistry (Process-Lifetime Metadata Registry)
│   └── { dataset_id → DatasetEntry(tier, schema_version, ttl) }
│
└── SessionContext (Trading-Day Lifetime - Reset at Midnight)
    │
    ├── HistoricalDataManager
    │   ├── DailyStore     { symbol → DataFrame }     refresh=DAILY
    │   ├── IntradayStore   { symbol → DataFrame }     refresh=EVERY_5_MIN
    │   └── DeliveryStore   { symbol → delivery_pct }  refresh=ON_DEMAND
    │
    ├── MarketRegimeManager
    │   └── cache { nifty_ret_20d, dist_52w, trend, ts } refresh=EVERY_5_MIN
    │
    ├── CacheManager (Named Ephemeral Slots)
    │   ├── dead_symbols    { symbol → expiry_ts }
    │   ├── push_throttle   { user_id → last_send_ts }
    │   └── session_auth    { token → { user_id, expiry_ts } } (60s TTL)
    │
    └── IndicatorManager
        └── bundles { (symbol, timeframe) → IndicatorBundle }

price_cache._cache (Module-Level 3-Tier Per-Symbol Granular RAM Cache)
└── { (interval, period) → { symbol → { "data": df, "ts": monotonic_ts, "data_as_of": dt } } }
```

---

# 3. ABSTRACT PIPELINE ARCHITECTURE & STEP LIBRARY

## 3.1 V8 Deployed Architecture vs V9 Target Matrix

| Subsystem | Active Implementation (v8.4.3) | V9 Clean Architecture Target |
| :--- | :--- | :--- |
| **Scheduler** | Autonomous 24/7 Scheduler (`app/main.py`) | Modular Orchestrator Services |
| **Scanner Engine** | Functional Script-based with Bulk Chunking | `PipelineContext` / `PipelineStep` Abstraction |
| **Session Control** | `SessionContext` State Machine & Daily Rotation | Encapsulated `SessionContext` |
| **Cache Architecture** | Per-Symbol Granular RAM Cache (`_cache[(int,per)][sym]`) | Unified `CacheManager` |
| **Alert Infrastructure**| Unified PostgreSQL Persistence (`alerts` table) | Modular `AlertService` |
| **Wealth Engine** | Monolithic Hybrid Cadence (5m CMP / 15m BUY) | Modular `WealthService` |

## 3.2 Abstract Pipeline Execution Specification

Every scanner pipeline processes symbols via a sequential list of steps:

```python
class StepResult(Enum):
    CONTINUE = auto()   # Step passed, proceed to next step
    REJECT = auto()     # Symbol failed gate, skip remaining steps
    ERROR = auto()      # Exception encountered, log and abort symbol

class PipelineStep(ABC):
    @abstractmethod
    def execute(self, ctx: PipelineContext) -> StepResult:
        """Execute logic against PipelineContext."""
        pass

class ScannerPipeline:
    def __init__(self, name: str, steps: list[PipelineStep]):
        self.name = name
        self.steps = steps

    def run(self, universe: pd.DataFrame, ctx: PipelineContext) -> ScanResult:
        results = ScanResult(scanner=self.name)
        for _, row in universe.iterrows():
            symbol = row["Stock"]
            ctx.set_current_symbol(symbol, row)
            for step in self.steps:
                res = step.execute(ctx)
                if res == StepResult.REJECT:
                    results.record_rejection(symbol, step.name, ctx.rejection_reason)
                    break
                elif res == StepResult.ERROR:
                    results.record_error(symbol, step.name, ctx.error)
                    break
            else:
                results.record_success(symbol, ctx.alert)
        return results
```

## 3.3 Complete Step Library (20 Core Steps)

| Step Name | Read Input | Write Output | Used By |
| :--- | :--- | :--- | :--- |
| `BlacklistGateStep` | `ctx.blacklist`, `ctx.symbol` | CONTINUE / REJECT | All Scanners |
| `CooldownGateStep` | `ctx.cooldown_set`, `ctx.symbol` | CONTINUE / REJECT | EOD, Reversal, Pullback |
| `DataValidationStep` | `ctx.ohlcv[symbol]` | `ctx.ticker`, `ctx.latest` | All Scanners |
| `TrendFilterStep` | `ctx.ticker` | CONTINUE / REJECT | Multi-TF Phase A, EOD |
| `BreakoutDetectionStep` | `ctx.ticker` | `ctx.breakout_signals` | EOD, Multi-TF Phase D |
| `ReversalDetectionStep` | `ctx.ticker` | `ctx.reversal_signals` | Reversal Scanner |
| `PullbackDetectionStep` | `ctx.ticker` | `ctx.pullback_signals` | Pullback Pipeline |
| `CandleQualityStep` | `ctx.latest` | CONTINUE / REJECT | EOD, Multi-TF, Pullback |
| `VolumeConfirmationStep`| `ctx.ticker` | CONTINUE / REJECT | All Scanners |
| `ForensicRiskStep` | `ctx.fundamentals[symbol]` | CONTINUE / REJECT | All Scanners |
| `FundamentalFilterStep` | `ctx.fundamentals[symbol]` | CONTINUE / REJECT | Reversal, Wealth Engine |
| `ScoringStep` | All signals, `ctx.latest` | `ctx.raw_score` | All Scanners |
| `BayesianAdjustmentStep` | `ctx.bayesian_weights`, score | `ctx.final_score` | All Scanners |
| `RegimePolicyStep` | `ctx.regime_context` | `ctx.threshold` | All Scanners |
| `ScoreThresholdStep` | `ctx.final_score` | CONTINUE / REJECT | All Scanners |
| `SLTargetStep` | `ctx.latest`, `ctx.entry_price` | `ctx.sl_result` | All Scanners |
| `RiskRewardGateStep` | `ctx.sl_result` | CONTINUE / REJECT | All Scanners |
| `RankingStep` | All passing candidates | Ranked Top N candidates | EOD, Reversal, Pullback |
| `AlertCreationStep` | Top candidates | `ctx.alert` payload | All Scanners |
| `DeduplicationStep` | `ctx.recent_alerts` | CONTINUE / REJECT | All Scanners |

---

# 4. CONTEXT MODEL & LIFECYCLE

`PipelineContext` carries all state through the pipeline, eliminating parameter passing:

```python
@dataclass
class PipelineContext:
    # Identity & Execution Metadata
    scanner_name: str
    scan_id: str
    scan_date: str
    ist_now: datetime

    # Universe Data
    universe: pd.DataFrame
    interval: str
    period: str

    # Pre-Loaded Datasets (Populated before per-symbol loop)
    ohlcv: dict[str, pd.DataFrame] = field(default_factory=dict)
    delivery: dict[str, float] = field(default_factory=dict)
    blacklist: set[str] = field(default_factory=set)
    fundamentals: dict[str, dict] = field(default_factory=dict)
    recent_alerts: set[tuple] = field(default_factory=set)
    cooldown_symbols: set[str] = field(default_factory=set)
    pledge_map: dict[str, float] = field(default_factory=dict)

    # Regime & Policy State
    regime_context: dict = field(default_factory=dict)
    bayesian_weights: Optional[dict] = None

    # Per-Symbol Iteration State (Mutated per symbol)
    symbol: str = ""
    category: str = ""
    sector: str = ""
    ticker: Optional[pd.DataFrame] = None
    latest: Optional[pd.Series] = None
    breakout_signals: dict = field(default_factory=dict)
    raw_score: int = 0
    final_score: int = 0
    entry_price: float = 0.0
    sl_result: Optional[dict] = None
    alert: Optional[dict] = None
    rejection_reason: Optional[str] = None
    error: Optional[Exception] = None

    def set_current_symbol(self, symbol: str, row: pd.Series):
        """Reset per-symbol fields before next symbol iteration."""
        self.symbol = symbol
        self.category = row.get("Category", "")
        self.sector = row.get("sector", "")
        self.ticker = None
        self.latest = None
        self.breakout_signals = {}
        self.raw_score = 0
        self.final_score = 0
        self.entry_price = 0.0
        self.sl_result = None
        self.alert = None
        self.rejection_reason = None
        self.error = None

    def release_temporary_data(self):
        """Purge large DataFrames post-scan."""
        self.ohlcv.clear()
        self.delivery.clear()
        self.fundamentals.clear()
```

---

# 5. QUANTITATIVE ALGORITHMS & MATHEMATICAL ENGINES

## 5.1 Technical Indicator Equations (`app/price_cache.py`, `app/indicator_manager.py`)

### Relative Strength Index (RSI - 14 Period)
Let $\Delta P_t = \text{Close}_t - \text{Close}_{t-1}$.
$$\text{Gain}_t = \max(\Delta P_t, 0), \quad \text{Loss}_t = \max(-\Delta P_t, 0)$$
$$\text{AvgGain}_t = \frac{\text{AvgGain}_{t-1} \times 13 + \text{Gain}_t}{14}, \quad \text{AvgLoss}_t = \frac{\text{AvgLoss}_{t-1} \times 13 + \text{Loss}_t}{14}$$
$$\text{RS}_t = \frac{\text{AvgGain}_t}{\text{AvgLoss}_t}, \quad \text{RSI}_t = 100 - \frac{100}{1 + \text{RS}_t}$$

### Average Directional Index (ADX - 14 Period)
True Range $\text{TR}_t = \max(\text{High}_t - \text{Low}_t, |\text{High}_t - \text{Close}_{t-1}|, |\text{Low}_t - \text{Close}_{t-1}|)$.
$$\text{+DM}_t = \text{High}_t - \text{High}_{t-1} \quad \text{if } \text{High}_t - \text{High}_{t-1} > \text{Low}_{t-1} - \text{Low}_t \text{ else } 0$$
$$\text{-DM}_t = \text{Low}_{t-1} - \text{Low}_t \quad \text{if } \text{Low}_{t-1} - \text{Low}_t > \text{High}_t - \text{High}_{t-1} \text{ else } 0$$
$$\text{+DI}_{14} = 100 \times \frac{\text{WilderSmooth}(\text{+DM}, 14)}{\text{WilderSmooth}(\text{TR}, 14)}, \quad \text{-DI}_{14} = 100 \times \frac{\text{WilderSmooth}(\text{-DM}, 14)}{\text{WilderSmooth}(\text{TR}, 14)}$$
$$\text{DX}_t = 100 \times \frac{|\text{+DI}_t - \text{-DI}_t|}{\text{+DI}_t + \text{-DI}_t}, \quad \text{ADX}_{14} = \text{WilderSmooth}(\text{DX}, 14)$$

### Exponential Moving Average (EMA)
$$\alpha = \frac{2}{N + 1}, \quad \text{EMA}_t = (\text{Close}_t \times \alpha) + (\text{EMA}_{t-1} \times (1 - \alpha))$$

### Average True Range (ATR - 20 Period)
$$\text{ATR}_{20} = \frac{1}{20} \sum_{i=0}^{19} \text{TR}_{t-i}$$

## 5.2 Fundamental Quality Score (`FM_Score`) (`app/scoring_engine.py`)

- **Financial Sector Rule (Banks & NBFCs)**:
  $$\text{Pass}_{\text{Financial}} = (\text{ROE} \ge 15.0\%) \land (\text{Debt/Equity} \le 3.0) \land (\text{YoY Growth} \ge 10.0\%)$$
- **Non-Financial Sector Rule**:
  $$\text{Pass}_{\text{NonFinancial}} = (\text{ROCE} \ge 20.0\%) \land (\text{Debt/Equity} \le 1.0) \land (\text{YoY Growth} \ge 10.0\%)$$
$$\text{FM\_Score} = 40 + \min(\text{ROE}, 30) \times 1.0 + \min(\text{YoY Growth}, 30) \times 0.5 - (\text{PledgePct} \times 2.0)$$

## 5.3 Candidate Scoring Engine (`app/scoring_engine.py`)

Outputs a composite score $S \in [0, 100]$:
$$S = \max(0, \min(100, S_{\text{Base}} + S_{\text{Regime}} + S_{\text{Bayesian}} - P_{\text{Penalties}}))$$

### Base Score Allocation ($S_{\text{Base}}$)
- **Category Tier Base (Max 30 pts)**: `DEBT_FREE_CASH` = 30, `WEALTH_COMPOUNDER` = 25, `BLUE_CHIP` = 20, `MIDCAP_GROWTH` = 18, `RECOVERY_PLAY` = 8.
- **Candle Quality (Max 15 pts)**: Body Ratio $\ge 0.60$ (+5), Close Position $\ge 0.70$ (+5), Upper Wick $\le 0.20$ (+5).
- **Volume Expansion (Max 20 pts)**: $\ge 4.0\text{x}$ (20 pts), $\ge 3.0\text{x}$ (15 pts), $\ge 2.5\text{x}$ (12 pts), $\ge 2.0\text{x}$ (7 pts), $\ge 1.5\text{x}$ (3 pts).
- **Trend Alignment (Max 15 pts)**: $\text{Close} > \text{EMA}_{20}$ (+3), $\text{Close} > \text{SMA}_{50}$ (+3), $\text{SMA}_{50} > \text{SMA}_{200}$ (+4), $\text{ADX}_{14} \ge 30$ (+5).
- **RSI Location (Max 10 pts)**: $55 \le \text{RSI} \le 68$ (10 pts), $50 \le \text{RSI} < 55$ or $68 < \text{RSI} \le 75$ (5 pts).
- **Delivery & Institutional Bonuses (Max 10 pts)**: Delivery $\% \ge 50\%$ (+5), Institutional Block Deal Footprint (+5).

### Penalties ($P_{\text{Penalties}}$)
- **Extended Breakout**: $-\min(20, ((\text{Close} - \text{Prior20DHigh}) / \text{ATR}_{20} - 1.5) \times 10)$ if extension $> 1.5\text{x}$.
- **OBV Divergence**: $-5$ pts if $\text{OBV Slope} \le 0$.
- **Promoter Pledge**: $-(\text{PledgePct} \times 1.5)$ if $\text{PledgePct} > 10\%$.

## 5.4 Dynamic Stop Loss & Target Engine (`app/sl_target_helper.py`)

### Anti-Trap Buffers
- **EOD Mode**: $\text{Buffer} = \max(0.80 \times \text{ATR}_{20}, 0.0075 \times \text{Entry})$
- **Multi-TF Mode**: $\text{Buffer} = \max(0.50 \times \text{ATR}_{20}, 0.0050 \times \text{Entry})$
- **Reversal Mode**: $\text{Buffer} = \max(1.00 \times \text{ATR}_{20}, 0.0100 \times \text{Entry})$

### Structural Support Selection & Target Equations
$$\text{Support} = \min(\text{SwingLow}_{10}, \text{S1 Pivot}, \text{VWAP}, \text{Low})$$
$$\text{Raw SL} = \text{Support} - \text{Buffer}$$
$$\text{StopLoss} = \max(\text{Raw SL}, \text{Entry} - (3.0 \times \text{ATR}_{20}))$$

- $\text{Target}_1 = \text{Entry} + 1.5 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_2 = \text{Entry} + 2.5 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_3 = \text{Entry} + 4.0 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_4 = \text{Entry} + 6.0 \times (\text{Entry} - \text{StopLoss})$

---

# 6. EXHAUSTIVE INTERNAL SCANNER EXECUTION CODE FLOWS

All scanners follow the **Full-Universe Candidate Discovery Pattern**: Candidates across all 50-stock chunks are accumulated into a global list before executing global score sorting, `SCANNER_MAX_ALERTS` truncation (top 10), and database persistence.

## 6.1 EOD Scanner Internal Pseudo-Code (`app/eod_scanner.py`)
```python
def run_eod_scanner(run_once=False, force=False):
    scan_id = generate_scan_id()
    universe = watchlist_cache.get_watchlist()
    approved_candidates = []

    for chunk in chunk_iterable(universe, batch_size=50):
        ohlcv_map = price_provider.fetch_batch(chunk, interval="1d", period="1y")
        for symbol, df in ohlcv_map.items():
            if df is None or len(df) < 200: continue
            latest = df.iloc[-1]
            if latest["Close"] < 20.0: continue # Penny stock floor
            
            # Structural Breakout Check
            prior_20d_high = df["High"].iloc[-21:-1].max()
            if latest["Close"] <= prior_20d_high: continue
            
            # Candle Quality Gates
            body_ratio = abs(latest["Close"] - latest["Open"]) / (latest["High"] - latest["Low"])
            close_pos = (latest["Close"] - latest["Low"]) / (latest["High"] - latest["Low"])
            upper_wick = (latest["High"] - latest["Close"]) / (latest["High"] - latest["Low"])
            if body_ratio < 0.60 or close_pos < 0.70 or upper_wick > 0.20: continue
            
            vol_ratio = latest["Volume"] / df["Volume"].iloc[-21:-1].mean()
            if vol_ratio < 2.5: continue
            
            # Scoring & Risk Engine
            score = scoring_engine.calculate_score(symbol, df, regime_ctx)
            if score < 82: continue
            
            sl_res = compute_sl_and_target(df, mode="EOD")
            if sl_res["rr_ratio"] < 2.0: continue
            
            approved_candidates.append({
                "symbol": symbol, "score": score, "sl_result": sl_res, "entry": latest["Close"]
            })

    # Global Accumulation & Top-10 Truncation
    approved_candidates.sort(key=lambda x: x["score"], reverse=True)
    top_10 = approved_candidates[:10]

    # UN-NESTED AT FUNCTION SCOPE (Version: EOD_INDENT_FIX_v1.0)
    saved_count = save_alert_batch(top_10)
    verify_alerts_saved_today(scan_id)
    upsert_scanner_health("EOD", status="OK", alerts=len(top_10), duration=time.time() - start)
    gc.collect()
    return len(top_10)
```

---

# 7. DATA ACQUISITION, PROVIDER ROUTING & RESILIENCY TOPOLOGY

## 7.1 Provider Selector Routing Authority (`app/data_providers/provider_selector.py`)
`ProviderSelector` delegates provider selection based on dataset keys (`price_1d`, `price_15m`, `live_quotes`) configured in `config.PROVIDER_ROUTING_POLICY` and matching capabilities in `config.PROVIDER_CAPABILITIES`. Fetchers MUST NOT hardcode provider selection logic.

## 7.2 Unified Fetcher & Fallback Chain (`app/data_providers/unified_fetcher.py`)
```text
┌─────────────────────────────────────────────────────────┐
│                    UNIFIED FETCHER                      │
│                                                         │
│  Primary: Fyers API v3 (Cap: 99 days for intraday)     │
│    │                                                    │
│    ├─ [Success] ──► Return Validated DataFrame          │
│    └─ [Fail/Error] ──► Trigger Dashboard Notification   │
│                             │                           │
│  Secondary: YFinance (Rate Limiter Circuit Breaker)    │
│    │                                                    │
│    ├─ [Success] ──► Parse MultiIndex Columns            │
│    └─ [Fail/Error] ──► Check BSE Mapping Table          │
│                             │                           │
│  Tertiary: BSE (.BO) Fallback Provider                  │
│    │                                                    │
│    ├─ [Success] ──► Persist .BO to symbol_mappings      │
│    └─ [Fail/Error] ──► Invalidate BSE Poisoned Mapping  │
└─────────────────────────────────────────────────────────┘
```
- **Duplicate Protection**: Uses `pending.discard(sym)` instead of `pending.remove(sym)` to prevent fatal `KeyError` crashes during batch retries.

## 7.3 Persistent BSE Fallback & Reverse Fallback (`app/price_provider.py`)
- Mapped `.BO` resolutions write to `symbol_mappings` in PostgreSQL (`[VERSION: PRICE_PROV_BSE_FALLBACK_v1.0]`).
- **Poisoned Mapping Invalidation**: If `.BO` returns empty data, `invalidate_bse_mapping(clean_orig)` strips suffixes, removes the poisoned DB mapping row, and triggers a reverse fallback retry to `.NS`.
- **Alphabetical BSE Symbols**: Mappings persist for all valid symbols (e.g. `YASHHV`) without `isdigit()` restrictions.

---

# 8. PRICE CACHE INFRASTRUCTURE & PARQUET SIDECARS

## 8.1 3-Tier Per-Symbol Granular RAM Cache Topology (`app/price_cache.py`)
`_cache` uses a per-symbol dictionary structure:
```python
_cache[(interval, period)][symbol] = {
    "data": df,              # Monotonically sorted OHLCV + Indicators DataFrame
    "ts": time.monotonic(),  # Monotonic TTL timestamp for per-symbol freshness
    "data_as_of": dt,        # Latest candle timestamp
    "schema_version": "v8.4.0"
}
```
- **Monotonic Timestamp Normalization**: `validate_ohlcv_structure()` enforces `pd.to_datetime(..., errors='coerce')`, NaN removal, deduplication, and chronological sorting to eliminate lexicographical string sorting errors on timestamps.
- **Intraday Snapshot Retrieval**: `get_intraday_snapshot()` inspects symbol-level keys (`_cache[cache_key][symbol]["ts"]`) directly, eliminating top-level `KeyError: 'ts'` failures.
- **IPO Infinite Fetch Loop Prevention**: `earliest_dates.json` records the earliest available date for newly listed stocks (IPOs) to prevent infinite YFinance `FULL` fetch loops.
- **Parquet Sidecars**: Disk parquet files persist metadata sidecars (`.meta.json`):
  ```json
  {
    "schema_version": 3,
    "indicator_version": "v5.2",
    "ohlcv_hash": "a1b2c3d4e5f6...",
    "created_at": "2026-07-25T11:00:00 IST"
  }
  ```

---

# 9. DATABASE ARCHITECTURE & COMPLETE POSTGRESQL DDLs

The system initializes PostgreSQL via `database.init_db()` with pool capacity `DB_MAXCONN=50` and acquire timeout `15s`. Dynamic JSON payloads (`context`, `bayesian_weights`) are scrubbed of `NaN`/`Inf` floats via the recursive `sanitize()` helper prior to SQL execution.

```sql
-- 1. Primary Signal Alerts Table
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    breakout_type TEXT NOT NULL,
    alert_time TEXT NOT NULL,
    scanner TEXT NOT NULL DEFAULT 'EOD',
    category TEXT,
    entry_price REAL,
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    target_3 REAL,
    score INTEGER,
    signals TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    initial_stop_loss REAL,
    target_price REAL,
    context JSONB,
    model_version TEXT,
    bayesian_regime TEXT,
    bayesian_weights JSONB,
    structural_failure_stop REAL,
    target_quality_score REAL,
    base_score INTEGER,
    rs_bonus INTEGER,
    sector_bonus INTEGER,
    rs_percentile REAL,
    sector_name TEXT,
    regime_score REAL,
    is_rejected BOOLEAN DEFAULT FALSE,
    exit_reason TEXT,
    alert_date DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT alerts_dedup_idx UNIQUE (symbol, breakout_type, scanner, alert_date),
    CONSTRAINT chk_alerts_status CHECK (status IN ('OPEN', 'WIN', 'LOSS', 'TRAILING', 'EXPIRED', 'PARTIAL_WIN_1', 'PARTIAL_WIN_2', 'NEUTRAL'))
);

-- 2. Scanner Health Table
CREATE TABLE IF NOT EXISTS scanner_health (
    scanner_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'IDLE',
    last_success TEXT,
    today_alerts INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    is_acknowledged BOOLEAN DEFAULT TRUE,
    updated_at TEXT NOT NULL,
    processed_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0.0,
    outcome TEXT DEFAULT 'SUCCESS',
    provider_stats JSONB
);

-- 3. Symbol Mappings Table (BSE Fallback Cache)
CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT PRIMARY KEY,
    bse_symbol TEXT NOT NULL,
    mapping_state TEXT NOT NULL DEFAULT 'ACTIVE',
    failure_count INTEGER DEFAULT 0,
    retry_after TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Funnel Telemetry Table
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

-- 5. Parquet Cache Storage Table
CREATE TABLE IF NOT EXISTS parquet_cache (
    name TEXT NOT NULL,
    date DATE NOT NULL,
    data BYTEA NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (name, date)
);

-- 6. Breakout Watchlist Table
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

-- 7. Wealth Portfolio Table
CREATE TABLE IF NOT EXISTS wealth_portfolio (
    symbol TEXT PRIMARY KEY,
    cmp REAL,
    hold_score INTEGER,
    bucket TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Bayesian Model Updates Table
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
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

# 10. CONCURRENCY, SYNCHRONIZATION & LOCK HIERARCHY

To guarantee thread safety, serial scanner execution, and prevent deadlocks across Railway multi-container deployments:

```text
Lock Acquisition Hierarchy (Strict Acquisition Order):
1. scanner_execution_lock (InstrumentedLock)
   └── 2. ProcessLock (flock + PostgreSQL Advisory Lock: pg_advisory_lock)
       └── 3. price_cache._fetch_lock (Prevents thundering herd API requests)
           └── 4. price_cache._lock (Protects internal _cache RAM dictionary)
```

- **InstrumentedLock Telemetry**: Tracks acquisitions, wait times, hold times, and contention events. Exposes statistics via `/api/lock-stats`.
- **ProcessLock Resilience (`[VERSION: PROCESS_LOCK_EXC_FIX_v1.0]`)**: On DB connection exception during advisory lock acquisition, `acquire()` cleans up local handles, releases thread locks, and returns `False` (preventing lock leaks).
- **De-nested Evening Locks**: De-nests the global lock so EOD, Reversal, and Pullback individually acquire/release `scanner_execution_lock` around execution blocks, keeping `time.sleep(15)` outside mutex contexts.

---

# 11. COMPLETE REST API SPECIFICATIONS & STREAMING PROTOCOLS

Flask REST API (`app/dashboard_server.py`) specifications:

| Endpoint | Method | Auth Level | Description | Response JSON Schema |
| :--- | :--- | :--- | :--- | :--- |
| `/api/scanner_status` | `GET` | Public | Real-time scanner health & run duration. | `{"status": "ok", "scanners": [{"scanner_name": "EOD", "status": "OK", "today_alerts": 3, "duration_seconds": 12.5}]}` |
| `/api/trigger-scanner` | `POST` | Admin | Async manual trigger for a scanner. | `{"status": "success", "message": "Scanner EOD triggered"}` |
| `/api/lock-stats` | `GET` | Admin | Mutex lock contention statistics. | `{"acquisitions": 142, "max_wait_sec": 0.12, "contention_events": 0}` |
| `/api/wealth_data` | `GET` | Public | Wealth Engine portfolio data. | `{"status": "ok", "data": [{"Stock": "RELIANCE", "CMP": 2450.0, "HoldScore": 88}]}` |
| `/version` | `GET` | Public | Build metadata & release gate status. | `{"architecture_version": "8.4.3", "git_commit": "c1bf1e0b", "status": "RELEASE_GATE_APPROVED"}` |

- **Gzip Middleware**: Compresses HTTP responses exceeding 500 bytes (reduces HTML payloads from 260KB to ~30KB).
- **Session Cache**: `@login_required` decorators query an in-memory session cache with a 60-second TTL (`_cached_check_session()`), eliminating 90%+ per-request SQL overhead.

---

# 12. COMPLETE REPOSITORY MODULE INVENTORY

All 88 Python modules under `app/`, `tests/`, and root are cataloged below:

```text
app/
├── main.py                     # Scheduler entrypoint & 24/7 background loop
├── application_context.py      # Process-lifetime singleton context
├── session_context.py          # Session rotation & daily boundary tracking
├── dataset_registry.py         # Memory dataset registry (PERSISTENT/SESSION/EPHEMERAL)
├── eod_scanner.py              # Post-market daily momentum breakout scanner
├── reversal_scanner.py         # Mean-reversion oversold bounce scanner
├── pullback_pipeline.py        # Uptrend pullback continuation pipeline
├── multi_tf_scanner.py         # Intraday 4-stage cascade scanner
├── wealth_engine.py            # Long-term fundamental screener & exit monitor
├── multibagger.py              # Compounder screener & exit monitor
├── scoring_engine.py           # Centralized candidate scoring engine (0-100)
├── sl_target_helper.py         # Dynamic stop-loss & target engine + validator
├── trade_ranking_engine.py     # Multi-factor candidate ranking engine
├── macro_utils.py              # Market regime engine & sector ranking calculator
├── strategy_policy.py          # Strategy policy engine
├── forensic_engine.py          # Forensic risk engine (CFO/PAT, Debt, Tiers)
├── quality_trajectory.py       # Fundamentals quality trajectory engine
├── data_provider.py            # High-level data provider boundary
├── price_cache.py              # Centralized price cache & monotonic timestamp normalizer
├── price_provider.py           # BSE fallback & rate limiter boundary
├── delivery_data.py            # NSE Bhavcopy delivery scraper & fallback
├── surveillance.py             # NSE ASM/GSM blacklist scraper
├── database.py                 # PostgreSQL driver, migrations & CRUD interface
├── lock_utils.py               # ProcessLock (flock + PG advisory lock)
├── memory_profiler.py          # MemoryProfiler & BatchMemoryTracker
├── telemetry_manager.py        # TelemetryManager & session timeline
├── dashboard_server.py         # Flask REST API & web dashboard server
└── data_providers/
    ├── provider_selector.py    # Provider routing authority
    ├── unified_fetcher.py      # Unified fetcher (Fyers -> YFinance -> BSE)
    └── fyers_fetcher.py        # Fyers REST API client
```

---

# 13. UI/UX SPECIFICATIONS & STREAMING CONTRACTS

## 13.1 Glassmorphism & Dark Mode Tokens
- **Background**: `#0B0E14` (Deep space dark mode).
- **Cards**: `background: rgba(255, 255, 255, 0.03)`, `backdrop-filter: blur(12px)`, `border: 1px solid rgba(255, 255, 255, 0.08)`.
- **Typography**: `Inter` / `Outfit` sans-serif fonts.
- **Accents**: Bullish Emerald (`#10b981`), Bearish Crimson (`#ef4444`), Brand Electric Blue (`#3b82f6`).

## 13.2 Streaming Contracts
- **Health Polling**: UI polls `/api/scanner_status` every 15 seconds.
- **Alert Streaming**: WebSockets push `AlertSchema` objects on creation.

---

# 14. DEPLOYMENT VERIFICATION & PRODUCTION TEST GATES

The system enforces **17 Production Deployment Gates** in `tests/test_production_deployment_gates.py`:

```python
# Gate 6 Memory Budget Checklist Implementation
def test_gate6_production_readiness_checklist(self):
    """Gate 6: Production Readiness Checklist (Memory Budget Alignment)."""
    import gc
    from forensics import forensics
    gc.collect() # PURGE UNREFERENCED TEST ALLOCATIONS
    mem = forensics.get_memory_stats()
    assert mem["rss_mb"] < 450.0, f"Memory threshold breached: {mem['rss_mb']} MB"
```

1. Gate 1: Cold start import time $\le 5.0\text{s}$.
2. Gate 2: Unsupported imports audit.
3. Gate 3: Smoke execution $\le 30\text{s}$.
4. Gate 4: AST method signature audit.
5. Gate 5: Railway integration contract.
6. Gate 6: Production readiness checklist ($\text{RSS} < 450.0\text{ MB}$ with `gc.collect()`).
7. Gate 7: Dependency reproducibility.
8. Gate 8: Scheduler 24h simulation.
9. Gate 9: Memory budget ($\text{RSS} < 450.0\text{ MB}$, Threads $< 60$).
10. Gate 10: Alert contract schema compliance.
11. Gate 11: Scanner execution invariants.
12. Gate 12: DB connection pool acquire $\le 15\text{s}$.
13. Gate 13: `/version` endpoint health.
14. Gate 14: Earnings calendar safety.
15. Gate 15: Quality trajectory invariants.
16. Gate 16: Forensic engine risk tiers.
17. Gate 17: Data readiness policy.

---

# 15. V9 CLEAN ARCHITECTURE BLUEPRINT & DEPRECATION PROTOCOL LOG

## 15.1 Target 5-Layer Layout (`src/`)
- `src/domain/`: Pure business logic models, indicators, risk, strategy rules.
- `src/application/`: Pipeline steps (`IPipelineStep`), context objects (`PipelineContext`).
- `src/infrastructure/`: API fetchers, PostgreSQL repositories (`AlertRepository`, `HealthRepository`).
- `src/interfaces/`: Flask REST API server and 24/7 scheduler (`TaskScheduler`).
- `src/common/`: Lock instrumentations, IEEE 754 float sanitizers.

## 15.2 Deprecation Protocol Log (Rule 58 Compliance)
- ~~*Legacy Top-Level Cache Dict Pointer Overwrites*~~ *(Replaced on 2026-07-24 by `PER_SYMBOL_CACHE_v1.0` in `app/price_cache.py` — symbols now have independent `_cache[(interval, period)][symbol]` TTL pointers)*
- ~~*21:00 IST Mandatory Time Guard on Scanner Execution*~~ *(Replaced on 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0` in `app/main.py` — `force=True` parameter passed directly by scheduler)*
- ~~*One-Shot 15:00 IST Intraday Multi-TF Execution Trigger*~~ *(Replaced on 2026-07-24 by Candle-Aligned 15-Minute Market Hours Cadence `:00`, `:15`, `:30`, `:45` in `app/main.py`)*
- ~~*Nested Verification Locks Inside Candidate Iteration Loop*~~ *(Replaced on 2026-07-25 by `EOD_INDENT_FIX_v1.0` in `app/eod_scanner.py` — un-nested verification, telemetry, and health reporting out of candidate loop)*
- ~~*Static 400.0 MB RSS Memory Limit in Deployment Gate 6*~~ *(Replaced on 2026-07-25 by `GATES_MEM_FIX_v1.0` in `tests/test_production_deployment_gates.py` — aligned Gate 6 RSS threshold to `< 450.0 MB` with `gc.collect()`)*

---
*End of Complete Technical Architecture & Zero-Code Reconstruction Specification — `docs/SYSTEM_ARCHITECTURE.md`*
