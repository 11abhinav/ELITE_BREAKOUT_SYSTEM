# ELITE BREAKOUT SYSTEM — COMPLETE TECHNICAL ARCHITECTURE & ZERO-CODE RECONSTRUCTION SPECIFICATION

> **Document Class:** Developer & AI Model Technical Reconstruction Blueprint
> **Target Audience:** Systems Engineers, Quantitative Developers, AI Coding Models
> **Status:** Absolute Master Technical Specification for 100% self-contained system reconstruction without access to source code.
> **Target File:** `docs/SYSTEM_ARCHITECTURE.md`
> **Last Synchronized:** 2026-07-25 (v8.4.4 Master Sync)

---

## TABLE OF CONTENTS

1. [Architectural Philosophy & System Runtime Model](#1-architectural-philosophy--system-runtime-model)
2. [Ownership Matrix & Cache Topology](#2-ownership-matrix--cache-topology)
3. [Abstract Pipeline Architecture & Step Library](#3-abstract-pipeline-architecture--step-library)
4. [Context Model, Dataclasses & Canonical Dataframe Schemas](#4-context-model-dataclasses--canonical-dataframe-schemas)
5. [Core System Enums & Data Models](#5-core-system-enums--data-models)
6. [Quantitative Algorithms, Indicator Specifications & Scoring Engines](#6-quantitative-algorithms-indicator-specifications--scoring-engines)
7. [Exhaustive Internal Scanner Execution Code Flows (All 6 Scanners)](#7-exhaustive-internal-scanner-execution-code-flows-all-6-scanners)
8. [Fundamentals Data Pipeline & Watchlist Generation](#8-fundamentals-data-pipeline--watchlist-generation)
9. [Data Acquisition, Provider Routing, Symbol Resolution Engine & Resiliency Topology](#9-data-acquisition-provider-routing-symbol-resolution-engine--resiliency-topology)
10. [Price Cache Infrastructure & Parquet Sidecars](#10-price-cache-infrastructure--parquet-sidecars)
11. [Database Architecture, Operational Behavior & Complete PostgreSQL DDLs](#11-database-architecture-operational-behavior--complete-postgresql-ddls)
12. [Concurrency, Synchronization & Lock Hierarchy](#12-concurrency-synchronization--lock-hierarchy)
13. [Autonomous Scheduler & 24/7 Execution Blueprint](#13-autonomous-scheduler--247-execution-blueprint)
14. [Alert Lifecycle, State Machine & Cooldown Rules](#14-alert-lifecycle-state-machine--cooldown-rules)
15. [Complete REST API Specifications & Streaming Protocols](#15-complete-rest-api-specifications--streaming-protocols)
16. [Exhaustive Repository Module Inventory & API Interface Contracts](#16-exhaustive-repository-module-inventory--api-interface-contracts)
17. [UI/UX Specifications & Streaming Contracts](#17-uiux-specifications--streaming-contracts)
18. [Verbatim Production Configuration (`app/config.py`)](#18-verbatim-production-configuration-appconfigpy)
19. [Deterministic Reconstruction Answers (Q1 – Q36)](#19-deterministic-reconstruction-answers-q1--q36)
20. [Deployment Verification, Failure Matrix & Golden Test Suites](#20-deployment-verification-failure-matrix--golden-test-suites)
21. [V9 Clean Architecture Blueprint & Versioning Policy](#21-v9-clean-architecture-blueprint--versioning-policy)
22. [AI Reconstruction Checklist & Module Dependency Blueprint](#22-ai-reconstruction-checklist--module-dependency-blueprint)
23. [Runtime Execution & Operational Semantics](#23-runtime-execution--operational-semantics)

---

# 1. ARCHITECTURAL PHILOSOPHY & SYSTEM RUNTIME MODEL

## 1.1 Process Architecture & Deployment Budget
- **Runtime Environment**: Single Python 3.9 process running inside a Linux/Railway container.
- **Resource Budget**: **2.0 GB RAM (2048 MB)** Recommended Container Operating Budget (Absolute System Hard Minimum Floor = **1.0 GB RAM** for low-footprint environments). Warning/eviction threshold = 1200 MB (60%), transient peak = 1400–1600 MB, emergency GC kill = 1800 MB (90%).
- **Process Isolation Directive**: Microservices are explicitly prohibited due to RAM duplication, inter-process serialization overhead, and network latency. All subsystems run in-process using thread pools and shared memory structures.
- **System Mandatory Invariants**:
  - **IST Timezone**: All timing, candle boundaries, trading schedules, and database timestamps MUST be evaluated in **IST (Asia/Kolkata - UTC+5:30)**.
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
       │ 1. Poll for NSE Bhavcopy delivery publication (every 5 mins)│
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

---

# 2. OWNERSHIP MATRIX & CACHE TOPOLOGY

## 2.1 Ownership Principle
**Every object and dataset in the system has EXACTLY ONE owner.** The owner service is exclusively responsible for creating, refreshing, invalidating, and destroying the object. Readers MUST NOT mutate objects they do not own.

## 2.2 Complete Dataset Ownership Matrix

| Dataset | Owner Service | Storage Tier | Refresh Cadence | Consumer Modules |
| :--- | :--- | :--- | :--- | :--- |
| **Watchlist Parquet** | `WatchlistService` (`app/daily_builder.py`) | Ephemeral Disk + RAM | Daily at 01:00 IST | All Scanners, Wealth Engine, Dashboards |
| **OHLCV Daily (1D)** | `PriceCache` (`app/price_cache.py`) | Session RAM + Parquet | Once per trading day | EOD, Reversal, Pullback, Wealth Engine |
| **OHLCV 15-Minute (15m)**| `PriceCache` (`app/price_cache.py`) | Session RAM Cache | Every 15-min tick | Multi-TF Scanner Phase C |
| **OHLCV 5-Minute (5m)** | `PriceCache` (`app/price_cache.py`) | Session RAM Cache | Every 5-min tick | Multi-TF Phase D, Wealth CMP Monitor |
| **OHLCV 1-Hour (1H)** | `PriceCache` (`app/price_cache.py`) | Session RAM Cache | Every 1-hour bar | Multi-TF Phase A (3-month period) |
| **OHLCV 30-Minute (30m)**| `PriceCache` (`app/price_cache.py`) | Session RAM Cache | Every 30-min tick | Multi-TF Phase B |
| **Technical Indicators** | `IndicatorManager` (`app/indicator_manager.py`) | Attached to DataFrame | On fetch write | All Scanners |
| **Delivery / Bhavcopy** | `DeliveryData` (`app/delivery_data.py`) | Ephemeral RAM | Daily post-18:00 IST | EOD Scanner, Reversal Scanner |
| **Fundamentals Cache** | `FundamentalsCache` (`app/fundamentals_cache.py`)| Postgres (`pledge_cache`) | Daily at 01:00 IST | Daily Builder, Wealth Engine |
| **Market Regime State** | `MarketRegimeEngine` (`app/macro_utils.py`) | Session RAM (5m TTL) | Every 5 min | All Scanners, Strategy Policy |
| **Sector Rankings** | `MarketRegimeEngine` (`app/macro_utils.py`) | Postgres | Daily | EOD, Multi-TF (Sector Bonus) |
| **RS Ratings** | `MarketRegimeEngine` (`app/macro_utils.py`) | Postgres | Daily | EOD, Multi-TF (RS Bonus) |
| **Bayesian Weights** | `BayesianUpdater` (`app/bayesian_updater.py`) | Postgres | Daily | Scoring Engine |
| **Surveillance Blacklist**| `Surveillance` (`app/surveillance.py`) | Session RAM (5m TTL) | Hourly | All Scanners |
| **Block Deals Data** | `InstitutionalData` (`app/block_deal_detector.py`) | Ephemeral RAM | Daily | EOD, Reversal Scoring |
| **Scanner Health** | `HealthService` (`app/database.py`) | Postgres (`scanner_health`)| On scan end | Dashboard Server, Admin API |
| **Alert Signals** | `AlertService` (`app/database.py`) | Postgres (`alerts`) | On alert hit | Dashboards, Telegram, Push Service |
| **Symbol Mappings** | `PriceProvider` (`app/price_provider.py`) | Postgres (`symbol_mappings`)| On BSE fallback | UnifiedFetcher, PriceProvider |

---

# 3. ABSTRACT PIPELINE ARCHITECTURE & STEP LIBRARY

```python
class StepResult(Enum):
    CONTINUE = "CONTINUE"  # Step passed, proceed to next step
    REJECT = "REJECT"      # Symbol failed gate, skip remaining steps
    ERROR = "ERROR"        # Exception encountered, log and abort symbol

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

---

# 4. CONTEXT MODEL, DATACLASSES & CANONICAL DATAFRAME SCHEMAS

## 4.1 Pipeline Context Dataclass
```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Set, Tuple, List, Any
import pandas as pd

@dataclass
class PipelineContext:
    scanner_name: str
    scan_id: str
    scan_date: str
    ist_now: datetime
    universe: pd.DataFrame
    interval: str
    period: str

    ohlcv: Dict[str, pd.DataFrame] = field(default_factory=dict)
    delivery: Dict[str, float] = field(default_factory=dict)
    blacklist: Set[str] = field(default_factory=set)
    fundamentals: Dict[str, dict] = field(default_factory=dict)
    recent_alerts: Set[Tuple[str, str]] = field(default_factory=set)
    cooldown_symbols: Set[str] = field(default_factory=set)
    pledge_map: Dict[str, float] = field(default_factory=dict)

    regime_context: dict = field(default_factory=dict)
    bayesian_weights: Optional[dict] = None

    symbol: str = ""
    category: str = ""
    sector: str = ""
    ticker: Optional[pd.DataFrame] = None
    latest: Optional[pd.Series] = None
    breakout_signals: dict = field(default_factory=dict)
    reversal_signals: dict = field(default_factory=dict)
    pullback_signals: dict = field(default_factory=dict)
    raw_score: int = 0
    final_score: int = 0
    entry_price: float = 0.0
    sl_result: Optional[dict] = None
    alert: Optional[dict] = None
    rejection_reason: Optional[str] = None
    error: Optional[Exception] = None

    def set_current_symbol(self, symbol: str, row: pd.Series):
        self.symbol = symbol
        self.category = row.get("Category", "")
        self.sector = row.get("sector", "")
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
        self.ohlcv.clear()
        self.delivery.clear()
        self.fundamentals.clear()
```

## 4.2 Canonical Dataframe Schemas (Strict Types & Nullability)

### 1. `watchlist` (`data/watchlist.parquet`)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Stock` | `str` | No | NSE Symbol Ticker (e.g. `RELIANCE`, `M&M`) |
| `Category` | `str` | No | Watchlist Category Tier (`DEBT_FREE_CASH`, `TOP_BANK`, `WEALTH_COMPOUNDER`, `BLUE_CHIP`, `MIDCAP_GROWTH`, `RECOVERY_PLAY`) |
| `sector` | `str` | No | Sector Classification Name (e.g. `IT`, `BANK`, `AUTO`) |
| `ROCE %` | `float` | Yes | Return on Capital Employed Percentage |
| `ROE %` | `float` | Yes | Return on Equity Percentage |
| `Debt/Equity` | `float` | Yes | Debt to Equity Ratio |
| `YoY Revenue Growth %`| `float` | Yes | Year-over-Year Revenue Growth Percentage |
| `Pledge %` | `float` | Yes | Promoter Pledged Shares Percentage |
| `Market Cap` | `float` | Yes | Total Market Capitalization in Indian Rupees (₹) |

### 2. `ohlcv_daily` (Daily OHLCV + Technical Indicator Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `EMA_9` | `float` | No | 9-period Exponential Moving Average |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |
| `EMA_50` | `float` | No | 50-period Exponential Moving Average |
| `SMA_50` | `float` | No | 50-period Simple Moving Average |
| `SMA_200` | `float` | No | 200-period Simple Moving Average |
| `ATR_20` | `float` | No | 20-period Average True Range (Wilder) |
| `ADX_14` | `float` | No | 14-period Average Directional Index (Wilder) |
| `RSI_14` | `float` | No | 14-period Relative Strength Index (Wilder) |
| `OBV` | `float` | No | On-Balance Volume (Cumulative) |
| `MACD` | `float` | No | Moving Average Convergence Divergence |
| `MACD_SIGNAL` | `float` | No | MACD Signal Line |
| `MACD_HIST` | `float` | No | MACD Histogram |
| `BB_WIDTH` | `float` | No | Bollinger Bands Width |
| `BB_WIDTH_PCTILE` | `float` | No | Bollinger Bands Width Percentile |
| `HIGH_52W` | `float` | No | 52-Week High |
| `SWING_LOW` | `float` | No | Support Swing Low |
| `SWING_HIGH` | `float` | No | Resistance Swing High |
### 3. `ohlcv_15m` (Intraday 15-Minute Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `EMA_9` | `float` | No | 9-period Exponential Moving Average |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |
| `VWAP` | `float` | No | Intraday Volume-Weighted Average Price (₹) |
| `RSI_14` | `float` | No | 14-period Relative Strength Index (Wilder) |
| `ATR_20` | `float` | No | 20-period Average True Range (Wilder) |

### 3.5 `ohlcv_30m` (Intraday 30-Minute Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `EMA_9` | `float` | No | 9-period Exponential Moving Average |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |
| `VWAP` | `float` | No | Intraday Volume-Weighted Average Price (₹) |
| `RSI_14` | `float` | No | 14-period Relative Strength Index (Wilder) |
| `ATR_20` | `float` | No | 20-period Average True Range (Wilder) |

### 3.6 `ohlcv_1h` (Intraday 1-Hour Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `EMA_9` | `float` | No | 9-period Exponential Moving Average |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |
| `SMA_50` | `float` | No | 50-period Simple Moving Average |
| `SMA_200` | `float` | No | 200-period Simple Moving Average |
| `ADX_14` | `float` | No | 14-period Average Directional Index |
| `PRIOR_20D_HIGH` | `float` | No | 20-day High Level |

### 4. `ohlcv_5m` (Intraday 5-Minute Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `VWAP` | `float` | No | Intraday Volume-Weighted Average Price (₹) |
| `EMA_9` | `float` | No | 9-period Exponential Moving Average |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |
| `ATR_20` | `float` | No | 20-period Average True Range (Wilder) |

---

# 5. CORE SYSTEM ENUMS & DATA MODELS

```python
# core_enums.py
from enum import Enum

class ProviderResult(Enum):
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    EMPTY_DATA = "EMPTY_DATA"
    MARKET_CLOSED = "MARKET_CLOSED"

class MappingState(Enum):
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"
    TEMP_DISABLED = "TEMP_DISABLED"

class ScanOutcome(Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"

class CandidateState(Enum):
    IDLE = "IDLE"
    HOURLY_PASSED = "HOURLY_PASSED"
    SETUP_ARMED = "SETUP_ARMED"
    ENTRY_READY = "ENTRY_READY"
    TRADE_ACTIVE = "TRADE_ACTIVE"

# core_models.py
@dataclass
class TradeStructure:
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    target_4: float
    rr_ratio: float
    sl_method: str
    target_method: str
    is_valid: bool = True
    rejection_reason: Optional[str] = None

@dataclass
class ScanFailure:
    scan_id: str
    symbol: str
    provider: str
    result: ProviderResult
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
```

---

# 6. QUANTITATIVE ALGORITHMS, INDICATOR SPECIFICATIONS & SCORING ENGINES

## 6.0 Mathematical Indicator Specifications

| Indicator | Period | Formula / Algorithm | Smoothing / Library | NaN Handling | Warm-up Bars | Rounding |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **EMA 9/20/50** | 9, 20, 50 | $\text{EMA}_t = \alpha \cdot P_t + (1-\alpha) \cdot \text{EMA}_{t-1}$ where $\alpha = \frac{2}{N+1}$ | pandas `ewm(span=N, adjust=False)` | Forward fill | $N$ bars | 2 decimals |
| **SMA 50/200** | 50, 200 | $\text{SMA}_t = \frac{1}{N} \sum_{i=0}^{N-1} P_{t-i}$ | pandas `rolling(window=N).mean()` | Drop initial NaNs | $N$ bars | 2 decimals |
| **RSI 14** | 14 | $RS = \frac{\text{Smoothed Gain}}{\text{Smoothed Loss}}$, $\text{RSI} = 100 - \frac{100}{1 + RS}$ | Wilder exponential smoothing ($\alpha = 1/14$) | Initial fill 50.0 | 14 bars | 2 decimals |
| **ATR 20** | 20 | $TR = \max(H - L, |H - C_{-1}|, |L - C_{-1}|)$, $\text{ATR}_t = \text{Wilder}(TR, 20)$ | Wilder rolling mean | Backfill with $H-L$ | 20 bars | 2 decimals |
| **ADX 14** | 14 | $+DI_{14}, -DI_{14}, DX = \frac{|+DI - -DI|}{+DI + -DI} \times 100$, $\text{ADX} = \text{Wilder}(DX, 14)$ | Wilder exponential smoothing | Initial fill 0.0 | 28 bars | 2 decimals |
| **OBV** | Cumulative | $OBV_t = OBV_{t-1} + \text{Volume}_t$ if $C_t > C_{t-1}$ else $-\text{Volume}_t$ | Direct vector integration | Initial bar = 0 | 1 bar | Integer |
| **VWAP** | Intraday | $\text{VWAP} = \frac{\sum (\text{Typical Price} \times \text{Volume})}{\sum \text{Volume}}$ where $\text{TP} = \frac{H+L+C}{3}$ | Cumulative intraday session reset | Reset at 09:15 IST | 1 bar | 2 decimals |
| **BB Width** | 20 | $\text{Upper} = \text{SMA}_{20} + 2\sigma$, $\text{Lower} = \text{SMA}_{20} - 2\sigma$, $\text{Width} = \frac{\text{Upper} - \text{Lower}}{\text{Middle}}$ | pandas `rolling(20).std()` | Forward fill | 20 bars | 4 decimals |
| **BB Width Pctile** | 60 | Rolling percentile of BB Width over the last 60 bars | pandas `rolling(BB_WIDTH_PCTILE_LOOKBACK).rank(pct=True)` | Drop NaNs | 60 bars | 4 decimals |
| **MACD** | 12, 26, 9 | $\text{MACD} = \text{EMA}_{12} - \text{EMA}_{26}$, $\text{Signal} = \text{EMA}_{9}(\text{MACD})$ | pandas `ewm(span=N, adjust=False)` | Skip symbol if length < 35 | 35 bars | 2 decimals |

## 6.1 Centralized Composite Scoring Engine (`app/scoring_engine.py`)

The scoring engine evaluates the quality of a breakout using a 120+ point scale across several distinct categories. A score $\ge 82$. 
- **Dynamic Threshold Modification**: Base required score is 82. Favorable macro market regimes maintain or lower selectivity filters, whereas unfavorable market regimes *raise* score thresholds (e.g., +5 in BEAR, +8 in SIDEWAYS, +10 in STRONG_BEAR) to enforce stricter quality standards.
- **VWAP Indicator Scope**: VWAP is used exclusively as an intraday anchor for intraday scanner modes (`MULTI_TF`). For daily scanner modes (`EOD`, `REVERSAL`, `PULLBACK`), VWAP falls back to `EMA20` or swing structural support.
- **9-Regime Policy Synthesis Tree**: The regime calculation engine evaluates Nifty 20-day returns, direction, trend slope, and ATR volatility to synthesize 9 policy regimes: `STRONG_BULL`, `WEAK_BULL`, `BULL`, `BEAR`, `WEAK_BEAR`, `STRONG_BEAR`, `SIDEWAYS`, `RANGEBOUND`, `NEUTRAL`.

```python
def calculate_score(symbol: str, df: pd.DataFrame, regime_ctx: dict) -> int:
    latest = df.iloc[-1]
    
    # 1. Base Technical Profile (Max ~11 pts)
    # Rewards stocks trading above long-term averages and with healthy volume
    score = 0
    if close > MIN_STOCK_PRICE: score += 3
    if close > SMA50 > SMA200: score += 3 
    if avg_volume > 500_000: score += 5
    
    # 2. Setup Base Quality (Max ~26 pts)
    # Includes points for Top-of-range close (+2), VCP patterns (+4), Pocket Pivots (+3), 
    # Gap breakouts (+3), and trend strength.
    setup_score = evaluate_setup_quality(latest)
    
    # 3. Signals Base (Max ~16 pts)
    # MACD crosses, Inside Bar breakouts, Momentum bursts
    signal_score = evaluate_signals(latest)
    
    # 4. RSI & Momentum (Max 15 pts)
    rsi_score = 15 if 60 <= rsi <= 70 else (10 if 55 <= rsi < 60 else 0)
    
    # 5. Volume Expansion (Max 20 pts)
    # Rewards heavy volume on the breakout day (up to 20 pts for >4x volume)
    vol_score = evaluate_volume_expansion(latest, avg_vol)
    
    # 6. Trend Strength (Max 16 pts)
    # EMA stack alignment, SMA 50 > 200, ADX > 25/30, DMI+ > DMI-
    trend_score = evaluate_trend_strength(latest)
    
    # 7. Bonus Modifiers
    # RS Percentile, Sector Strength, Regime Match, Target Quality, Structural R:R
    # These can add an additional 20+ points for exceptional market context.
    
    return total_score
```

### Calibration Sanity Check
With a theoretical ceiling exceeding 120+ points, the threshold of 82 ensures that a stock must exhibit strength across multiple independent vectors to pass.

| Category | Max Achievable Score (No Bonuses) |
| :--- | :--- |
| Base Technical Profile | 11 |
| Setup Base Quality | 26 |
| Signals Base | 16 |
| RSI & Momentum | 15 |
| Volume Expansion | 20 |
| Trend Strength | 16 |
| **Total Practical Max** | **104** |

Even with zero bonus points for Relative Strength, Sector alignment, or Macro Regime, a perfectly formed technical setup can achieve 104 points. The `82` threshold acts as an ~78% quality filter on the raw technicals.

---

# 7. EXHAUSTIVE INTERNAL SCANNER EXECUTION CODE FLOWS (ALL 6 SCANNERS)

All 6 scanners read thresholds directly from `config.py`.

## 7.1 EOD Breakout Scanner (`app/eod_scanner.py`)
```python
def run_eod_scanner(run_once=False, force=False):
    scan_id = generate_scan_id()
    start_time = time.time()
    universe = watchlist_cache.get_watchlist()
    approved_candidates = []

    for chunk in chunk_iterable(universe, batch_size=50):
        ohlcv_map = price_provider.fetch_batch(chunk, interval="1d", period="1y")
        for symbol, df in ohlcv_map.items():
            if df is None or len(df) < 50: continue  # Lowered from 200 to 50 for IPO/new listing evaluation
            latest = df.iloc[-1]
            if latest["Close"] < config.MIN_STOCK_PRICE: continue
            
            prior_20d_high = df["High"].iloc[-21:-1].max()
            if latest["Close"] <= prior_20d_high: continue
            
            dist_52w = ((df["High"].iloc[-252:].max() - latest["Close"]) / latest["Close"]) * 100.0
            if dist_52w > config.EOD_ADVANCED_CONFIG["MAX_DISTANCE_FROM_52W_HIGH_PCT"]: continue
            
            range_hl = latest["High"] - latest["Low"]
            body_ratio = abs(latest["Close"] - latest["Open"]) / range_hl if range_hl > 0 else 0
            close_pos = (latest["Close"] - latest["Low"]) / range_hl if range_hl > 0 else 0
            upper_wick = (latest["High"] - latest["Close"]) / range_hl if range_hl > 0 else 0
            
            if body_ratio < config.EOD_CONFIG["MIN_BODY_RATIO"]: continue
            if close_pos < config.EOD_CONFIG["MIN_CLOSE_POSITION"]: continue
            if upper_wick > config.EOD_CONFIG["MAX_UPPER_WICK"]: continue
            
            vol_ratio = latest["Volume"] / df["Volume"].iloc[-21:-1].mean()
            if vol_ratio < config.EOD_CONFIG["MIN_VOLUME_RATIO"]: continue
            
            score = scoring_engine.calculate_score(symbol, df, regime_ctx)
            if score < config.SCORE_THRESHOLDS["1d"]: continue
            
            sl_res = compute_sl_and_target(df, mode="EOD")
            if sl_res["rr_ratio"] < config.MIN_NATURAL_RR["EOD"]: continue
            
            approved_candidates.append({
                "symbol": symbol, "score": score, "sl_result": sl_res, "entry": latest["Close"]
            })
            
    # Executable Next-Morning Open Validation (evaluated for EOD & Reversal at 09:15:00 IST):
    # open_p = fetch_live_price(symbol); c0 = candidate["entry"]; t1 = candidate["sl_result"]["target_1"]
    # gap_pct = (open_p - c0) / c0 * 100
    # if gap_pct > config.MAX_ENTRY_GAP_PCT (3.0%) or open_p >= t1:
    #     save_rejected_alert(symbol, rejection_reason=f"REJ_ENTRY_GAP_TOO_WIDE (Open ₹{open_p:.2f} vs Close ₹{c0:.2f})")
    #     continue

    approved_candidates.sort(key=lambda x: x["score"], reverse=True)
    top_10 = approved_candidates[:config.SCANNER_MAX_ALERTS["EOD"]]
    # RANKED_OUT suppressed candidates bypass save_rejected_alert to avoid telemetry pollution
    saved_count = save_alert_batch(top_10)
    upsert_scanner_health("EOD", status="OK", alerts=len(top_10), duration=time.time() - start_time)
    gc.collect()
    return len(top_10)
```

## 7.2 Reversal Scanner (`app/reversal_scanner.py` — v6)

**Purpose:** Deep-discount mean-reversion scanner. Targets high-quality stocks that have corrected 20–45% from their 52-week high and are showing early recovery signals. Runs once post-18:00 IST after Bhavcopy availability confirmation.

**Entry Point:** Called by `main.py` as `_run_reversal_with_retries(force=True)` in the evening batch.

**Filter Cascade (executed in strict order per symbol):**

```text
Universe: watchlist.parquet (all categories)
   │
   ├─[Gate 1] Price ≥ ₹100 (MIN_STOCK_PRICE)
   ├─[Gate 2] Avg daily volume ≥ 300,000 shares (MIN_AVG_DAILY_VOLUME)
   ├─[Gate 3] ROE ≥ 12% (MIN_ROE from REVERSAL_CONFIG)
   ├─[Gate 4] YoY Revenue growth ≥ 8% (MIN_YOY_REVENUE_GROWTH)
   ├─[Gate 5] Drop from 52W High: 20% ≤ drop ≤ 45% (REVERSAL_CONFIG band)
   ├─[Gate 6] Close > SMA_50 (mandatory trend recovery gate — FIX 2)
   ├─[Gate 7] Price ≤ SMA_200 + (MAX_DROP_BELOW_SMA200=20%) safety fence
   ├─[Gate 8] RSI previously ≤ RSI_OVERSOLD_THRESHOLD (38) AND current RSI ≥ RSI_CURL_MIN (50)
   ├─[Gate 9] Volume ratio ≥ MIN_VOLUME_RATIO (2.0× 20-bar avg)
   ├─[Gate 10] Not in REVERSAL cooldown (40 trading day suppression on failed alerts, matching holding lifecycle)
   │
   └─[SCORE] Reversal scoring engine (max 100 pts, min threshold = 62)
         • Trend structure:    25 pts  (SMA50 reclaim + SMA200 position)
         • SMA200 proximity:   15 pts  (closer = less falling-knife risk)
         • Volume:             15 pts  (confirmation signal)
         • MACD momentum:      15 pts  (MACD histogram flip from negative to positive)
         • RSI curl quality:   15 pts  (faster RSI recovery from oversold)
         • Category quality:   10 pts  (fundamental tier from daily builder)
         • Drop sweet spot:     5 pts  (bonus for 25–35% drop band)
         • R:R quality:         5 pts  (R:R ≥ 2.5 gets max bonus)
         • Delivery + OBV:     bonus   (institutional accumulation confirmation)
```

**Outputs:**
- `alerts` table rows with `scanner='REVERSAL'`, `breakout_type='REVERSAL'`
- `rejected_alerts` table row on score failure

**Key Config References:**
- `REVERSAL_CONFIG` — all filter thresholds
- `MIN_NATURAL_RR["REVERSAL"] = 2.0` — minimum R:R accepted
- **Two-Tier Cooldown Precedence**: Tier 2 (40-day Fallen Knife Defense) has higher precedence over Tier 1 (7-day alert dedup). If a symbol stopped out on a reversal trade within 40 trading days, it is hard-blocked even if Tier 1 alert dedup has expired.
- `ALERT_COOLDOWN_MINUTES["REVERSAL"] = 10080` (7 days dedup)
- `SCANNER_MAX_ALERTS["REVERSAL"] = 10`
- SL/Target: `compute_sl_and_target(mode="REVERSAL")` — uses ATR=2.0 base, sl_atr_buf=1.0

**Failure Recovery:** On per-symbol exception: log to `scan_failures` table, CONTINUE to next symbol. Scanner health updated at end of run.

---

**Purpose:** Intraday 4-phase cascade scanner operating on 1H → 30m → 15m → 5m timeframes. Identifies breakout setups with multi-timeframe trend alignment and fires buy alerts only on 5m confirmed triggers. Runs every 15 minutes between **09:30 AM and 14:45 PM IST** during market hours (with a strict **14:15 IST entry cutoff** blocking new Phase D entries).

**Two Entry Points:**
- `run_hourly_phase()` — Phase A: scans entire universe on 1H, builds `breakout_watchlist`
- `run_lower_tf_phase()` — Phases B/C/D: reads active `breakout_watchlist`, advances state machine

**Phase A — 1H Trend Permission (run_hourly_phase):**
```text
Input: watchlist.parquet (full universe)
Bulk pre-fetch: fetch_watchlist_data(watchlist, period="3mo", interval="1h")
Batch size: 50 symbols (env: MULTI_TF_FETCH_BATCH_SIZE)
Min data required: 50 bars (MTF_BAR_LIMIT_FIX)

Filters per symbol:
  • Price ≥ ₹100
  • EMA9 > EMA20 > SMA50 AND (Close > SMA200 OR SMA200 is missing)   (ema_ok)
  • ADX ≥ 18                                   (adx_ok — ADX_MIN_THRESHOLD)
  • Distance to prior 20D High: -2% to +5%     (dist_ok — MTF_DIST_GATE_FIX)
  • Stale flag = False

Output: DB upsert to breakout_watchlist (state = "HOURLY_APPROVED")
```

**Phase B — 30m Consolidation Gate (HOURLY_APPROVED → SETUP_ARMED):**
```text
Fetch: data_30m for all HOURLY_APPROVED symbols (period="1mo", interval="30m")
Check on previous candle (iloc[-2]):
  • BB_WIDTH_PCTILE < 0.45 AND distance within [-1.5%, +2.5%]  (consolidation)
  OR
  • Price > trigger_level AND volume_ratio > 1.2               (fast breakout override)

Output: DB upsert state = "SETUP_ARMED" with:
  trigger_level = breakout_level
  invalidation_level = min(SWING_LOW, EMA20)
  expires_at = min(now + 60min, session_end)
```

**Phase C — 15m Micro-Alignment Gate (SETUP_ARMED → ENTRY_READY):**
```text
Fetch: data_15m for SETUP_ARMED symbols (period="5d", interval="15m")
Gate: EMA9_15m > EMA20_15m AND distance within [-1.5%, +2.5%]
Output: DB upsert state = "ENTRY_READY" (expires in 30m)
```

**Phase D — 5m Final Trigger (ENTRY_READY → TRADE_ACTIVE):**
```text
Fetch: data_5m + data_daily (for S/R pivot injection)
apply_indicators(df, timeframe="5m", daily_ohlc=daily_df)

Thrust Trigger (preferred):
  • Close > prev_High AND Close > (trigger_level + 0.15*ATR20)
  • Volume ratio > 1.2, close_position ≥ 0.6, upper_wick < 0.35

Pullback Trigger (PULLBACK_TRIGGER_MODE="PREVIOUS_HIGH"):
  • Low ≤ max(trigger_level, EMA9)           (touches the level)
  • Close > trigger_level AND Close > prev_High
  • Volume ratio > 1.0, close_position ≥ 0.6

Extension Kill Gate:
  • Close > trigger_level + (0.8 * ATR20)   → REJECT (PD01_OVER_EXTENDED)

On valid trigger:
  • compute_sl_and_target(mode="MULTI_TF")
  • save_alert_if_new() + mark state = "TRADE_ACTIVE"
```

**Decay / Demotion Rules:**
- `drift > 3%` from resistance → Demote back to `HOURLY_APPROVED` + 2h cooldown (MTF_FLAPPING_FIX)
- Age `> 4h` + `drift > 1.5%` → Expiry demotion

**Key Config References:**
- `MULTI_TF_CONFIG`: MIN_RSI=52, MAX_RSI=87, MIN_VOLUME_RATIO=2.5, PULLBACK_TRIGGER_MODE="PREVIOUS_HIGH"
- `SCORE_THRESHOLDS["15m"] = 78`, `["1h"] = 80`
- `ALERT_COOLDOWN_MINUTES["MULTI_TF"] = 240` (4 hours)
- `SCANNER_MAX_ALERTS["MULTI_TF"] = 15`

---

## 7.4 Pullback Pipeline Scanner (`app/pullback_pipeline.py` — pb-1.0.0)

**Purpose:** Identifies post-impulse pullback setups. Scans for stocks that have made a strong upward impulse (≥8% gain in ≤20 bars), then pulled back 23.6–61.8% (Fibonacci retracement of the impulse wave) in an orderly fashion (low volume, limited swings), and are now showing a re-entry trigger candle. Runs post-18:00 IST in the evening batch after Bhavcopy.

**Entry Point:** `run_pullback_pipeline(run_date, force=False)`

**Filter Cascade:**

```text
Regime Gate: STRONG_BEAR → Scanner disabled entirely (returns 0)
Score threshold = 75 + REGIME_POLICIES[market_regime]["score_modifier"]

For each symbol in watchlist (daily OHLCV, period="1y"):
  ├─[Gate 1] Data freshness: dataset_date == run_date (else wait for Bhavcopy)
  ├─[Gate 2] History ≥ 200 bars (MIN_HISTORY)
  ├─[Gate 3] Impulse Detection:
  │   • Gain ≥ 8% in ≤ 20 bars (MIN_IMPULSE_GAIN_PCT, MAX_IMPULSE_BARS)
  │   • Impulse ATR ≥ 3.0× ATR_20 (MIN_IMPULSE_ATR)
  ├─[Gate 4] Pullback Geometry:
  │   • Depth: 23.6% ≤ pullback ≤ 61.8% of the impulse wave (effective 5%–15% absolute price drop) (MIN/MAX_DEPTH_PCT)
  │   • Duration: 3–20 bars (MIN/MAX_DURATION)
  │   • Internal swings ≤ 2 (MAX_INTERNAL_SWINGS)
  │   • Pullback volume ratio < 0.75× impulse volume (orderly retrace)
  ├─[Gate 5] Trigger Candle:
  │   • Volume ratio ≥ 1.3× 20-bar avg (TRIGGER_VOL_MULT)
  │   • Close position ≥ 0.75 of candle range (MIN_CLOSE_LOCATION)
  │   • Body ATR ≥ 0.5× ATR_20 (MIN_BODY_ATR)
  │   • Upper wick ≤ 25% of range (MAX_UPPER_WICK)
  │   • Entry gap from prior session ≤ 3% (MAX_ENTRY_GAP_PCT)
  └─[SCORE] Pullback bonus scoring (max +5 bonus points, PRIOR_WINDOW=30 bars)
```

**Output:** `alerts` table with `scanner='PULLBACK'`, cooldown 7 days, max 10 alerts per run.

**Key Config:** `PULLBACK_CONFIG` dict — all thresholds above (VERSION: pb-1.0.0, lowered MIN_HISTORY from 260→200 per PB_BAR_FIX_v1.0).

---

## 7.5 Multibagger Scanner (`app/multibagger.py` — V5 Pipeline)

**Purpose:** Long-term compounder scanner targeting fundamentally excellent businesses (ROCE ≥ 20%, ROE ≥ 15%, debt-free) that meet V5 pipeline quality thresholds. Runs cold start at **04:00 AM IST** (with fresh daily watchlist), post-market scan at **19:00 PM IST**, and **15-minute intraday exit monitor**.

**Entry Point:** `run_multibagger_scan()` or `monitor_exits()` (intraday exit monitor)

**Execution Flow:**

```text
Input: watchlist.parquet
Data: 1Y daily OHLCV + Screener fundamentals

For each symbol:
  ├─[Phase 1] Fundamental & Valuation Gate (V5 Pipeline):
  │   run_pipeline_for_symbol(symbol, map_watchlist_to_v5(row))
  │   → PipelineDecision: composite_score, quality.score, valuation.score
  ├─[Phase 2] Technical Overlay & Institutional Footprint:
  │   • Close > SMA_200 (trend gate)
  │   • Apply institutional block deal bonuses
  ├─[Phase 3] Conviction Tier Classification (classify_conviction):
  │   • 🚀 Prime Multibagger: composite≥75, CQS≥65, PAS≥50, trend≥10, Piotroski F-Score≥7 (₹100,000 allocation)
  │   • 💎 High Quality: composite≥65, CQS≥60, trend≥10 (₹50,000 allocation)
  │   • 🟡 Watchlist: composite 50–64 (Non-alerting watchlist tier; strictly blocked from generating active BUY alerts)
  └─[Phase 4] Alert Generation & Category Binding:
      If tier IN ["🚀 Prime Multibagger", "💎 High Quality"] AND in Buy Zone AND Technicals Confirmed:
        → Trigger active BUY alert and insert into alerts DB table with category = tier (ensuring zero category label mis-stamping)
```

Output:
  • alerts table (scanner='MULTIBAGGER')
  • elite_wealth_system.parquet (for dashboard)
  • ALERT_COOLDOWN_MINUTES["MULTIBAGGER"] = 43200 (30 days)
```

---

## 7.6 Wealth Engine Buy-Scan (`app/wealth_engine.py` — buy-scan path)

The Wealth Engine's buy-scan path (`run_wealth_scan()`) executes the 4-phase pipeline documented in §23.1. Refer to §23.1 for the full phase contract matrix. Key alert generation specifics:

```text
Scanner name: "WEALTH"
Breakout type: "WEALTH_BUY"
Max alerts per run: SCANNER_MAX_ALERTS["WEALTH"] = 40
Cooldown: ALERT_COOLDOWN_MINUTES["WEALTH"] = 1440 (24 hours)
Score threshold: FM_Score ≥ 55 (Layer 1 minimum)
SL/Target: compute_sl_and_target(mode="EOD") — ATR base 2.0
```

---

# 7A. SL/TARGET ENGINE v7 — COMPLETE SPECIFICATION

**File:** `app/sl_target_helper.py` (1,647 lines) — `ACTIVE_ALGO_VERSION = "SL_ENGINE_V7.1"`

## 7A.1 Architecture Overview

The SL/Target engine is a multi-stage pipeline: **Structural Stop Discovery → Target Candidate Generation → Cluster Engine → Conflict Resolver → Trade Invariant Validation.**

```text
Entry Price + OHLCV DataFrame + Mode
          │
          ▼
  [SupportEngine.get_ranked_supports()]
  → Ranked list of swing lows, SMA200, EMA20, VWAP, S1 by structural score
          │
          ▼
  [_compute_structural_stop()]
  → Pick top-scoring support cluster within 1.5 ATR width
  → Apply ATR-scaled buffer (0.8× ATR for EOD)
  → Validate: Entry > SL ≥ MIN_STOP_PCT distance
          │
          ▼
  [CandidateGenerator.generate_breakout_candidates()]
  → Enumerate all TargetSource candidates:
    EQUAL_HIGH, RESISTANCE, HIGH_20D, PREV_DAY_HIGH, HIGH_52W,
    ABCD, RETRACE_50, RETRACE_618, RETRACE_382,
    FIB_127, FIB_162, FIB_200, SMA200, BB_MID, SMA50,
    ATR_PROJ, R1, R2, ROUND_NUM (19 sources across all modes)
> **Note on Priority vs. Weight**: Priority is a tiebreaker for equal-score clusters. The weight column determines cluster contribution in the `ClusterEngine` sum. RETRACE_618 (priority 7, weight 7) and RETRACE_50 (priority 8, weight 8) carry more weight than their priority implies — the weight is correct by design since a 50% retracement is a stronger magnet than 61.8% in trending markets.
          │
          ▼
  [RoundNumberEngine.detect_and_boost()]
  → Boost target by ROUND_NUMBER_BOOST=8 pts if within 0.5% of round number
          │
          ▼
  [ClusterEngine.cluster(candidates)]
  → Group candidates within TARGET_CLUSTER_WINDOW_PCT=0.75% or 0.5 ATR
  → Each cluster: consensus_price = weighted avg, score = sum of weights
          │
          ▼
  [ConflictResolver.resolve(clusters, mode)]
  → TARGET_CONFLICT_POLICY:
    EOD:      "REGIME"         — sort by (consensus_price, score) regime-aware
    MULTI_TF: "CONFIDENCE"     — sort by cluster score descending
    REVERSAL: "SECOND_NEAREST" — pick second nearest target above entry
    PULLBACK: "REGIME"         — sort by (consensus_price, score) regime-aware
          │
          ▼
  [TradeStructureValidator.validate()]
  → 6 invariant checks (see §7A.3)
          │
          ▼
  Output: {stop_loss, target_1, target_2, target_3, sl_method, t_method, rr_ratio, is_valid}
```

## 7A.2 Per-Scanner Dispatch Config (`_MODE_CONFIG`)

```python
#           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
_MODE_CONFIG = {
    "EOD":      (2.00,    0.80,       0.0075,     3.0),
    "MULTI_TF": (1.50,    0.50,       0.0050,     3.0),
    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),
    "PULLBACK": (2.00,    0.75,       0.0075,     3.0),
}
```

- `atr_base`: Number of ATRs from entry for ATR fallback SL
- `sl_atr_buf`: ATR buffer added below swing low support cluster
- `sl_pct_buf`: Minimum % distance buffer below support
- `max_sl_atr`: Maximum SL distance from entry in ATR units

## 7A.3 TradeStructureValidator — 9 Invariant Checks

```text
Invariant 1: entry > 0                                       (Basic sanity check)
Invariant 2: stop_loss < entry                                 (SL must be logically placed)
Invariant 3: risk = entry - stop_loss > 0                      (Risk amount must be calculable)
Invariant 4: target_1 > entry                                  (First target must be profitable)
Invariant 5: target_1 < target_2 < target_3 (and target_4 > target_3 if target_4 present; spacing >= max(0.5*ATR20, 0.5% entry))
Invariant 6: natural_rr = (target_1 - entry) / risk >= min_rr  (Reward-to-risk ratio minimum on T1)
Invariant 7: (target_3 - entry) / risk >= MIN_REWARD_POTENTIAL (T3-based reward potential floor for Reversal/EOD)
Invariant 8: natural_rr <= MAX_REASONABLE_RR                   (Caps upper bound R:R at 8.0x to filter extreme outliers)
Invariant 9: target_cluster_confidence >= MIN_TARGET_CONFIDENCE (Rejects target clusters with confidence score < 40)
```

Failure on any invariant → `is_rejected=True`, `rejection_reason=<code>`, alert saved to `rejected_alerts` table.

## 7A.4 TargetSource Priority & Weights (from `config.py`)

| Source | Priority | Weight |
|---|---|---|
| EQUAL_HIGH | 1 | 10 |
| RESISTANCE | 2 | 10 |
| HIGH_20D | 3 | 9 |
| PREV_DAY_HIGH | 4 | 9 |
| HIGH_52W | 5 | 8 |
| ABCD | 6 | 9 |
| RETRACE_618 | 7 | 7 |
| RETRACE_50 | 8 | 8 |
| RETRACE_382 | 9 | 6 |
| FIB_127 | 10 | 7 |
| FIB_162 | 11 | 6 |
| SMA200 | 12 | 8 |
| SMA50 | 13 | 6 |
| BB_MID | 14 | 7 |
| FIB_200 | 15 | 5 (regime-adjusted: BULL=7, BEAR=2) |
| ATR_PROJ | 16 | 4 |
| R1 | 17 | 5 |
| R2 | 18 | 4 |
| ROUND_NUM | 99 | 0 (boost only) |

## 7A.5 Partial Exit Profiles

```python
# Canonical EXIT_PROFILES defined in config.py (§18)
# EXIT_PROFILES = {
#     "CONSERVATIVE": {"t1": 25, "t2": 50, "t3": 25},
#     "BALANCED":     {"t1": 30, "t2": 40, "t3": 30},
#     "AGGRESSIVE":   {"t1": 20, "t2": 30, "t3": 50},
# }

SCANNER_EXIT_PROFILE = {
    "EOD":      "BALANCED",      # T1: 30%, T2: 40%, T3: 30%
    "MULTI_TF": "AGGRESSIVE",    # T1: 20%, T2: 30%, T3: 50% (holds majority for runner)
    "REVERSAL": "CONSERVATIVE",  # T1: 25%, T2: 50%, T3: 25% (locks gain at T2 bounce)
    "PULLBACK": "BALANCED",      # T1: 30%, T2: 40%, T3: 30%
}
```

## 7A.6 Anti-Operator-Trap Design Principles

The SL/Target engine is specifically designed to reject three operator manipulation patterns:

1. **Climax Volume Trap**: Volume spike at 52-week high with no continuation → penalized by scoring engine `calculate_score()` via extended breakout penalty (`CLIMAX_VOLUME_LOOKBACK = 20` defined in config)
2. **Lower High Trap**: Sequence of lower highs post-breakout → `LOWER_HIGH_LOOKBACK = 6` bars parameter defined in config for future dedicated pipeline gate
3. **Thin Spread Trap**: Candle range < 0.3% of price → `MIN_CANDLE_RANGE_PCT = 0.003` parameter defined in config for future dedicated pipeline gate

SL placement is deliberately set **below the support zone**, not at it, to avoid stop-hunting by operators.

---

# 8. FUNDAMENTALS DATA PIPELINE & WATCHLIST GENERATION

**File:** `app/daily_builder.py` | **Schedule:** Daily at 01:00 IST | **Owner:** WatchlistService

## 8.1 Pipeline Execution Flow

```text
01:00 IST — Scheduler triggers daily_builder

Step 1: TradingView Screener Fetch
  • Source: TradingView NSE & BSE screener API
  • Filters: MCap ≥ ₹150Cr, Daily liquidity ≥ ₹15Cr (MIN_DAILY_LIQUIDITY_RUPEES_WATCHLIST)
> **Note on liquidity gate cascade**: The universe is pre-filtered here at ≥₹15Cr/day. The downstream `WEALTH_REJ_001` gate at ≥₹1Cr/day (§7.6, §23.10) is therefore **unreachable** in production — it can only fire on symbols added to the Wealth watchlist through a path that bypasses this pre-filter. The rejection code is retained for completeness but is currently informational.
  • Price ≥ ₹100 (MIN_PRICE filter)
  • Returns: raw symbol list with fundamental ratios
              (ROCE, ROE, Debt/Equity, YoY Revenue Growth, YoY Profit Growth)

Step 2: Symbol Normalization
  • Fix TradingView underscores/hyphens: M_M → M&M, L_TFH → L&TFH
  • Deduplicate NSE + BSE entries
  • Assign Category tier (DEBT_FREE_CASH, TOP_BANK, WEALTH_COMPOUNDER, etc.)

Step 3: Promoter Pledge Fetch
  • Source: BSE corporate API + NSE corporate API
  • Cache: pledge_cache table (24h TTL)
  • Inject Promoter_Pledge % column into dataset

Step 4: Sector Classification
  • Map each stock to sector and industry sub-group
  • Source: TradingView sector tag or NSE classification fallback

Step 5: Output & Persistence
  • Write: data/watchlist.parquet (WATCHLIST_PATH)
  • Write: DatasetRegistry["watchlist"] (session RAM)
  • Update: data_cache_metadata table (key="watchlist", cadence=86400s)
  • Log: validation_history table (quality score, row count)

Step 6: Self-test Validation
  • Run QualityValidator V8.0 on output parquet
  • Alert if row_count drops > MAX_HISTORY_SHRINK (30%) vs prior day
  • Quality score thresholds: OK ≥ 90, WARNING 75–90, ERROR < 75
```

## 8.2 Category Assignment Rules

| Category | ROCE Threshold | Other Criteria |
|---|---|---|
| `DEBT_FREE_CASH` | ≥ 25% | D/E = 0 + positive FCF |
| `TOP_BANK` | ≥ 18% | NBFC/Bank sector, ROE ≥ 15% |
| `WEALTH_COMPOUNDER` | ≥ 20% | MCap ≥ ₹5,000 Cr, 5Y track record |
| `BLUE_CHIP` | ≥ 15% | MCap ≥ ₹20,000 Cr |
| `MIDCAP_GROWTH` | ≥ 15% | MCap ₹2,000–₹20,000 Cr, YoY revenue ≥ 15% |
| `RECOVERY_PLAY` | ≥ 10% | Improving from prior year loss |

## 8.3 Watchlist Parquet Schema (Final Output)

| Column | Type | Nullable | Source |
|---|---|---|---|
| `Stock` | str | No | TradingView NSE symbol |
| `Category` | str | No | Assigned by daily_builder |
| `Sector` | str | No | TradingView sector tag |
| `Industry` | str | No | TradingView industry classification |
| `Market Cap Cr` | float | Yes | TradingView screener (₹ Cr) |
| `ROE %` | float | Yes | TradingView screener |
| `ROCE %` | float | Yes | TradingView screener |
| `Debt/Equity` | float | Yes | TradingView screener |
| `YOY Revenue %` | float | Yes | TradingView screener |
| `YOY Profit %` | float | Yes | TradingView screener |
| `PEG Ratio` | float | Yes | TradingView screener |
| `Promoter_Pledge` | float | Yes | BSE/NSE corporate API |

---

# 9. DATA ACQUISITION, PROVIDER ROUTING, SYMBOL RESOLUTION ENGINE & RESILIENCY TOPOLOGY

## 9.1 Data Provider Capability Matrix

| Provider | Bulk Download | Live Quotes | Intraday (1m..1h) | Historical (1d) | NSE Support | BSE Support | Rate Limit |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **YFinance** | Yes (batch size 30) | No | Yes (15m, 1h) | Yes (1d, 1wk) | Yes (`.NS`) | Yes (`.BO`) | 50/s, 500/m, 2000/30m |
| **Fyers API v3** | No (single ticker) | Yes (REST/WS) | Yes (1m, 5m, 15m, 1h)| Yes (1d) | Yes (`NSE:SYM-EQ`) | Yes (`BSE:500325-EQ`) | 10 req/sec |
| **BSE Scraping** | Yes | No | No | Yes (1d) | No | Yes (BSE Code) | ScraperAPI proxy |

## 9.2 Symbol Resolution Engine & Provider Translation Rules

### 9.2.1 Canonical System Symbol Standard
Inside the runtime memory and database, all symbols are stored in their **Canonical NSE Format** (e.g. `RELIANCE`, `M&M`, `TATASTEEL`).

### 9.2.2 Provider Translation & Candidate Generation Order
When a provider request is made for canonical symbol `S`:

1. **TradingView Screener Translation**:
   - Symbols with ampersands are scraped as underscore/hyphen: `M_M` $\rightarrow$ corrected to `M&M`, `L_TFH` $\rightarrow$ `L&TFH`, `GVT_D` $\rightarrow$ `GVT&D`.

2. **Fyers API v3 Resolution Order**:
   - Primary: `NSE:{S}-EQ`
   - Secondary (if primary fails): `NSE:{S}-BE` (trade-to-trade series)
   - Tertiary: `NSE:{S}-SM` (SME series)
   - Fallback: `BSE:{BSE_CODE}-EQ` (via `symbol_mappings` lookup)

3. **YFinance Resolution Order**:
   - Primary: `{S}.NS`
   - Secondary (if `.NS` returns `NOT_FOUND`): `{S}.BO` (BSE equity fallback)
   - Tertiary: `{BSE_NUMERIC_CODE}.BO` (Numerical BSE security code)

### 9.2.3 PostgreSQL `symbol_mappings` Lifecycle & Exponential Backoff
The database table `symbol_mappings` tracks mapping state and failure backoffs:

```text
[ UNMAPPED / ACTIVE ] ──(Provider NOT_FOUND)──> Increment failure_count
                                                        │
                                          Calculate Exponential Backoff
                                                        │
                                                        ▼
[ INVALID ] <──(retry_after Active)── Set mapping_state = 'INVALID'
```

- **Exponential Backoff Schedule**:
  - `failure_count = 1` $\rightarrow$ `retry_after = NOW() + 7 days`
  - `failure_count = 2` $\rightarrow$ `retry_after = NOW() + 30 days`
  - `failure_count = 3` $\rightarrow$ `retry_after = NOW() + 90 days`
  - `failure_count >= 4` $\rightarrow$ `retry_after = NOW() + 365 days` (Permanent Delisting Candidate)
- **Automatic Recovery**: When a symbol resolves successfully via a fallback provider, `save_bse_mapping()` or `save_fyers_mapping()` resets `failure_count = 0`, sets `mapping_state = 'ACTIVE'`, and sets `is_invalid = FALSE`.

---

# 10. PRICE CACHE & PARQUET SIDECARS (`app/price_cache.py`)

## 10.1 Two-Tier Cache Architecture & Persistence Invariants
The data caching architecture implements a resilient, thread-safe three-tier hierarchy designed to eliminate redundant external network fetches, mitigate provider rate-limiting, and ensure zero-downtime historical continuity:
- **Tier 1 (High-Speed Session RAM Cache)**: Maintained in memory via `_cache: dict[tuple, dict]` keyed by `(interval, period)`. Protected by reentrant thread lock (`_lock`). Serves real-time data frame lookups across multiple concurrent scanners with microsecond latency.
- **Tier 2 (Persistent Disk Parquet Sidecars)**: Stored in local repository filesystem under `DATA_DIR/history/{interval}/{symbol}.parquet` accompanied by atomic metadata sidecars (`.meta.json`). Guarantees fast system warm-up and survivability across application restarts.

## 10.2 Dynamic Cadence Engine & Cache Floors
To optimize network bandwidth while ensuring candlestick accuracy, cache freshness is dynamically computed by `get_dynamic_cadence(interval)`:
- **Market Closed Hours**: Post-15:30 IST on weekdays or weekends, daily timeframe intervals (`1d`, `daily`, `1wk`, `1mo`) automatically cache until the next scheduled NSE market open (09:15 IST) or up to 12 hours post-close (`43200s`).
- **Intraday Market Hours**: Calculates exact seconds remaining until the next multiple of the active interval (e.g., 15-minute or 1-hour boundaries relative to 09:15 IST) plus a 5-second broker data settling buffer.
- **Minimum Cache Floors (`CACHE_FLOOR_FIX_v1.0`)**: Enforces a strict expiration floor equal to 50% of the total interval duration (5m $\rightarrow$ 150s, 15m $\rightarrow$ 450s, 30m $\rightarrow$ 900s, 1h $\rightarrow$ 1800s). This prevents cache expiration race conditions and delta re-fetch storms when scanners execute near candle transition boundaries.

## 10.3 Thundering Herd Protection & Global Serialization
When scheduled evening scanners or intraday Multi-TF pipelines trigger simultaneously across multiple workers, unregulated network fetches risk overwhelming external broker endpoints (Thundering Herd pattern):
- **Global Fetch Serialization (`_fetch_lock`)**: Serializes external batch downloading across all active threads and scanner routines. Only a single thread interacts with provider HTTP sockets at a time.
- **Double-Check Lock Pattern**: Threads waiting on `_fetch_lock` re-verify the in-memory RAM cache (`_cache`) immediately upon acquiring the lock. If the leading thread has already populated the requested symbols, waiting scanners instantly reuse the freshly populated DataFrame.
- **Structural Integrity Gate (`validate_ohlcv_structure`)**: Enforces strict timestamp monotonicity and fundamental price boundary rules (`High >= Low`, Open and Close within `[Low, High]` bounds, and non-negative Volume). Malformed DataFrames are instantly dropped in favor of existing stale cache elements.
- **Anti-Shrink Protection (`MAX_HISTORY_SHRINK = 0.30`)**: Protects persistent historical caches from provider API failures or truncations. If an incoming full fetch returns a row count more than 30% smaller than the existing local Parquet record (`incoming_rows < existing_rows * (1.0 - MAX_HISTORY_SHRINK)`), the system rejects the remote payload (`Reason=HISTORICAL_SHRINK`), flags the local cache as `is_stale=True`, and retains existing historical depth.
- **Atomic Sidecars & Pre-Enriched Indicators**: Each `.parquet` file writes an associated `.meta.json` recording `schema_version`, `indicator_version`, `ohlcv_hash` (deterministic SHA-256 fingerprint of core price columns), and row counts. Data frames are pre-calculated with canonical technical indicators via `indicator_executor` prior to persistence, ensuring read-ready analytical structures.

---

# 11. DATABASE ARCHITECTURE, OPERATIONAL BEHAVIOR & COMPLETE POSTGRESQL DDLS

## 11.1 Operational Database Behavior & Retention Rules
- **Immutable Columns**: In `alerts` table, `id`, `symbol`, `breakout_type`, `scanner`, `alert_time`, `entry_price`, `initial_stop_loss`, and `alert_date` are IMMUTABLE once inserted.
- **Mutable Tracking Columns**: `stop_loss`, `status`, `target_1`..`target_4`, and `exit_reason` are updated dynamically as trailing stops adjust or targets hit.
- **UPSERT Logic**: For the `alerts` table, the system uses strict deduplication: `ON CONFLICT (symbol, breakout_type, scanner, alert_date) DO NOTHING`. Stop loss trailing is handled by dedicated background tasks, never clobbered by new inserts. The `candidates` table (pre-alert screening) uses `ON CONFLICT (...) DO UPDATE SET status = CASE WHEN candidates.status IN ('QUALIFIED', 'OPEN') THEN EXCLUDED.status ELSE candidates.status END`.
- **Data Retention Policy**:
  - `alerts`: Retained permanently (never deleted).
  - `funnel_telemetry`: Retained for 90 trading days; purged nightly during Midnight Rotation.
  - `parquet_cache`: Retained for 30 calendar days.

## 11.2 Complete PostgreSQL DDLs (All Operational Tables)

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
    target_4 REAL, -- Added via idempotent ALTER TABLE migration in database.py
    CONSTRAINT alerts_dedup_idx UNIQUE (symbol, breakout_type, scanner, alert_date),
    CONSTRAINT chk_alerts_status CHECK (status IN ('OPEN', 'WIN', 'LOSS', 'TRAILING', 'EXPIRED', 'PARTIAL_WIN_1', 'PARTIAL_WIN_2', 'NEUTRAL'))
);

-- 2. Candidates Screening Table
CREATE TABLE IF NOT EXISTS candidates (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    breakout_type TEXT NOT NULL,
    alert_date DATE NOT NULL DEFAULT CURRENT_DATE,
    status TEXT NOT NULL DEFAULT 'FOUND',
    scanner TEXT,
    technical_score INTEGER,
    volume_ratio REAL,
    delivery_pct REAL,
    rr_ratio REAL,
    market_context TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, breakout_type, alert_date)
);

-- 3. Scanner Health Table
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

-- 4. Symbol Mappings Table (BSE Fallback Cache)
CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT PRIMARY KEY,
    bse_symbol TEXT NOT NULL,
    mapping_state TEXT NOT NULL DEFAULT 'ACTIVE',
    failure_count INTEGER DEFAULT 0,
    retry_after TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Funnel Telemetry Table
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

-- 6. Parquet Cache Table
CREATE TABLE IF NOT EXISTS parquet_cache (
    name TEXT NOT NULL,
    date DATE NOT NULL,
    data BYTEA NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (name, date)
);

-- 7. Breakout Watchlist Table
CREATE TABLE IF NOT EXISTS breakout_watchlist (
    symbol TEXT PRIMARY KEY,
    category TEXT,
    current_state TEXT,
    h1_status TEXT,
    m30_status TEXT,
    m15_status TEXT,
    m5_status TEXT,
    breakout_level REAL,
    trigger_level REAL,
    support_level REAL,
    invalidation_level REAL,
    invalidated_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    armed_at TIMESTAMPTZ,
    context_json JSONB,
    session_date TEXT,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Wealth Portfolio Table
CREATE TABLE IF NOT EXISTS wealth_portfolio (
    symbol TEXT PRIMARY KEY,
    cmp REAL,
    hold_score INTEGER,
    bucket TEXT,
    entry_price REAL,
    entry_date DATE,
    shares INTEGER,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 9. Bayesian Model Updates Table
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

-- 10. User Sessions Table
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- 11. Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 12. Pledge Cache Table
CREATE TABLE IF NOT EXISTS pledge_cache (
    symbol TEXT PRIMARY KEY,
    pledge_pct REAL NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 13. Scan Failures Table
CREATE TABLE IF NOT EXISTS scan_failures (
    id SERIAL PRIMARY KEY,
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    result TEXT NOT NULL,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 14. Rejected Alerts Table
CREATE TABLE IF NOT EXISTS rejected_alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    scanner TEXT NOT NULL,
    engine_version TEXT,
    rejection_reason TEXT,
    alert_date TEXT DEFAULT (CURRENT_DATE::TEXT),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    context JSONB
);

-- 15. Concall Cache Table
CREATE TABLE IF NOT EXISTS concall_cache (
    symbol TEXT PRIMARY KEY,
    concall_summary JSONB,
    management_confidence INTEGER,
    sentiment_score REAL,
    quarter TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 16. Validation History Table
CREATE TABLE IF NOT EXISTS validation_history (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    validation_date DATE NOT NULL,
    status TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 17. Data Cache Metadata
CREATE TABLE IF NOT EXISTS data_cache_metadata (
    cache_key TEXT PRIMARY KEY,
    last_updated TIMESTAMPTZ NOT NULL,
    ttl_seconds INTEGER NOT NULL,
    row_count INTEGER
);

-- 18. Push Subscriptions Table
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id SERIAL PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 19. Sector Rankings Table
CREATE TABLE IF NOT EXISTS sector_rankings (
    sector_name TEXT PRIMARY KEY,
    relative_strength REAL NOT NULL,
    rank INTEGER NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 20. RS Ratings Table
CREATE TABLE IF NOT EXISTS rs_ratings (
    symbol TEXT PRIMARY KEY,
    rs_score REAL NOT NULL,
    percentile REAL NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

# 12. CONCURRENCY, SYNCHRONIZATION & LOCK HIERARCHY

```text
Lock Acquisition Hierarchy (Strict Acquisition Order):
1. scanner_execution_lock (InstrumentedLock)
   └── 2. ProcessLock (flock + PostgreSQL Advisory Lock: pg_advisory_lock)
       └── 3. price_cache._fetch_lock (Prevents thundering herd API requests)
           └── 4. price_cache._lock (Protects internal _cache RAM dictionary)
```

---

# 13. AUTONOMOUS SCHEDULER & 24/7 EXECUTION BLUEPRINT

```python
# app/main.py Core Scheduler Implementation
def run_system_scheduler():
    logging.info("Starting autonomous 24/7 system scheduler...")
    while True:
        now = datetime.now(IST)
        
        # 1. Midnight Rotation at 00:00 IST
        if last_rotation_date != now.date():
            last_rotation_date = now.date()
            ApplicationContext.get_instance().new_trading_day()
            gc.collect()
            
        # 2. Daily Builder at 01:00 IST
        if now.hour == 1 and not daily_builder_ran:
            daily_builder_ran = True
            safe_run_daily_builder()
            ApplicationContext.get_instance().create_session()
            
        # 3. Wealth Engine Initial Setup at 02:00 IST
        if now.hour == 2 and not wealth_initial_ran:
            wealth_initial_ran = True
            safe_run_wealth_scan_initial()
            
        # 4. Readiness Verification Check at 08:30 IST
        if now.hour == 8 and now.minute >= 30 and not verify_scans_ran:
            verify_scans_ran = True
            verify_scans()
            
        # 5. Pre-Market Warmup at 09:14:30 IST
        if now.hour == 9 and now.minute == 14 and now.second >= 30 and not warmup_ran:
            warmup_ran = True
            fetch_watchlist_data(wl_df, period="10d", interval="15m")
            
        # 6. Market Hours Intraday Loop (09:15 - 15:30 IST)
        elif is_market_open(now):
            with scanner_execution_lock:
                # 5-min slot deduplicated CMP & performance updates
                if not last_perf or (now - last_perf).total_seconds() >= 300:
                    _run_performance_tracker_single()
                    last_perf = now
                if not last_mb_exit or (now - last_mb_exit).total_seconds() >= 900:
                    _run_multibagger_exit_single()
                    last_mb_exit = now
                safe_run_wealth_market_hours() # 5-min exit update, 15-min BUY scan
                
            # Multi-TF: candle-aligned cadence starting at 09:30 AM IST (after 09:15-09:30 bar completes)
            if now.hour < 15:
                current_slot = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
                if last_multi_tf is None or current_slot > last_multi_tf:
                    last_multi_tf = current_slot
                    with scanner_execution_lock:
                        _trigger_multi_tf()
                        
        # 7. Evening Batch Scanners at 18:00 IST (Sequential: EOD -> Reversal -> Pullback)
        if now.hour >= 18 and not evening_scanners_ran:
            evening_scanners_ran = True
            run_evening_batch_async() # Waits for Bhavcopy, hard 10-min timeout per scanner
            
        # 8. Multibagger Scanner Daily Run at 19:00 IST (Independent Branch)
        if now.hour >= 19 and last_multibagger_date != now.date():
            last_multibagger_date = now.date()
            _run_multibagger_scanner_single()
            
        time.sleep(15) # Loop sleeps 15s for precision slot timing
```

> [!IMPORTANT]
> **Execution Duration Timing Rule**: `duration_seconds` logged in `scanner_health` MUST be measured strictly AFTER acquiring `scanner_execution_lock` (when the scanner enters `RUNNING` status). Queue wait time during `QUEUED` or `DEFERRED` states is excluded to ensure accurate processing latency metrics.

---

# 14. ALERT LIFECYCLE, STATE MACHINE & COOLDOWN RULES

## 14.1 Alert Status Lifecycle State Machine
- `OPEN`: Signal triggered, entry active.
- `PARTIAL_WIN_1`: Target 1 hit. Stop loss trailed to **Breakeven (Entry Price)**.
- `PARTIAL_WIN_2`: Target 2 hit. Stop loss trailed to **Target 1 Price**.
- `WIN`: Target 3 hit (100% position liquidated). Target 4 is an informational runner target.
- `TRAILING`: Active stop loss trailing above entry price following EMA9/swing low.
- `LOSS`: Candle Low dropped below active `stop_loss`.
- `EXPIRED`: Signal failed to reach T1 within 20 trading days (40 days for REVERSAL).
- `NEUTRAL`: Position closed at breakeven.

> [!IMPORTANT]
> **Intrabar SL vs Target Precedence**: If a single 5m/1h candle touches both Stop Loss (Low <= SL) and Target (High >= T1/T2/T3), **Stop Loss (`LOSS`) takes conservative precedence**.
> **Terminal State Immutability Guard**: Once an alert reaches a terminal state (`WIN`, `LOSS`, `EXPIRED`, `CLOSED`), its `status`, `stop_loss`, and `exit_reason` columns are frozen and immutable.
> **Share Sizing & Remainder Routing**: Position sizing calculates integer shares. Any fractional rounding remainder is routed to the final T3 tranche so `remaining_shares` reaches `0` cleanly.

## 14.2 Candidate State Machine & Cascade Transitions

```text
[ IDLE ] ──(1H Trend & ADX Pass)──> [ HOURLY_APPROVED ] ──(30m Squeeze / Override)──> [ SETUP_ARMED ]
                                                                                              │
                                                                                      (15m EMA Alignment)
                                                                                              │
                                                                                              ▼
[ COOLDOWN ] <──(Exit Hit)── [ TRADE_ACTIVE ] <──(5m Thrust/Pullback)── [ ENTRY_READY ]
```

- `IDLE` $\rightarrow$ `HOURLY_APPROVED`: Triggered when 1H $\text{EMA}_9 > \text{EMA}_{20} > \text{SMA}_{50}$, $\text{Close} > \text{SMA}_{200}$ (or 50–199 bar fallback $\text{EMA}_9 > \text{EMA}_{20} > \text{SMA}_{50}$), $\text{ADX} \ge 18$, and distance $-0.02 \le \text{dist} \le 0.05$.
- `HOURLY_APPROVED` $\rightarrow$ `SETUP_ARMED`: Triggered when 30m BB Width Percentile $< 0.45$ and $-0.015 \le \text{dist} \le 0.025$ OR Fast Breakout Override ($\text{dist} < -0.015$ and Volume Ratio $> 1.2\text{x}$).
- `SETUP_ARMED` $\rightarrow$ `ENTRY_READY`: Triggered when 15m $\text{EMA}_9 > \text{EMA}_{20}$ and $-0.015 \le \text{dist} \le 0.025$.
- `ENTRY_READY` $\rightarrow$ `TRADE_ACTIVE`: Triggered when 5m execution criteria pass (Thrust mode: $\text{Close} > \text{prev\_high}$, $\text{Close} > \text{trigger} + 0.15 \times \text{ATR}_{20}$, Volume Ratio $> 1.2\text{x}$; Pullback mode: $\text{Low} \le \max(\text{trigger}, \text{EMA}_9)$, $\text{Close} > \text{prev\_high}$, Volume Ratio $> 1.0\text{x}$; Natural $R:R \ge 1.5$).
- `TRADE_ACTIVE` $\rightarrow$ `COOLDOWN`: Triggered on stop loss breach, target hit, or 20-day expiry (40-day for REVERSAL).

> **Production Implementation Audit Trail (§7.3 Alignment)**:
> 1. **Phase A Trend Gate**: Uses `ADX_MIN_THRESHOLD` (18) and allows a reduced trend fallback (`EMA9 > EMA20 > SMA50`) for symbols with 50–199 bars.
> 2. **Distance Gate**: Allows candidates from 2% above to 5% below breakout level (`-0.02 <= dist_to_breakout <= 0.05`).
> 3. **Execution Lock & Candle Alignment**: First intraday cycle runs on the completed 09:15–09:30 candle boundary at 09:30 AM IST and executes every 15 minutes (:00, :15, :30, :45) until 14:45 PM IST.
> 4. **Cooldown & Max Alerts Architecture**: Uses `ALERT_COOLDOWN_MINUTES["MULTI_TF"] = 240` (4 hours) for intraday deduplication and `SCANNER_MAX_ALERTS["MULTI_TF"] = 15`.

## 14.3 Intraday Exit Owner & Deduplication Architecture
- **Intraday Exit Owner**: `PerformanceTracker` / `CMP Exit Monitor` (`app/performance_tracker.py`) runs every 5 minutes during market hours for ALL active `OPEN` alerts across all 6 scanners (`EOD`, `MULTI_TF`, `REVERSAL`, `PULLBACK`, `WEALTH`, `MULTIBAGGER`).
- **Deduplication vs Cooldown Architecture**:
  - `alerts_dedup_idx UNIQUE(symbol, breakout_type, scanner, alert_date)`: Prevents duplicate alert rows within the same calendar day.
  - `ALERT_COOLDOWN_MINUTES`: Prevents re-alerting across consecutive trading sessions within the specified rolling minute window (e.g. 240m for Multi-TF, 40 trading days for Reversal).

---

# 15. COMPLETE REST API SPECIFICATIONS & STREAMING PROTOCOLS

Flask REST API (`app/dashboard_server.py`) specifications:

| Endpoint | Method | Auth Level | Description | Response JSON Schema |
| :--- | :--- | :--- | :--- | :--- |
| `/api/scanner_status` | `GET` | Public | Real-time scanner health & run duration. | `{"status": "ok", "scanners": [{"scanner_name": "EOD", "status": "OK", "today_alerts": 3, "duration_seconds": 12.5}]}` |
| `/api/trigger-scanner` | `POST` | Admin | Async manual trigger for a scanner. | `{"status": "success", "message": "Scanner EOD triggered"}` |
| `/api/lock-stats` | `GET` | Admin | Mutex lock contention statistics. | `{"acquisitions": 142, "max_wait_sec": 0.12, "contention_events": 0}` |
| `/api/wealth_data` | `GET` | Public | Wealth Engine portfolio data. | `{"status": "ok", "data": [{"Stock": "RELIANCE", "CMP": 2450.0, "HoldScore": 88}]}` |
| `/version` | `GET` | Public | Build metadata & release gate status. | `{"architecture_version": "8.4.3", "git_commit": "c1bf1e0b", "status": "RELEASE_GATE_APPROVED"}` |

> **Note**: The REST API surface also encompasses the 13 UI/UX streaming and operational endpoints documented in §17.

---

# 16. EXHAUSTIVE REPOSITORY MODULE INVENTORY & API INTERFACE CONTRACTS

## 16.1 Public API Interface Contracts

### 1. `PriceCache` (`app/price_cache.py`)
- `fetch_batch(symbols: list[str], interval: str, period: str) -> dict[str, pd.DataFrame]`
  - *Description*: Fetches batch OHLCV dataframes from RAM cache or provider fallback chain.
  - *Arguments*: `symbols` (list of ticker strings), `interval` (`"1d"`, `"15m"`, `"5m"`, `"1h"`), `period` (`"1y"`, `"3mo"`, `"1mo"`).
  - *Returns*: Dictionary mapping symbol ticker to OHLCV DataFrame.
  - *Exceptions*: Raises `ValueError` if interval unsupported.
  - *Caller Permissions*: All scanners, Wealth Engine, IndicatorManager.

### 2. `ScoringEngine` (`app/scoring_engine.py`)
- `calculate_score(symbol: str, df: pd.DataFrame, regime_ctx: dict) -> int`
  - *Description*: Computes centralized open-ended composite quality and breakout score (0–120+).
  - *Arguments*: `symbol` (ticker string), `df` (OHLCV daily dataframe), `regime_ctx` (market regime context dict).
  - *Returns*: Integer score on an open-ended scale (0–120+).
  - *Caller Permissions*: EOD Scanner, Multi-TF Scanner, Reversal Scanner, Pullback Pipeline.

### 3. `SLTargetHelper` (`app/sl_target_helper.py`)
- `compute_sl_and_target(df: pd.DataFrame, mode: str) -> dict`
  - *Description*: Computes dynamic stop loss, anti-trap buffer, target laddering (T1..T4), and R:R ratio.
  - *Arguments*: `df` (OHLCV dataframe), `mode` (`"EOD"`, `"REVERSAL"`, `"MULTI_TF"`, `"PULLBACK"`).
  - *Returns*: Dict containing `stop_loss`, `target_1`..`target_4`, `rr_ratio`, `sl_method`, `is_valid`.
  - *Caller Permissions*: All scanners.

---

# 17. UI/UX SPECIFICATIONS & STREAMING CONTRACTS (`app/dashboard_server.py`)

## 17.1 Frontend Architecture & API Topologies
The system frontend is powered by a lightweight, responsive web application served via a Flask WSGI engine (`app/dashboard_server.py`) protected by `ProxyFix` for robust cloud deployment across CDN and edge networking interfaces. The presentation architecture combines clean interactive interfaces with low-latency JSON REST endpoints designed for autonomous system surveillance:
- **Presentation Layer**: Pure vanilla HTML5, modern HSL/dark-mode styling via CSS, and vanilla reactive JavaScript components providing real-time visual feedback without heavy frontend framework overhead.
- **REST Contract Integrity**: All API json payloads serialize timestamp elements uniformly via `serialize_datetimes()` into strict ISO-8601 strings to guarantee deterministic frontend parsing.

## 17.2 Real-Time Polling & Notification Streaming Architecture
To prevent websocket connection drops and memory bloat over unstable cloud edge connections, the system utilizes an asynchronous, resilient short-polling and event push architecture:
- **Dynamic Surveillance Polling**: The dashboard client automatically queries lightweight endpoints (`/api/scanner_status`, `/api/summary`) at configurable cadence intervals to dynamically render scanner health status badges (`OK`, `RUNNING`, `DOWN`), execution durations, alert counts, and prevailing macroeconomic regime classifications (`BULL`, `NEUTRAL`, `BEAR`).
- **Live Event Stream Feed**: Endpoint `/api/notifications` delivers real-time chronological feeds of newly confirmed breakout alerts, near-miss candidate logs, and system diagnostics directly to frontend notification centers and floating snackbars.
- **State Synchronization Contracts**: Stateful endpoints `/api/notifications/mark_seen/<id>`, `/api/notifications/mark_all_seen`, and `/api/notifications/clear_all` provide atomic POST interface contracts for user notification acknowledgment and database cleanup.

## 17.3 VAPID WebPush Notification Pipeline
For remote mobile and desktop alerting, the server implements full Progressive Web App (PWA) push capabilities via WebPush protocols:
- **PWA Integration**: Serves `/manifest.json` and dedicated Service Worker implementation `/service-worker.js` with zero-caching headers to guarantee real-time Service Worker upgrades.
- **VAPID Subscription Contract**: Client applications obtain public VAPID authentication credentials via `/api/push/vapid_public_key` (`GET`). Upon receiving browser authorization, the subscription payload (`endpoint`, `p256dh` cryptographic key, and `auth` secret) is securely committed to database storage via `/api/push/subscribe` (`POST`). When any scanner pipeline generates an unrejected alert, background messaging dispatchers immediately broadcast WebPush payloads to registered client Service Workers.

## 17.4 Administrative Export & Capital Management Surface
- **Whitelisted Data Exports**: To support external audit and spreadsheet reconstruction, administrative endpoints `/admin/export/<table>` and `/admin/export/watchlist/<list_type>` stream raw database rows as downloadable CSV formats. SQL injection is mitigated via strict hard-coded table name whitelisting in the route handler.
- **Analytical Matrix Tools**: Provides deep mathematical evaluation endpoints including `/api/capital_info`, `/api/deposit_funds`, and `/api/analytics/expectancy_matrix`, computing Monte Carlo expectancy tables, Win/Loss distributions, and real-time portfolio margin requirements.

## 17.5 Counterfactual Shadow Tracking UI Pipeline
- **Rejected Trade Surveillance**: The Admin Dashboard (`/admin`) renders system-rejected alerts (`is_rejected = TRUE`) in a dedicated Counterfactual Shadow Table without altering live portfolio equity curves.
- **Shadow Status Enums**:
  - `👻 SHADOW WIN`: Rejected setup touched Target 1/2/3 before Stop Loss (Hypothetical Missed Win).
  - `👻 SHADOW LOSS`: Rejected setup touched Stop Loss before Target (Hypothetical Correct Rejection).
  - `👻 SHADOW EXPIRED`: Rejected setup timed out after 40 trading days.
- **Rejection Quality Telemetry**: Evaluates rejection engine accuracy in real-time:
  - $\text{True Negatives Rate (\%)} = \frac{\text{SHADOW\_LOSS Count}}{\text{Total Closed Shadow Alerts}} \times 100\%$ (Validates filter accuracy).
  - $\text{False Negatives Rate (\%)} = \frac{\text{SHADOW\_WIN Count}}{\text{Total Closed Shadow Alerts}} \times 100\%$ (Highlights overly aggressive gates).

## 17.6 Dynamic Sliding Queue UI Slider
- **Queue Position Resolution**: When multiple manual or automated scanner triggers enter the execution pipeline simultaneously, the server tags active queue states dynamically based on request timestamps (`updated_at` ASC).
- **UI Render**: Displays human-readable queue pills (`QUEUED-1`, `QUEUED-2`, `QUEUED-3`) on the Admin Dashboard health cards, updating smoothly as prior scanners release their process locks.

## 17.7 Mutex Lock Telemetry & Memory Heap Profiling Surface
- **Process Lock Telemetry (`/api/lock-stats`)**: Exposes lock acquisition counts, wait times, hold times, and contention events across `ProcessLock` instances (`eod_scanner`, `reversal_scanner`, `multi_tf_scanner`, `pullback_pipeline`, `wealth_engine`, `multibagger_scanner`).
- **Memory Profiler Timeline**: Renders stage timeline execution durations (`StageTimelineTracker`) and peak memory heap usage per universe chunk to guarantee zero OOM memory leaks.

---

# 18. VERBATIM PRODUCTION CONFIGURATION (`app/config.py`)

Below is the verbatim source code of `app/config.py`:

```python
# =====================================================================================
# app/config.py
# Centralized configuration for all scanners
# =====================================================================================

import os

# =====================================================================================
# BASE DIRECTORY
# =====================================================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

# =====================================================================================
# TELEGRAM CONFIG (DYNAMIC ENVIRONMENT READ)
# =====================================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")

_thread_eod      = os.getenv("THREAD_EOD")
_thread_multi_tf = os.getenv("THREAD_MULTI_TF")
_thread_1h       = os.getenv("THREAD_1H")
_thread_reversal = os.getenv("THREAD_REVERSAL")

THREAD_EOD      = int(_thread_eod)      if _thread_eod      else None
THREAD_MULTI_TF = int(_thread_multi_tf) if _thread_multi_tf else None
THREAD_1H       = int(_thread_1h)       if _thread_1h       else None
THREAD_REVERSAL = int(_thread_reversal) if _thread_reversal else None

# =====================================================================================
# DATA DIRECTORY & PATHS
# =====================================================================================

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_PATH = os.path.join(DATA_DIR, "elite_fundamental_watchlist.parquet")
DB_PATH = os.path.join(DATA_DIR, "alerts.db")

# =====================================================================================
# SYSTEM & PROFILING CONFIGURATION
# =====================================================================================

MEMORY_PROFILER_CONFIG = {
    "DEEP_DIAGNOSTIC_RSS_MB": 5.0,
    "MIN_DF_DELTA_MB": 1.0,
    "MAX_TRACEMALLOC_PEAK_MB": 20.0,
    "CONSECUTIVE_TRIGGER_COUNT": 3,
    "RATE_LIMIT_MINUTES": 30
}

# =====================================================================================
# API / FETCH CONFIGURATION
# =====================================================================================

DISABLE_NSE_SURVEILLANCE_FETCH = False  # Set to True in validation environments to avoid WAF/tarpit timeouts

# =====================================================================================
# SCORE THRESHOLDS & AI
# =====================================================================================

ENABLE_AI_SENTIMENT_SCORE = True  # Set False to disable experimental AI sentiment scoring for audit/backtest runs

SCORE_THRESHOLDS = {
    "15m": 78,
    "1h":  80,
    "1d":  82,
}

# =====================================================================================
# SCAN CONFIGURATION (Algorithm Parameters)
# =====================================================================================
ACTIVE_ALGO_VERSION = "SL_ENGINE_V7.1"  # Updated: Target Engine v7 Pipeline, Institutional S/R Clustering, Parallel Orchestration + Combined Audit Fixes

# =====================================================================================
# MOMENTUM BONUS CONSTANTS & RULE 10 RATIONALE
# =====================================================================================
# RS_BONUS (10 pts): Awarded if stock's 63-day RS rating is >= 80th percentile vs Nifty 50 over active scan universe.
# SECTOR_BONUS (8 pts): Awarded if stock belongs to a Top-3 RS sector holding 3-session hysteresis.
# MAX_MOMENTUM_BONUS (15 pts): Hard cap on combined momentum bonuses so RS (+10) and Sector (+8) co-exist (10+5=15) without clipping Sector to zero.
RS_BONUS = 10
SECTOR_BONUS = 8
MAX_MOMENTUM_BONUS = 15



MULTI_TF_CONFIG = {
    "MIN_SIGNALS":        2,
    "MIN_BODY_RATIO":     0.60,
    "MIN_CLOSE_POSITION": 0.70,
    "MAX_UPPER_WICK":     0.35,
    "MIN_VOLUME_RATIO":   1.2,
    "MIN_VOLUME_AVG":     150_000,
    "MIN_RSI":            52,
    "MAX_RSI":            87,
    "PULLBACK_TRIGGER_MODE": "PREVIOUS_HIGH", # Alternatives: PREVIOUS_OPEN, INSIDE_BAR, ENGULFING
}

LIVE_1H_CONFIG = {
    "MIN_SIGNALS":        3,
    "MIN_BODY_RATIO":     0.55,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK":     0.25,
    "MIN_VOLUME_RATIO":   2.0,
    "MIN_VOLUME_AVG":     100_000,
    "MIN_RSI":            55,
    "MAX_RSI":            86,
}

EOD_CONFIG = {
    "MIN_SIGNALS":        1,
    "MIN_BODY_RATIO":     0.45,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK":     0.35,
    "MIN_VOLUME_RATIO":   1.8,
    "MIN_VOLUME_AVG":     50_000,
    "MIN_RSI":            55,
    "MAX_RSI":            88,
}

EOD_ADVANCED_CONFIG = {
    "MAX_DISTANCE_FROM_52W_HIGH_PCT": 15.0,
    "MAX_SINGLE_DAY_MOVE_PCT": 15.0,
    "MAX_GAP_FROM_PRIOR_HIGH_PCT": 3.0,
    "GAP_LOOKBACK_BARS": 10,
    
    # ── Sustainability & Breakout Conviction ──
    "MAX_EXTENDED_BREAKOUT_ATR_MULT": 1.5,
    "GAP_AND_GO_PENALTY_MULT": 10,
    "GAP_AND_GO_MAX_PENALTY": 20,
    "MIN_ATR_EXPANSION_RATIO": 0.9,  # [FIX P1] Relaxed from 1.2 — 1.2 rejected steady uptrend breakouts
    "MIN_OBV_SLOPE": 0.0,
    
    # ── Prior Context & Tight Bases ──
    "PRE_BREAKOUT_LOOKBACK_BARS": 5,
    "MAX_PRE_BREAKOUT_RED_CANDLES": 2,
    "TIGHT_BASE_BB_WIDTH_PCTILE": 0.35,
    
    # ── [FIX] Structural Breakout Constraint Relaxation ──
    # Previously 0.20, which contradicted the fact that Bollinger Bands expand upon breakout.
    "MAX_BB_WIDTH_PCTILE": 0.80
}

REVERSAL_CONFIG = {
    "MIN_DROP_FROM_52W_HIGH": 20.0,
    "MAX_DROP_FROM_52W_HIGH": 45.0,
    # ── [FIX] Reversal RSI Constraint Relaxation ──
    # Since above_sma50 is a strict gate, the stock is recovering. Thus RSI won't be deeply oversold (<35) recently.
    "RSI_OVERSOLD_THRESHOLD": 38,
    "RSI_CURL_MIN": 50,
    "MIN_VOLUME_RATIO": 2.0,
    "MIN_AVG_DAILY_VOLUME": 300_000,
    "MIN_ROE": 12.0,
    "MIN_YOY_REVENUE_GROWTH": 8.0,
    "MAX_DROP_BELOW_SMA200": 20.0,
    "REVERSAL_COOLDOWN_TRADING_DAYS": 40
}

ALERT_COOLDOWN_MINUTES = {
    "WEALTH": 1440,       # 24 hours
    "MULTI_TF": 240,      # 4 hours (240 minutes)
    "EOD": 1440,          # 24 hours
    "REVERSAL": 10080,    # 7 days
    "PULLBACK": 10080,    # 7 days
    "MULTIBAGGER": 43200  # 30 days
}

SCANNER_MAX_ALERTS = {
    "WEALTH": 40,    # = sum of bucket caps: Core(15) + Growth(10) + Opportunistic(10) + QOS(5)
    "MULTI_TF": 15,
    "EOD": 10,
    "REVERSAL": 10,
    "PULLBACK": 10,
    "MULTIBAGGER": 10,
}

# =====================================================================================
# SCANNER LOOKBACK & THRESHOLD CONSTANTS
# (All formerly hardcoded inside scanner modules — centralised here for §7 preamble compliance)
# =====================================================================================

# Reversal Gate 8: RSI must have been below RSI_OVERSOLD_THRESHOLD within this many bars
REVERSAL_RSI_LOOKBACK = 15

# Multi-TF Phase B: Bollinger Band squeeze gate threshold (percentile rolling window)
BB_WIDTH_PCTILE_LOOKBACK = 60

# Multi-TF batch size (number of symbols fetched per provider call)
MULTI_TF_FETCH_BATCH_SIZE = 50

# =====================================================================================
# POSITION SIZING & RISK BUDGETING CONFIGURATION
# =====================================================================================
MAX_SL_DISTANCE_PCT = 8.0         # Max allowed stop loss distance % from entry
ACCOUNT_RISK_BUDGET_PCT = 1.0     # Max portfolio equity risk % per trade (Kelly / risk budget)
# [VERSION: PHASE2_SL_TARGET_IMPROVE_v1.0] Enforce MAX_POSITION_PCT concentration cap (25% max portfolio capital per single trade)
MAX_POSITION_PCT = 0.25

PULLBACK_CONFIG = {
    "VERSION": "pb-1.0.0",
    "LOOKBACK": 10, "CONFIRM": 3,
    "MIN_IMPULSE_GAIN_PCT": 8.0, "MIN_IMPULSE_ATR": 3.0, "MAX_IMPULSE_BARS": 20,
    "MIN_DEPTH_PCT": 23.6, "MAX_DEPTH_PCT": 61.8, "ABSOLUTE_FLOOR_PCT": 2.0,
    "MIN_DURATION": 3, "MAX_DURATION": 20,
    "MAX_INTERNAL_SWINGS": 2, "MAX_PB_VOLUME_RATIO": 0.75,
    "TRIGGER_VOL_MULT": 1.3, "MIN_CLOSE_LOCATION": 0.75,
    "MIN_BODY_ATR": 0.5, "MAX_UPPER_WICK": 0.25, "MAX_ENTRY_GAP_PCT": 3.0,
    "MAX_BONUS": 5, "PRIOR_WINDOW": 30,
    "OUTAGE_THRESHOLD_BUMP": 3,
    "MIN_HISTORY": 200,   # [VERSION: PB_BAR_FIX_v1.0] Lowered from 260 to 200 bars (1y daily data has ~250 trading bars)
    "MODE": "LIVE", "DEBUG_SWINGS": False,
}

# ── Data Quality Framework (V8.0) ──
QUALITY_VALIDATOR_VERSION = "V8.0"

QUALITY_SCORE_WEIGHTS = {
    "row_completeness": 40,
    "missing": 20,
    "price_sanity": 20,
    "continuity": 10,
    "freshness": 10,
}


# Configurable Score Bands for Advanced Outcome Analytics (Feature F-13)
SCORE_BANDS = [
    (70, 75),
    (75, 80),
    (80, 85),
    (85, 90),
    (90, 101),
]


# Maximum percentage of row loss accepted before logging a regression warning
MAX_HISTORY_SHRINK = 0.30



# Source reliability multipliers (0.0 to 1.0). Used for fallback evaluation.
SOURCE_RELIABILITY = {
    "NSE": 1.0,
    "Fyers": 1.0,
    "Cache": 0.95,
    "BSE": 0.70
}


# [FINDING-F FIX] Lowered ADX from 25 to 18. ADX 25+ indicates a trend that has
# already moved significantly. ADX 18-24 captures the accumulation/developing phase
# exactly where breakouts occur, while still filtering out choppy (ADX < 18) stocks.
ADX_MIN_THRESHOLD = 18
MIN_STOCK_PRICE = 100.0    # No penny stocks — matches daily_builder MIN_PRICE

# LIQUIDITY THRESHOLDS (in Rupees)
MIN_DAILY_LIQUIDITY_RUPEES_WATCHLIST = 150_000_000  # ₹15 Cr/day for raw watchlist
MIN_DAILY_LIQUIDITY_RUPEES_WEALTH    = 10_000_000   # ₹1 Cr/day for long-term wealth engine

DELIVERY_CONVICTION_THRESHOLDS = {
    "institutional": 60,
    "positional":    40,
    "moderate":      25,
    "intraday_churn": 0,
}

BATCH_DOWNLOAD_SIZE = 30
YAHOO_TIMEOUT = 30
PRICE_CACHE_TTL_SECONDS = 60  # Changed from 180s: Intraday runs every 5min (need fresh cache hit)


TELEGRAM_CHUNK_SIZE = 10
TELEGRAM_RETRIES = 3
TELEGRAM_TIMEOUT = 10
LOG_LEVEL = "INFO"

# =====================================================================================
# ANTI-FAKE-BREAKOUT PARAMETERS
# =====================================================================================

# Minimum % above prior high for a valid breakout (timeframe-aware)
MIN_BREAKOUT_MARGIN = {
    "15m": 0.003,   # 0.3% above prior high
    "1h":  0.005,   # 0.5%
    "1d":  0.007,   # 0.7%
}

# Breakout candle volume must be at least this multiple of 20-bar avg
MIN_BREAKOUT_VOLUME_RATIO = 2.5

# Reject if N prior candles are ALL bearish (no momentum build-up)
# Moved to EOD_ADVANCED_CONFIG["MAX_PRE_BREAKOUT_RED_CANDLES"]

# BASE_WIDTH below this = tight consolidation = bonus-worthy setup
BASE_TIGHTNESS_THRESHOLD = 1.5

# BASE_WIDTH above this = volatile/choppy = penalize
BASE_VOLATILITY_THRESHOLD = 3.0

# =====================================================================================
# ANTI-OPERATOR-TRAP PARAMETERS
# =====================================================================================

# Bars to look back for climax top volume pattern
CLIMAX_VOLUME_LOOKBACK = 20

# Bars to look back for lower-high pattern (failed breakout retest)
LOWER_HIGH_LOOKBACK = 6

# Minimum candle range as % of price (below this = thin spread trap)
MIN_CANDLE_RANGE_PCT = 0.003   # 0.3%

# =====================================================================================
# SL/TARGET ATR CAPS (max target distance from entry, per timeframe)
# =====================================================================================

ADAPTIVE_TARGET_CAPS = {
    "STRONG_BULL": {"15m": 10.0, "1h": 12.0, "1d": 15.0},
    "WEAK_BULL":   {"15m": 7.0,  "1h": 9.0,  "1d": 11.0},
    "BULL":        {"15m": 8.0,  "1h": 10.0, "1d": 12.0},
    "BEAR":        {"15m": 4.0,  "1h": 6.0,  "1d": 8.0},
    "WEAK_BEAR":   {"15m": 5.0,  "1h": 7.0,  "1d": 9.0},
    "STRONG_BEAR": {"15m": 3.0,  "1h": 4.0,  "1d": 6.0},
    "SIDEWAYS":    {"15m": 5.0,  "1h": 7.0,  "1d": 9.0},
    "RANGEBOUND":  {"15m": 5.0,  "1h": 7.0,  "1d": 9.0},
    "NEUTRAL":     {"15m": 6.0,  "1h": 8.0,  "1d": 10.0}
}

# =====================================================================================
# V6.0 INSTITUTIONAL CONFIGURATION
# =====================================================================================

MIN_NATURAL_RR = {
    "MULTI_TF": 1.5,
    "EOD": 2.0,
    "REVERSAL": 2.0,
    "PULLBACK": 2.0,
}

# =====================================================================================
# LOCK CONTENTION TELEMETRY CONFIGURATION
# =====================================================================================
LOCK_WAIT_WARNING_SECONDS = float(os.environ.get("LOCK_WAIT_WARNING_SECONDS", "10.0"))
LOCK_HOLD_WARNING_SECONDS = float(os.environ.get("LOCK_HOLD_WARNING_SECONDS", "120.0"))

MAX_REASONABLE_RR = {
    "MULTI_TF": 6.0,
    "EOD": 8.0,
    "REVERSAL": 4.0,
    "PULLBACK": 8.0,
}

MIN_TARGET_CONFIDENCE = 40
TARGET_CONFIDENCE_BASELINE = {
    "version": "2026_Q3",
    "percentile": 95,
    "sample_size": 18000,
    "value": 85
}

MIN_REWARD_POTENTIAL = {
    "MULTI_TF": 1.8,
    "EOD": 4.0,
    "REVERSAL": 3.0,
    "PULLBACK": 4.0,
    "MULTIBAGGER": 8.0,
}

MIN_STOP_PCT = {
    "MULTI_TF": 0.6,
    "EOD": 1.5,
    "REVERSAL": 2.0,
    "PULLBACK": 1.5,
    "MULTIBAGGER": 4.0,
}



TARGET_QUALITY_THRESHOLD = {
    "EOD":      55,
    "REVERSAL": 50
}

STRUCTURAL_RESISTANCE_SCORES = {
    "1H Swing High": 35,
    "30m Swing High": 30,
    "15m Swing High": 25,
    "Major Swing High": 40,
    "Swing High": 30,
    "Rolling Swing High": 20,
    "5m Swing High": 20,
    "R2": 20,
    "R1": 15,
}

STRUCTURAL_STOP = {
    "MAX_CLUSTER_WIDTH_ATR": 1.5,
    "DISASTER_BUFFER_PCT": 1.5,
    "SCORES": {
        "1H Swing Low": 35,
        "30m Swing Low": 30,
        "15m Swing Low": 25,
        "Swing Low Cluster": 40,
        "Swing Low": 30,
        "Rolling Swing Low": 25,
        "S1 (Discovery)": 20,
        "S1": 20,
        "SMA200": 30,
        "EMA20": 15,
        "SMA50": 15,
        "VWAP": 15,
        "Intraday Candle Low": 20
    },
    "BONUS_OVERLAP": 15,
    "USE_SUPPORT_CLUSTER": True
}

# =====================================================================================
# FALLBACK PRICE PROVIDER (when YFinance rate-limited)
# =====================================================================================

# ── DATA PROVIDER SETTINGS ──────────────────────────────────────────────────────────
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "auto")  # auto, yfinance, fyers, or kite

# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Provider routing policy and capabilities configuration
ROUTING_POLICY_VERSION = 2

PROVIDER_ROUTING_POLICY = {
    "price_1d": ["yahoo", "fyers", "bse"],
    "price_1wk": ["yahoo", "fyers", "bse"],
    "price_1mo": ["yahoo", "fyers", "bse"],
    "price_1h": ["fyers", "yahoo", "bse"],
    "price_30m": ["fyers", "yahoo", "bse"],
    "price_15m": ["fyers", "yahoo", "bse"],
    "price_5m": ["fyers", "yahoo", "bse"],
    "price_1m": ["fyers", "yahoo", "bse"],
    "live_quotes": ["fyers", "yahoo", "bse"],
    "bhavcopy_delivery": ["nse_bhavcopy", "bse_bhavcopy"],
    "promoter_pledge": ["bse_corporate", "nse_corporate"],
    "default": ["fyers", "yahoo", "bse"]
}

PROVIDER_CAPABILITIES = {
    "yahoo": {
        "bulk": True,
        "live": False,
        "intraday": True,
        "historical": True
    },
    "fyers": {
        "bulk": False,
        "live": True,
        "intraday": True,
        "historical": True
    },
    "bse": {
        "bulk": True,
        "live": False,
        "intraday": False,
        "historical": True
    }
}

STAGE_PERFORMANCE_BUDGETS = {
    "download_seconds": 5.0,
    "fallback_seconds": 3.0,
    "validation_seconds": 2.0,
    "indicators_seconds": 15.0,
    "parquet_write_seconds": 2.0,
    "scanner_seconds": 10.0,
    "database_seconds": 2.0,
    "cleanup_seconds": 1.0,
    "total_scan_seconds": 60.0
}

# ── FYERS CONFIGURATION ──────────────────────────────────────────────────────────
FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
FYERS_REDIRECT_URL = os.getenv("FYERS_REDIRECT_URL", "https://elitebreakoutsystem-production-4ad2.up.railway.app/fyers/callback")
FYERS_TOKEN_PATH = os.path.join(DATA_DIR, "fyers_token.txt")


REGIME_POLICIES = {
    "STRONG_BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 5,
        "min_target_quality_override": 60,
        "min_reward_potential_mult": 1.5,
        "capital_allocation_mult": 1.0
    },
    "WEAK_BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    },
    
    "BULL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    },
    "BEAR": {
        "score_modifier": 5,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 1,
        "min_target_quality_override": 80,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "SIDEWAYS": {
        "score_modifier": 8,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 2,
        "min_target_quality_override": 75,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "RANGEBOUND": {
        "score_modifier": 8,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 2,
        "min_target_quality_override": 75,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "WEAK_BEAR": {
        "score_modifier": 10,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 1,
        "min_target_quality_override": 80,
        "min_reward_potential_mult": 0.8,
        "capital_allocation_mult": 0.5
    },
    "STRONG_BEAR": {
        "score_modifier": 10,
        "allow_mean_reversion": False,
        "max_new_positions_per_day": 0,
        "min_target_quality_override": 100,
        "min_reward_potential_mult": 0.5,
        "capital_allocation_mult": 0.0
    },
    "NEUTRAL": {
        "score_modifier": 0,
        "allow_mean_reversion": True,
        "max_new_positions_per_day": 3,
        "min_target_quality_override": 65,
        "min_reward_potential_mult": 1.0,
        "capital_allocation_mult": 1.0
    }
}

# ── Target Engine v7 — FINAL FROZEN ──────────────────────────────────────────

# For Enum typing, though Enum is defined in sl_target_helper.
# We will use string representations here to avoid circular imports, 
# or just redefine them if we need them, but it's better to keep strings in config 
# and map them to enums in the helper.
# Actually, the spec says "TARGET_SOURCE_WEIGHTS = { TargetSource.EQUAL_HIGH: 10 ... }"
# To do this cleanly without circular import, we can define the enum here or in a separate file.
# The spec puts the Enum in sl_target_helper.py. So we'll use strings in config and the engine will map/handle.
# Let's use the string names matching the enum keys.

TARGET_SOURCE_WEIGHTS = {
    "EQUAL_HIGH":     10,
    "RESISTANCE":     10,
    "HIGH_20D":        9,
    "PREV_DAY_HIGH":   9,
    "HIGH_52W":        8,
    "ABCD":            9,
    "RETRACE_50":      8,
    "RETRACE_618":     7,
    "RETRACE_382":     6,
    "FIB_127":         7,
    "FIB_162":         6,
    "SMA200":          8,
    "BB_MID":          7,
    "SMA50":           6,
    "FIB_200":         5,
    "ATR_PROJ":        4,
    "R1":              5,
    "R2":              4,
    "ROUND_NUM":       0,
}

FIB_200_WEIGHTS = {"BULL": 7, "TRENDING": 7, "NEUTRAL": 5, "BEAR": 2}

SOURCE_PRIORITY = {
    "EQUAL_HIGH":     1,
    "RESISTANCE":     2,
    "HIGH_20D":       3,
    "PREV_DAY_HIGH":  4,
    "HIGH_52W":       5,
    "ABCD":           6,
    "RETRACE_618":    7,
    "RETRACE_50":     8,
    "RETRACE_382":    9,
    "FIB_127":        10,
    "FIB_162":        11,
    "SMA200":         12,
    "SMA50":          13,
    "BB_MID":         14,
    "FIB_200":        15,
    "ATR_PROJ":       16,
    "R1":             17,
    "R2":             18,
    "ROUND_NUM":      99,
}

TARGET_CONFLICT_POLICY = {
    "EOD":      "REGIME",
    "MULTI_TF": "CONFIDENCE",
    "REVERSAL": "SECOND_NEAREST",
    "PULLBACK": "REGIME",
}

EXIT_PROFILES = {
    "CONSERVATIVE": {"t1": 25, "t2": 50, "t3": 25},
    "BALANCED":     {"t1": 30, "t2": 40, "t3": 30},
    "AGGRESSIVE":   {"t1": 20, "t2": 30, "t3": 50},
}

SCANNER_EXIT_PROFILE = {
    "EOD":      "BALANCED",
    "MULTI_TF": "AGGRESSIVE",
    "REVERSAL": "CONSERVATIVE",
    "PULLBACK": "BALANCED",
}

FIB_EXTENSIONS   = [1.272, 1.618, 2.0]
FIB_RETRACEMENTS = [0.382, 0.500, 0.618]
ABCD_BC_RETRACE_MIN = 0.382
ABCD_BC_RETRACE_MAX = 0.786
FIB_200_GATE     = {"min_adx": 30, "min_vol_ratio": 2.0, "require_above_vwap": True}

ROUND_NUMBER_BOOST      = 8
ROUND_NUMBER_PCT        = 0.005
TARGET_CLUSTER_WINDOW_ATR_FRAC = 0.5
TARGET_CLUSTER_WINDOW_PCT      = 0.0075

#           atr_base  sl_atr_buf  sl_pct_buf  max_sl_atr
_MODE_CONFIG = {
    "EOD":      (2.00,    0.80,       0.0075,     3.0),
    "MULTI_TF": (1.50,    0.50,       0.0050,     3.0),
    "REVERSAL": (2.00,    1.00,       0.0100,     3.5),
    "PULLBACK": (2.00,    0.75,       0.0075,     3.0),   # Pullback Continuation
}


SCANNER_MULTI_TF = "MULTI_TF"

```

---

# 19. DETERMINISTIC RECONSTRUCTION ANSWERS (Q1 – Q36)

### Q1 – Q3: Production Config & Indicator Parameters
- **Exact Indicators Calculated**: EMA9, EMA20, EMA50, EMA200; SMA20, SMA50, SMA100, SMA200; ATR20 (Wilder smooth); ADX14 (Wilder smooth); RSI14 (Wilder smooth); MACD (12, 26, 9); OBV (On-Balance Volume); VWAP (intraday); BB Width (20-period, 2.0 std dev).

### Q4: Exact DataFrame Schemas
- **`watchlist`**: `["Stock", "Category", "Sector", "Industry", "Market Cap Cr", "ROE %", "ROCE %", "Debt/Equity", "YOY Revenue %", "YOY Profit %", "PEG Ratio", "Promoter_Pledge"]`
- **`ohlcv_daily`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "EMA_50", "SMA_50", "SMA_200", "ATR_20", "ADX_14", "RSI_14", "OBV", "MACD", "MACD_SIGNAL", "MACD_HIST", "BB_WIDTH", "BB_WIDTH_PCTILE", "HIGH_52W", "SWING_LOW", "SWING_HIGH"]`
- **`ohlcv_1h`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "SMA_50", "SMA_200", "ADX_14", "PRIOR_20D_HIGH"]`
- **`ohlcv_30m`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "VWAP", "RSI_14", "ATR_20"]`
- **`ohlcv_15m`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "VWAP", "RSI_14", "ATR_20"]`
- **`ohlcv_5m`**: `["Open", "High", "Low", "Close", "Volume", "VWAP", "EMA_9", "EMA_20", "ATR_20"]`

### Q5 – Q7: Risk Engine & Target Allocation
- **No Structural Stop Fallback**: If no swing low or pivot support is identified, stop loss defaults to $\text{Entry} - (2.0 \times \text{ATR}_{20})$.
- **Multiple Resistance Levels**: Targets selected by ascending order of resistance levels ($R_1 < R_2 < 52W\text{ High}$).
- **RR Acceptance**: A trade is accepted IF AND ONLY IF `natural_rr >= MIN_NATURAL_RR[mode]`.

### Q8 – Q10: Scanner Rejection & Exception Handling
- **Rejection Codes**: `EOD001` (Illiquid), `EOD002` (Candle quality failure), `REV004` (Fallen knife cooldown), `MTF013` (VWAP violation).
- **Exception Rule**: On symbol exception inside a chunk, log error to `scan_failures` table and CONTINUE to next symbol.

### Q11 – Q13: Provider Retries & Circuit Breakers
- **Provider API**: 3 attempts with exponential backoff ($2^{\text{attempt}} \times 1\text{s}$). Provider circuit breaker stays OPEN for 300 seconds (5 mins) after 3 consecutive failures.
- **Pipeline Data Quality Circuit Breaker**: Evaluated globally per-scanner execution (e.g., EOD, Pullback). If the combined count of `stale_data` + `no_data` + `data_quality` failures $\ge$ 25% of the total watchlist, the scanner is aborted (`status = "DOWN"`), triggering an unthrottled Web Push Notification and Telegram alert to the system admins.

### Q14 – Q15: Scheduler & Overrun Policy
- Scanner overrun (>10m) triggers a process warning log and forces completion before starting next batch.
- On Railway restart at 11:40 AM, session re-attaches to active trading day state without clearing database tables.

### Q16 – Q18: Database Indexes & Pool Options
- **Indexes**: `CREATE INDEX idx_alerts_symbol ON alerts(symbol);`, `CREATE INDEX idx_alerts_date ON alerts(alert_date);`.
- **Pool Settings**: Min=5, Max=50 connections, 15s acquire timeout. Isolation level: `READ COMMITTED`.

### Q19 – Q22: Presentation, Short-Polling & WebPush Protocols
- **Primary Delivery Protocol**: Primary signal delivery uses REST HTTP short-polling (5–15s dashboard cadence) + VAPID Web Push notifications + Telegram bot alerts. Optional WebSocket protocol for streaming dashboard events: `{ "type": "ALERT_NEW", "payload": { "symbol": "RELIANCE", "scanner": "EOD", "entry": 2450.0 } }`.
- **Notification Order**: DB persistence FIRST $\rightarrow$ Telegram broadcast SECOND $\rightarrow$ Web Push THIRD.

### Q23 – Q26: Session Lifecycle & Memory Eviction
- **Midnight Rotation**: Clears ephemeral RAM caches, destroys `SessionContext`, and triggers `gc.collect()`.
- **Memory Eviction & Budget Thresholds**: 2.0 GB System RAM budget. EPHEMERAL tier evicted when RSS exceeds 1200 MB RAM (60% warning threshold). Emergency GC and heap trimming (`malloc_trim()`) triggered if RSS exceeds 1800 MB (90% limit).

### Q27 – Q28: Module Dependency Rules & `.env.example`
- **Forbidden Imports**: `database` MUST NOT import `dashboard_server`. `price_cache` MUST NOT import `scoring_engine`.

```text
# .env.example
DATABASE_URL=postgresql://user:pass@localhost:5432/breakout_db
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyZ
CHAT_ID=-100123456789
FYERS_CLIENT_ID=XXXXXX-100
FYERS_SECRET_KEY=YYYYYYYYYY
DATA_PROVIDER=auto
```

### Q29 – Q36: Individual AI Reconstruction Answers
- **Q29 (Import Graph Order)**: `config.py` $\rightarrow$ `core_enums.py` $\rightarrow$ `database.py` $\rightarrow$ `lock_utils.py` $\rightarrow$ `price_cache.py` $\rightarrow$ `indicator_manager.py` $\rightarrow$ `unified_fetcher.py` $\rightarrow$ `scoring_engine.py` $\rightarrow$ `scanners` $\rightarrow$ `main.py`.
- **Q30 (Reversal Cooldown Precedence)**: Tier 2 (40-day Fallen Knife Defense) takes precedence over Tier 1 (7-day alert dedup).
- **Q31 (Date & Timezone Standards)**: All date operations use IST (`Asia/Kolkata`) `datetime.now(ZoneInfo("Asia/Kolkata"))`.
- **Q32 (Currency Standard)**: All price and risk metrics use Indian Rupees (₹ / RS).
- **Q33 (Idempotent DB Migrations)**: All database DDL changes use `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- **Q34 (Process Lock Policy)**: All scanner runs use `ProcessLock` or `scanner_execution_lock` to enforce single-instance execution.
- **Q35 (Telegram Payload Format)**: All Telegram posts use HTML parsing mode with explicit symbol, price, SL, targets, score, and TradingView chart links.
- **Q36 (Health Heartbeat SLA)**: Scanner health updated on every run; marked `DOWN` if heartbeat gap exceeds 3x cadence during market hours.

---

# 20. DEPLOYMENT VERIFICATION, FAILURE MATRIX & GOLDEN TEST SUITES

## 20.1 Complete Production Deployment Gates (All 17 Gates)

```python
# Gate 6 Memory Budget Checklist Implementation
def test_gate6_production_readiness_checklist(self):
    """Gate 6: Production Readiness Checklist (Memory Budget Alignment)."""
    import gc
    from forensics import forensics
    gc.collect() # PURGE UNREFERENCED TEST ALLOCATIONS
    mem = forensics.get_memory_stats()
    assert mem["rss_mb"] < 1200.0, f"Memory threshold breached: {mem['rss_mb']} MB"
```

1. **Gate 1: Cold Start Import Speed**: Verify total import latency $\le 5.0\text{s}$.
2. **Gate 2: Unsupported Imports Audit**: Ensure zero forbidden external libraries (`scikit-learn`, `tensorflow`, `ta-lib`, etc.).
3. **Gate 3: Smoke Execution**: Run full scanner smoke test suite in $\le 30.0\text{s}$.
4. **Gate 4: AST Method Signature Reflection Audit**: Validate public function signatures across all 54 modules.
5. **Gate 5: Railway Integration Contract**: Verify environment variable resolution (`DATABASE_URL`, `PORT`).
6. **Gate 6: Production Readiness Checklist**: Verify RAM usage budget ($\text{RSS} < 1200.0\text{ MB}$ with explicit `gc.collect()`).
7. **Gate 7: Dependency Reproducibility**: Ensure all requirements in `requirements.txt` are strictly pinned.
8. **Gate 8: Scheduler 24h Timeline Simulation**: Simulate 24-hour cycle execution without blocking threads.
9. **Gate 9: Memory Budget Assertions**: Verify thread pool count $< 60$ and peak RAM $< 1200.0\text{ MB}$.
10. **Gate 10: Alert Contract Schema Compliance**: Ensure alert JSON payloads contain required keys (`symbol`, `entry_price`, `stop_loss`, `target_1`..`target_4`, `score`).
11. **Gate 11: Scanner Execution Invariants**: Enforce `entry_price > stop_loss` and `target_1 >= entry_price`.
12. **Gate 12: DB Connection Pool Timeout**: Verify database pool acquires connection within $\le 15.0\text{s}$.
13. **Gate 13: `/version` Endpoint Health**: Validate build metadata, git commit hash, and release gate status.
14. **Gate 14: Earnings Calendar Metadata & UI Event Badging**: Enrich all generated alerts with earnings calendar dates and render visual event mark badges (`🔴 RESULTS TODAY`, `🟠 RESULTS IN 1D`, `🟡 RESULTS IN 3D`, `⚠️ UNVERIFIED`) on the UI without hard-blocking scanner setups.
15. **Gate 15: Quality Trajectory Invariants**: Verify fundamentals trajectory calculations.
16. **Gate 16: Forensic Risk Tiers**: Ensure CFO/PAT ratio $< 0.5$ or Debt/Equity $> 2.0$ triggers `HIGH`/`REJECT` risk tiers.
17. **Gate 17: Data Readiness Policy**: Confirm watchlist parquet freshness before scanner runs.

## 20.2 System Failure Decision Matrix

| Failure Event | System Response & State Handling | Recovery & Fallback Protocol |
| :--- | :--- | :--- |
| **TradingView Scraper Outage** | Scrape loop fails at 01:00 IST | Retain prior day's `watchlist.parquet`, emit Telegram admin alert |
| **Yahoo Finance API Outage** | Request timeout / HTTP 5xx error | Fallback to Fyers REST API v3 $\rightarrow$ BSE |
| **PostgreSQL Database Disconnect**| SQL execution exception | Exponential backoff (5 retries, 2s delay), queue alerts in RAM array |
| **Railway Container Restart** | Process terminates & reboots at 11:40 AM | Boot sequence restores state machine from Postgres without clearing data |
| **RSS Memory $> 400.0\text{ MB}$** | Memory profiler warning threshold | Trigger `gc.collect()`, evict EPHEMERAL price cache tier |
| **Scanner Overrun ($> 10\text{m}$)**| Hard timeout flag set | Gracefully exit candidate loop, save partial alerts, log outcome |

## 20.3 Golden Reference Test Vectors & Deterministic Outputs

```python
def test_golden_scenario_1_bullish_breakout(self):
    """Golden Reference Test Vector 1: Bullish Breakout Confirmation."""
    df = create_synthetic_ohlcv(
        close=2450.0, volume_ratio=3.2, body_ratio=0.72, close_pos=0.85, upper_wick=0.10
    )
    # Frozen Bayesian weights fixture for deterministic testing
    bayesian_fixture = {"regime": "BULL", "weights": {"volume": 1.2, "momentum": 1.0, "quality": 1.1}}
    score = scoring_engine.calculate_score("RELIANCE", df, regime_ctx={"regime": "BULL"}, bayesian_weights=bayesian_fixture)
    sl_res = compute_sl_and_target(df, mode="EOD")
    
    # Expected Deterministic Output Values (using pytest.approx for floating point comparisons)
    assert score == 86, f"Expected Score 86, got {score}"
    assert sl_res["stop_loss"] == pytest.approx(2410.50, abs=1e-2), f"Expected SL 2410.50, got {sl_res['stop_loss']}"
    assert sl_res["target_1"] == pytest.approx(2549.25, abs=1e-2), f"Expected T1 2549.25, got {sl_res['target_1']}"
    # Natural R:R (T1-based): Risk = 39.50, Reward = 99.25, R:R = 2.51 (Passes MIN_NATURAL_RR["EOD"] >= 2.0 gate with +0.51 margin)
    assert sl_res["rr_ratio"] >= 2.0, f"Expected R:R >= 2.0, got {sl_res['rr_ratio']}"
    # Reward Potential (T3-based): Risk = 39.50, T3 Reward >= 158.00 (T3 >= 2608.00) -> Reward Potential >= 4.0
    assert sl_res["target_3"] >= pytest.approx(2608.00, abs=1e-2), f"Expected T3 >= 2608.00 (4.0R reward potential), got {sl_res['target_3']}"
```

---

# 21. V9 CLEAN ARCHITECTURE BLUEPRINT & VERSIONING POLICY

## 21.1 Versioning & Schema Migration Policy
- **Major Version Bump**: Triggered on breaking API schema changes, DDL structural updates, or indicator algorithm modifications.
- **Idempotent Database Migrations**: Database schema updates MUST be executed via idempotent SQL statements (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).
- **Backward Compatibility Invariant**: Database columns and REST API keys MUST NEVER be removed or renamed in minor updates; deprecated fields are soft-deprecated according to Rule 58.

## 21.2 Deprecation & Architectural Change-Log (Old vs. New & Commit Mapping)

Per Rule 63, ANY future modification, refactoring, or architectural change MUST log an entry in this section detailing Old vs. New behavior, Commit ID, Rationale, and Version Tag.

| Date & Commit ID | Version Tag | Old Behavior | New Behavior | RCA & Rationale for Change |
| :--- | :--- | :--- | :--- | :--- |
| **2026-07-25**<br>`PENDING` | `DOC_AUDIT_CLOSURE_v1.0` | Documentation lacked explicit specifications for Sections 8, 10, and 17 (TOC promises), and only documented EOD scanner in Section 7 without the SL/Target algorithm detail. | Added complete, exhaustive specifications for Reversal, Multi-TF, Pullback, Multibagger, and Wealth buy-scan flows in Section 7; complete SL/Target Engine v7 spec in Section 7A; Fundamentals Data Pipeline in Section 8; Price Cache & Parquet Sidecars in Section 10; and UI/UX & Streaming Contracts in Section 17. | Direct addressing of critical audit feedback (Points 1.1, 1.2, 1.3) to achieve 100% deterministic, self-contained documentation for zero-code system reconstruction. |
| **2026-07-25**<br>`5f448211` | `UNIFIED_FETCHER_FALLBACK_SUCCESS_LOG_v1.0` | Live quote fallbacks silently discarded pending symbols without logging success. | Added explicit `INFO` logging (`✅ [BSE] Successfully resolved fallback live quote for {orig} ({y_sym}): ₹{val:.2f}`) for Fyers, Yahoo, and BSE live quote resolution. | Eliminates ambiguity in raw terminal and Railway logs when a primary provider returns 404 but a fallback provider successfully retrieves live data. |
| **2026-07-25**<br>`8d1cea6e` | `DOC_1GB_RAM_MINIMUM_v1.0` | Container memory budget defined as **500 MB RAM**. | Container minimum RAM requirement updated to **1.0 GB RAM (1024 MB)**. | Aligned container budget with empirical production memory profiles where multi-threaded 300-symbol rolling calculations reach 800-888 MB RSS before `malloc_trim(0)`. |
| **2026-07-25**<br>`3d67d436` | `DOC_FINAL_DETERMINISTIC_SPEC_v1.0` | Wealth Engine phase contracts, telemetry JSON schema, and restart checkpoints were implied. | Added Sections 23.16–23.19 explicitly specifying Phase Contract Matrix, Telemetry Schema, Cache Destruction Timeline, and Recovery Checkpoints. | Fulfills 100% deterministic zero-code reconstruction requirement. |
| **2026-07-25**<br>`0265cca7` | `DOC_RUNTIME_OPERATIONAL_SEMANTICS_v1.0` | Operational semantics scattered across code comments. | Added Section 23 with Rejection Taxonomy, SLAs, Ingestion Lifecycle, and Logging Taxonomy. | Standardizes dynamic execution behavior for AI models. |
| **2026-07-25**<br>`254fc297` | `DOC_SYMBOL_RESOLUTION_ENGINE_v1.0` | Symbol mapping lookup algorithm was implicit in provider utilities. | Added Section 9.2 with explicit candidate generation order (`NSE:SYM-EQ` $\rightarrow$ `NSE:SYM-BE` $\rightarrow$ `BSE:CODE`), database lookup rules, and exponential backoff. | Prevents AI models from inventing custom fallback logic. |
| **2026-07-25**<br>`1f46c368` | `DOC_12POINTS_EXPANDED_v1.0` | Code contracts, `config.py` dump, and indicator math were partially omitted. | Incorporated all 12 user audit review points into Sections 4, 6, 9, 11, 14, 16, 20. | Complete self-contained reconstruction specification. |
| **2026-07-24**<br>`9866aa8e` | `DOC_2DOC_RESTRUCT_v1.0` | Documentation split across 7 fragmented markdown files. | Consolidated documentation into **EXACTLY TWO canonical files**: `docs/SYSTEM_SPECIFICATION.md` and `docs/SYSTEM_ARCHITECTURE.md`. | Mandated 2-document rule (Rule 58) for single source of truth. |

---

# 22. AI RECONSTRUCTION CHECKLIST & MODULE DEPENDENCY BLUEPRINT

To build an exact replica of this codebase from zero with 100% deterministic fidelity, follow this exact step-by-step creation sequence:

```text
Creation Order & Module Dependency Hierarchy:

Step 1: Foundational Constants & Core Types
├── app/config.py               # (Constants, thresholds, provider policies)
├── app/core_enums.py           # (ProviderResult, ScanOutcome, CandidateState)
└── app/core_models.py          # (TradeStructure, ScanFailure)

Step 2: Database Layer & Lock Utilities
├── app/database.py             # (PostgreSQL DDLs, connection pool DB_MAXCONN=50)
└── app/lock_utils.py           # (ProcessLock pg_advisory_lock)

Step 3: Contexts, Telemetry & Profiling
├── app/application_context.py # (Singleton app context)
├── app/session_context.py     # (Trading-day state machine)
├── app/dataset_registry.py    # (Memory dataset tier registry)
├── app/memory_profiler.py     # (RSS delta & memory tracking)
├── app/telemetry_manager.py   # (Timeline & funnel logging)
├── app/metrics_collector.py   # (Prometheus/Grafana integration metrics)
└── app/system_health.py       # (Component health and uptime tracking)

Step 4: Data Acquisition & Price Cache Infrastructure
├── app/price_cache.py         # (3-tier per-symbol RAM cache & timestamp normalizer)
├── app/watchlist_cache.py     # (Watchlist parquet parsing & symbol corrections)
├── app/indicator_manager.py   # (RSI, ADX, EMA, ATR calculation bundles)
├── app/delivery_data.py       # (NSE Bhavcopy delivery scraper)
├── app/surveillance.py        # (NSE ASM/GSM blacklist scraper)
├── app/data_providers/provider_selector.py # (Routing policy authority)
├── app/data_providers/fyers_fetcher.py      # (Fyers API client & 99-day cap)
├── app/data_providers/unified_fetcher.py    # (Fyers -> YFinance -> BSE chain)
└── app/price_provider.py      # (BSE mapping state machine & fallback)

Step 5: Scoring, Risk & Quant Engines
├── app/scoring_engine.py      # (Centralized candidate scoring 0-100)
├── app/sl_target_helper.py    # (Dynamic stop loss, anti-trap buffer & validator)
├── app/performance_tracker.py # (Exit engine & dynamic partial profit state machine)
├── app/trade_ranking_engine.py# (Multi-factor candidate ranking)
├── app/macro_utils.py         # (Market regime engine & sector rankings)
├── app/strategy_policy.py     # (Regime-aware threshold modifiers)
├── app/forensic_engine.py     # (Forensic risk tiers & CFO/PAT gates)
├── app/quality_trajectory.py  # (Fundamentals trajectory score)
└── app/block_deal_detector.py # (Institutional footprints & bonus)

Step 6: Quantitative Scanners & Wealth Engines
├── app/eod_scanner.py         # (EOD Breakout Scanner & un-nested health)
├── app/reversal_scanner.py    # (Reversal Scanner & 30-day fallen knife cooldown)
├── app/pullback_pipeline.py   # (Pullback Pipeline Scanner & evidence bonus)
├── app/multi_tf_scanner.py    # (Multi-TF Intraday 4-stage cascade scanner)
├── app/wealth_engine.py       # (Wealth Engine Screener & 5m exit monitor)
└── app/multibagger.py         # (Multibagger Compounder Screener & exit monitor)

Step 7: Presentation, Notification & Entrypoint
├── app/daily_builder.py       # (TradingView screener scraper at 01:00 IST)
├── app/dashboard_server.py    # (Flask REST API, Gzip middleware & session cache)
├── app/telegram_engine.py     # (Telegram Bot API broadcast handler)
├── app/push_service.py        # (VAPID Web Push notification engine)
└── app/main.py                # (24/7 Autonomous scheduler entrypoint)
```

---

# 23. RUNTIME EXECUTION & OPERATIONAL SEMANTICS

## 23.1 Wealth Engine 4-Phase Internal Pipeline Architecture
The Wealth Engine (`app/wealth_engine.py`) operates as a multi-stage pipeline:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE A: BULK DATA ACQUISITION & WORKER BATCHING                        │
│ Input: Watchlist Parquet (287 symbols) + Benchmark Index (^NSEI)       │
│ Execution: Batch size = 50 symbols, Worker Threads = 3                  │
│ Output: Raw OHLCV Cache (1Y Daily)                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: CANDIDATE SELECTION                                            │
│ Input: Raw OHLCV + Watchlist Fundamentals                               │
│ Operations: Piotroski F-Score (min 6), Block Deal Bonus, V5 Mapper      │
│ Output: Evaluated candidate DataFrame with FM_Score & Completeness Flag │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: ENTRY TIMING & SECTOR CONCENTRATION GATES                      │
│ Input: Evaluated Candidate DataFrame                                    │
│ Gates: Top-15 Core, Top-10 Growth, Top-10 Opp, Top-5 QOS Caps          │
│ Constraints: Max 25% sector cap, Max 2 per industry sub-group            │
│ Output: BUY / SUPPRESS / WAIT Signal Codes + Position Sizing            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: PORTFOLIO MANAGEMENT & EXIT MONITORS                           │
│ Input: Open Positions in wealth_portfolio Postgres table                │
│ Monitors: 20% Hard Drawdown Stop, Trailing Stop Breach, Hold Score < 45 │
│ Tax Bonus: LTCG Bonus (+10 pts) applied in final 30 days of 1Y hold      │
│ Output: SELL / HOLD / SELL_REVIEW / TLH Signals                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ DASHBOARD EXPORT & PERSISTENCE PIPELINE                                 │
│ Target 1: PostgreSQL wealth_portfolio table                             │
│ Target 2: PostgreSQL parquet_cache table (name = 'wealth_engine')       │
│ Target 3: Disk File data/elite_wealth_system.parquet                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## 23.2 Batch Processing Contract & Worker Sizing
- **Batch Size Constant**: 50 symbols per fetch chunk. Hardcoded in `chunk_iterable(universe, batch_size=50)`.
- **Rationale**: Bounded RSS memory footprint during pandas rolling indicator calculations and optimized yfinance URL request string length.
- **Worker Threads**: `WORKER_COUNT = 3`. Hardcoded to 3 concurrent worker threads to comfortably operate within the container's 1.0 GB RAM budget during parallel rolling window computations.
- **Remainder Handling**: Last batch processes the remaining symbols ($294 \pmod{50} = 44$).
- **Parallelism Rule**: Batches execute sequentially within the Wealth Engine, but individual symbols within a batch are parsed concurrently via `ThreadPoolExecutor(max_workers=3)`.

## 23.3 Runtime Memory Lifecycle & Budget Resolution
- **Memory Checkpoints**: RSS memory monitored before fetch, after fetch, after candidate evaluation, and after portfolio export.
- **Garbage Collection & Trimming Protocol**:
  - `gc.collect()` executed explicitly after every scanner batch run.
  - `malloc_trim()` invoked post-batch on Linux runtimes to release un-mapped heap memory to the OS.
- **Surviving vs. Evicted Objects**:
  - **Surviving**: `data/watchlist.parquet` (Session tier), Postgres tables (`alerts`, `wealth_portfolio`).
  - **Evicted**: Ephemeral OHLCV dataframes (`ohlcv.clear()`) evicted immediately after scoring.
- **Memory Budget SLA & Transient Peaks**:
  - System Memory Budget: **2.0 GB RAM (2048 MB)**.
  - Baseline RSS: ~400–600 MB after initialization.
  - Warning / Eviction Threshold: **1200 MB** (60% of system limit).
  - Transient RSS peaks during bulk 300-symbol pandas rolling window calculations reach **1400–1600 MB** in process memory, operating safely within the 2.0 GB allocation before garbage collection.
  - Emergency GC / Kill Threshold: **1800 MB** (90% of system limit). Memory Profiler triggers emergency cache eviction and `gc.collect()` + `malloc_trim()` if RSS exceeds 1800 MB.

## 23.4 Inter-Scanner Lock Queueing & Scheduling Policy
- **Mutex Lock Hierarchy**: `scanner_execution_lock` (`InstrumentedLock`) + `ProcessLock("wealth_engine")`.
- **Scheduling Priority**:
  1. **Wealth Engine (Priority 1)**: Maximum execution SLA 180s.
  2. **Multi-TF Intraday Scanner (Priority 2)**: 15-minute market hours cadence.
  3. **Evening Batch Scanners (Priority 3)**: Post 18:00 IST sequential runs (`EOD` $\rightarrow$ `Reversal` $\rightarrow$ `Pullback`).
- **Queue Behavior**: Manual API triggers (`/api/trigger-scanner`) wait on `ProcessLock` with a 10s wait warning and a hard 60s acquire timeout. Queueing order is strictly **FIFO**. Read-only dashboard endpoints (`/api/wealth_data`, `/api/scanner_status`) DO NOT acquire execution locks and run concurrently with zero blocking.

## 23.5 Concall Transcripts & Sentiment Cache Specification
- **Storage Location**: PostgreSQL table `concall_cache` (scraped by `concall_scraper.py`).
- **Cache Schema**: `(symbol TEXT PRIMARY KEY, concall_summary JSONB, sentiment_score REAL, updated_at TIMESTAMPTZ)`.
- **TTL & Refresh**: 30-day TTL. Checked by `get_recent_concall_analysis()` during Layer 1 scoring to inject management sentiment bonus (+5 pts) into candidate ranking.

## 23.6 Market Regime Calculation Mathematics
- **Nifty 50 6-Month Return Formula**:
  $$\text{Nifty}_{6M} = \frac{C_{\text{latest}} - C_{t-126}}{C_{t-126}} \times 100$$
  where $C_{t-126}$ is the closing price 126 trading days ($\approx 6$ calendar months) prior.
- **Regime Classification Thresholds**:
  - `BULL`: $\text{Nifty}_{6M} \ge +5.0\%$
  - `BEAR`: $\text{Nifty}_{6M} \le -5.0\%$
  - `NEUTRAL`: $-5.0\% < \text{Nifty}_{6M} < +5.0\%$
- **Cache TTL**: 5 minutes (`MACRO_CACHE_TTL_SECONDS = 300`). Shared via `SessionContext.market_regime_manager`.

## 23.7 Data Freshness, Stale vs. Missing & Data Quality Math
- **Data Status Definitions**:
  - **Fresh Data**: OHLCV candle timestamp is within 24 hours (1D) or active 15m session bar.
  - **Stale Data**: Candle exists in cache but timestamp is older than TTL or marked `is_stale = True` (e.g., post-market close fallback).
  - **Missing Data**: Data provider returns `ProviderResult.NOT_FOUND` or empty dataframe.
- **Data Quality Score (0–100 Pts)**:
  $$\text{Quality Score} = 0.40 \times \text{Completeness} + 0.20 \times \text{NonMissing} + 0.20 \times \text{PriceSanity} + 0.10 \times \text{Continuity} + 0.10 \times \text{Freshness}$$
- **Health Classification**:
  - `OK`: Quality Score $\ge 90\%$
  - `WARNING`: $75\% \le \text{Quality Score} < 90\%$
  - `ERROR`: Quality Score $< 75\%$
  - `FAILED`: Uncaught data acquisition crash

## 23.8 Database Idempotency & Position Deduplication
- **Unique Constraint Index**: `alerts_dedup_idx UNIQUE (symbol, breakout_type, scanner, alert_date)`.
- **Runtime Bypass Logic**: If a symbol is already present in `wealth_portfolio` or has an active `OPEN` alert triggered today for the same scanner, `generate_entry_signal()` returns `Signal_Code = "HOLD"`, `Signal_Reason = "Position Already Open"`, skipping duplicate alert persistence.

## 23.9 Telemetry, Performance SLAs & Data Reconciliation
- **Mandatory Funnel Telemetry Fields**: `scanner`, `run_date`, `symbol`, `stage`, `gate`, `passed`, `observed_value`, `threshold_value`, `message`. Stored in `funnel_telemetry` table.
- **Symbol Count Reconciliation**:
  - `universe_count`: 287 canonical equity symbols in `watchlist.parquet`.
  - `requested_count`: 294 symbols (includes 287 watchlist symbols PLUS index benchmarks `^NSEI`, `^NSEBANK`, and 5 BSE fallback candidates).

## 23.10 Wealth Engine Rejection Taxonomy Catalog

| Rejection Code | Meaning / Cause | Severity | Retryability | User Dashboard Visibility |
| :--- | :--- | :--- | :--- | :--- |
| `WEALTH_REJ_001` | Daily Liquidity $< \text{₹}10\text{M}$ | `WARNING` | Retryable next session | Hidden from Buy Signals |
| `WEALTH_REJ_002` | `FM_Score` $< 55$ quality gate | `INFO` | Retryable after 1Y earnings | Visible as `WAIT` |
| `WEALTH_REJ_003` | Price below 200 SMA (`Close < SMA_200`) | `INFO` | Retryable on trend cross | Visible as `WAIT` |
| `WEALTH_REJ_004` | Sector Cap Exceeded ($> 25\%$ portfolio) | `INFO` | Retryable on rebalance | Visible as `Ranked Out` |
| `WEALTH_REJ_005` | Industry Cap Exceeded ($> 2$ stocks) | `INFO` | Retryable on rebalance | Visible as `Ranked Out` |
| `WEALTH_REJ_006` | Stale Data Flagged (`is_stale = True`) | `WARNING` | Retryable on provider fetch | Suppressed (Fake Buy Shield) |
| `WEALTH_REJ_007` | Incomplete Fundamentals / Technicals | `WARNING` | Retryable post-01:00 IST | Visible as `Incomplete` |
| `WEALTH_REJ_008` | 20% Hard Drawdown Stop Breach | `CRITICAL` | Non-retryable (Hard Sell) | Instant `SELL REVIEW` Alert |
| `DB_IDEMPOTENCY` | Active position already open in DB | `INFO` | Non-retryable (Deduplicated) | Visible as `HOLD` |

## 23.11 Complete Runtime Component Performance SLAs

| Subsystem Component | Target SLA (Expected) | Warning Threshold | Failure / Timeout |
| :--- | :--- | :--- | :--- |
| **Phase A Bulk Data Fetch** | $\le 15.0\text{s}$ | $> 30.0\text{s}$ | $> 60.0\text{s}$ |
| **Layer 1 Candidate Selection** | $\le 5.0\text{s}$ | $> 10.0\text{s}$ | $> 20.0\text{s}$ |
| **Layer 2 Entry Timing** | $\le 2.0\text{s}$ | $> 5.0\text{s}$ | $> 10.0\text{s}$ |
| **Layer 3 Portfolio Management** | $\le 30.0\text{s}$ | $> 45.0\text{s}$ | $> 90.0\text{s}$ |
| **Entire Wealth Engine SLA** | $\le 60.0\text{s}$ | $> 120.0\text{s}$ | $> 180.0\text{s}$ |
| **EOD Scanner SLA** | $\le 12.0\text{s}$ | $> 30.0\text{s}$ | $> 600.0\text{s}$ (10m hard cap) |
| **Multi-TF Intraday Scanner SLA** | $\le 8.0\text{s}$ | $> 20.0\text{s}$ | $> 60.0\text{s}$ |

## 23.12 Cache Invalidation & Ingestion Lifecycle Sequence

```text
 1. FETCH   ──> PriceCache fetches OHLCV batch (RAM allocation)
 2. INDIC   ──> IndicatorManager attaches EMA/SMA/ATR/RSI vectors
 3. SCORE   ──> ScoringEngine computes 0-100 score + trade targets
 4. PERSIST ──> AlertService inserts signals into PostgreSQL alerts table
 5. EXPORT  ──> DashboardServer updates wealth_portfolio & parquet_cache tables
 6. RELEASE ──> Call ohlcv.clear() & delete transient DataFrames
 7. PURGE   ──> Trigger explicit gc.collect() & malloc_trim(0) heap release
```

## 23.13 Failure Recovery Checkpoints & Railway Reboot Semantics
- **Railway Container Reboot**: On container restart at 11:40 AM, process boots up, initializes `ApplicationContext`, reads open positions from PostgreSQL `wealth_portfolio` table, and restores candidate state machine without losing historical trade logs.
- **Phase Failure Isolation**: If Phase A (bulk fetch) fails due to a network outage, the Wealth Engine aborts the current run, logs `ProviderResult.NETWORK_ERROR` to `scanner_health`, and retains the existing `data/elite_wealth_system.parquet` file on disk for dashboard serving until the next 5-minute tick retry.

## 23.14 Canonical Log Prefix Taxonomy

| Log Category | Standardized Prefix | Mandatory Log Fields | Example Log Message |
| :--- | :--- | :--- | :--- |
| **System Info** | `[INFO]` | Component, action, timestamp | `[INFO] [SCHEDULER] Session rotated to 2026-07-25` |
| **Warning** | `[WARN]` | Component, condition, impact | `[WARN] [PRICE_PROVIDER] YFinance retry 2/3 for RELIANCE` |
| **Error** | `[ERROR]` | Component, exception, stacktrace | `[ERROR] [DATABASE] DB pool acquisition timeout` |
| **Memory** | `[MEMORY]` | Current RSS, delta MB, peak RSS | `[MEMORY] Post-scan cleanup: RSS 412 MB (Delta: -380 MB)` |
| **Telemetry** | `[TELEMETRY]` | Scanner, stage, gate, passed | `[TELEMETRY] [EOD] Passed 42/287 candidates` |
| **Lock** | `[LOCK]` | Lock name, acquire wait sec | `[LOCK] ProcessLock("wealth_engine") acquired in 0.12s` |
| **Fetch** | `[FETCH]` | Provider, ticker count, duration | `[FETCH] [FYERS] Downloaded 50 tickers in 1.4s` |
| **Database** | `[DB]` | Operation, table, rows affected | `[DB] Upserted 10 alerts into alerts table` |
| **Cache** | `[CACHE]` | Cache tier, action, symbol | `[CACHE] Evicted EPHEMERAL OHLCV cache for RELIANCE` |

## 23.15 Resolution of Memory Target Allocation vs. Un-trimmed RSS Peak
- **Architectural Clarification**:
  - **1.0 GB RAM Minimum Container Budget**: The official system memory requirement configured for production deployment environments.
  - **888 MB RSS Peak**: The un-trimmed C-heap RSS memory footprint observed inside Python's memory allocator during multi-threaded bulk 300-symbol pandas rolling window calculations, operating safely within the 1.0 GB RAM container budget.
  - **Heap Trimming Mechanism**: Python's default memory allocator (`pymalloc`) holds allocated glibc memory arenas in RSS even after Python objects are destroyed. Calling `gc.collect()` followed by `malloc_trim(0)` forces glibc to return unused heap arenas to the Linux OS, dropping active RSS back below **400 MB** before starting the next processing phase.

## 23.16 Wealth Engine Phase Contract Matrix

| Phase Name | Exact Input | Exact Output | Caches Released | DB Writes | Failure Policy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase A: Bulk Fetch** | `watchlist.parquet` (287 symbols) + `^NSEI` | Raw OHLCV Cache (`Dict[str, DataFrame]`) | None | None | Abort run, retain previous parquet file on disk |
| **Layer 1: Candidate Selection**| Raw OHLCV + Watchlist Fundamentals | Evaluated candidate DataFrame (`FM_Score`) | None | None | Skip bad symbol, log warning to `scan_failures` |
| **Layer 2: Entry Timing** | Evaluated Candidate DataFrame | `BUY`/`SUPPRESS`/`WAIT` Signal Codes | Transient indicator DataFrames | None | Skip failed symbol calculation |
| **Layer 3: Portfolio Mgmt** | Open positions in `wealth_portfolio` table | `SELL`/`HOLD`/`SELL_REVIEW` Signals | Evict EPHEMERAL price cache (`ohlcv.clear()`) | Upsert `wealth_portfolio` table | Log DB error, retry connection after 2s |
| **Dashboard Export** | Portfolio DataFrame | `elite_wealth_system.parquet` | None | Write `parquet_cache` table (`name='wealth_engine'`) | Retain disk backup on DB write fail |

## 23.17 Restart & Mid-Run Failure Checkpoint Semantics
- **Atomic Phase Recovery**: The Wealth Engine is designed around **atomic, idempotent execution**. No partial state is stored in memory between phases.
- **Mid-Run Process Crash**: If the Python process terminates during Layer 1 or Layer 2, no corrupt partial signals are saved to PostgreSQL.
- **Boot Sequence Restore**: Upon process restart (e.g. Railway container reboot at 11:40 AM):
  1. Boot sequence initializes `ApplicationContext`.
  2. Reads existing open positions from `wealth_portfolio` PostgreSQL table.
  3. Loads `data/watchlist.parquet` from disk.
  4. Triggers clean Phase A fetch from beginning on next scheduled tick.

## 23.18 Ephemeral Cache Lifecycle & Invalidation Timeline

```text
Time (t)   Event / Lifecycle Stage                            Memory / Cache Action
─────────────────────────────────────────────────────────────────────────────────────────────
t + 0.0s   CREATE      PriceCache allocates dict space          Allocates dict
t + 1.5s   POPULATE    Downloads 50-symbol OHLCV batch          Fills EPHEMERAL RAM cache
t + 3.0s   CONSUMERS   ScoringEngine computes indicators/scores Calculates rolling windows
t + 4.5s   PERSIST     AlertService inserts signals into DB     Persists DB records
t + 5.0s   RELEASE     Call ohlcv.clear() on dictionary         Evicts DataFrame objects
t + 5.2s   DESTROY     Invoke gc.collect() & malloc_trim(0)     Reclaims C-heap to OS (<400MB)
```

## 23.19 Canonical Telemetry Telemetry JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FunnelTelemetryRecord",
  "type": "object",
  "properties": {
    "scan_id": { "type": "string" },
    "scanner": { "type": "string", "enum": ["EOD", "REVERSAL", "MULTI_TF", "WEALTH", "PULLBACK", "MULTIBAGGER"] },
    "run_date": { "type": "string", "format": "date" },
    "symbol": { "type": "string" },
    "stage": { "type": "string" },
    "gate": { "type": "string" },
    "passed": { "type": "boolean" },
    "observed_value": { "type": ["number", "null"] },
    "threshold_value": { "type": ["number", "null"] },
    "comparator": { "type": ["string", "null"] },
    "message": { "type": ["string", "null"] },
    "created_at": { "type": "string", "format": "date-time" }
  },
  "required": ["scan_id", "scanner", "run_date", "symbol", "stage", "gate", "passed"]
}
```

---

# 21. ANALYSE YOUR WATCHLIST DIAGNOSTIC ARCHITECTURE & REPOSITORY

The platform provides an on-demand stock analysis engine (`app/stock_analyzer.py`), REST API layer (`app/dashboard_server.py`), and PostgreSQL repository (`app/database.py`).

## 21.1 Core Architecture Components
1. **Real-time Autocomplete Engine (`search_symbols_autocomplete`)**:
   - Performs dual-tier lookup across `watchlist_cache` and `symbol_mappings` table.
   - Matches symbol prefix, company name substring, and produces custom fallback entries.
2. **7-Stage Dry-Run Funnel (`analyze_symbol`)**:
   - Runs dry-run evaluation through 7 scanner stages: *Daily Builder $\rightarrow$ EOD Breakout $\rightarrow$ Multi-TF Intraday $\rightarrow$ Reversal $\rightarrow$ Pullback $\rightarrow$ Wealth Engine $\rightarrow$ Multibagger Engine*.
   - Calculates Overall Health Score (0-100) combining Technical Trend (50%), Fundamental Quality (30%), and RS Percentile (20%).
   - Generates Deficit Summary list ("What This Stock Lacks Right Now").
3. **Manual Alert Promotion Engine (`create_manual_alert_from_analysis`)**:
   - Calculates dynamic SL, T1, T2, T3 targets via `compute_sl_and_target()`.
   - Saves alert to `alerts` table (`is_manual=True`) and dispatches Telegram notifications.

## 21.2 Personal Watchlist Schema (`user_watchlists`)
```sql
CREATE TABLE IF NOT EXISTS user_watchlists (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL DEFAULT 'DEFAULT_USER',
    symbol VARCHAR(32) NOT NULL,
    company_name VARCHAR(255),
    added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_health_score NUMERIC(5,2),
    last_status VARCHAR(32) DEFAULT 'MONITORING',
    notes TEXT,
    UNIQUE(user_id, symbol)
);
```

---
*End of Complete Technical Architecture & Zero-Code Reconstruction Specification — `docs/SYSTEM_ARCHITECTURE.md`*
