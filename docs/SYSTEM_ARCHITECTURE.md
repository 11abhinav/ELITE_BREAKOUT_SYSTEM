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

---

# 1. ARCHITECTURAL PHILOSOPHY & SYSTEM RUNTIME MODEL

## 1.1 Process Architecture & Deployment Budget
- **Runtime Environment**: Single Python 3.9 process running inside a Linux/Railway container.
- **Resource Budget**: Strictly bounded at **1.0 GB RAM (1024 MB)** (Minimum System Memory Requirement).
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

### 4. `ohlcv_5m` (Intraday 5-Minute Dataframe)
| Column Name | Type | Nullable | Meaning / Units |
| :--- | :--- | :--- | :--- |
| `Open` | `float` | No | Bar Opening Price (₹) |
| `High` | `float` | No | Bar Session High Price (₹) |
| `Low` | `float` | No | Bar Session Low Price (₹) |
| `Close` | `float` | No | Bar Session Closing Price (₹) |
| `Volume` | `float` | No | Total Traded Volume (Shares) |
| `VWAP` | `float` | No | Intraday Volume-Weighted Average Price (₹) |
| `EMA_20` | `float` | No | 20-period Exponential Moving Average |

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

## 6.1 Centralized Composite Scoring Engine (`app/scoring_engine.py`)

```python
def calculate_score(symbol: str, df: pd.DataFrame, regime_ctx: dict) -> int:
    latest = df.iloc[-1]
    
    # 1. Base Score Allocation (Max 30 pts)
    category = get_category_tier(symbol)
    base_points = {
        "DEBT_FREE_CASH": 30, "TOP_BANK": 30, "WEALTH_COMPOUNDER": 25,
        "BLUE_CHIP": 20, "MIDCAP_GROWTH": 18, "RECOVERY_PLAY": 8
    }.get(category, 15)

    # 2. Candle Quality (Max 15 pts)
    range_high_low = latest["High"] - latest["Low"]
    body_ratio = abs(latest["Close"] - latest["Open"]) / range_high_low if range_high_low > 0 else 0
    close_pos = (latest["Close"] - latest["Low"]) / range_high_low if range_high_low > 0 else 0
    upper_wick = (latest["High"] - latest["Close"]) / range_high_low if range_high_low > 0 else 0
    
    candle_score = 0
    if body_ratio >= config.EOD_CONFIG["MIN_BODY_RATIO"]: candle_score += 5
    if close_pos >= config.EOD_CONFIG["MIN_CLOSE_POSITION"]: candle_score += 5
    if upper_wick <= config.EOD_CONFIG["MAX_UPPER_WICK"]: candle_score += 5

    # 3. Volume Expansion (Max 20 pts)
    vol_avg = df["Volume"].iloc[-21:-1].mean()
    vol_ratio = latest["Volume"] / vol_avg if vol_avg > 0 else 1.0
    vol_score = 20 if vol_ratio >= 4.0 else (15 if vol_ratio >= 3.0 else (12 if vol_ratio >= 2.5 else (7 if vol_ratio >= 2.0 else 3)))

    # 4. Trend & Indicators (Max 15 pts)
    trend_score = 0
    if latest["Close"] > latest["EMA_20"]: trend_score += 3
    if latest["Close"] > latest["SMA_50"]: trend_score += 3
    if latest["SMA_50"] > latest["SMA_200"]: trend_score += 4
    if latest["ADX_14"] >= config.ADX_MIN_THRESHOLD: trend_score += 5

    # 5. RSI Location (Max 10 pts)
    rsi = latest["RSI_14"]
    rsi_score = 10 if 55 <= rsi <= 68 else (5 if (50 <= rsi < 55 or 68 < rsi <= 75) else 0)

    # 6. Regime & Momentum Bonuses ($S_{Regime}$)
    rs_percentile = macro_utils.get_stock_rs_percentile(symbol)
    rs_bonus = config.RS_BONUS if rs_percentile >= 80.0 else 0
    sector_name = macro_utils.get_symbol_sector(symbol)
    sector_bonus = config.SECTOR_BONUS if macro_utils.is_top_3_sector(sector_name) else 0
    regime_score = min(config.MAX_MOMENTUM_BONUS, rs_bonus + sector_bonus)

    # 7. Penalties ($P_{Penalties}$)
    penalties = 0
    prior_high = df["High"].iloc[-21:-1].max()
    atr = latest["ATR_20"]
    if latest["Close"] > prior_high + (config.EOD_ADVANCED_CONFIG["MAX_EXTENDED_BREAKOUT_ATR_MULT"] * atr):
        penalties += 10
    if df["OBV"].iloc[-1] <= df["OBV"].iloc[-5]:
        penalties += 5

    raw_score = base_points + candle_score + vol_score + trend_score + rsi_score + regime_score - penalties
    return max(0, min(100, raw_score))
```

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
            if df is None or len(df) < 200: continue
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

    approved_candidates.sort(key=lambda x: x["score"], reverse=True)
    top_10 = approved_candidates[:config.SCANNER_MAX_ALERTS["EOD"]]
    saved_count = save_alert_batch(top_10)
    upsert_scanner_health("EOD", status="OK", alerts=len(top_10), duration=time.time() - start_time)
    gc.collect()
    return len(top_10)
```

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

# 11. DATABASE ARCHITECTURE, OPERATIONAL BEHAVIOR & COMPLETE POSTGRESQL DDLS

## 11.1 Operational Database Behavior & Retention Rules
- **Immutable Columns**: In `alerts` table, `id`, `symbol`, `breakout_type`, `scanner`, `alert_time`, `entry_price`, `initial_stop_loss`, and `alert_date` are IMMUTABLE once inserted.
- **Mutable Tracking Columns**: `stop_loss`, `status`, `target_1`..`target_4`, and `exit_reason` are updated dynamically as trailing stops adjust or targets hit.
- **UPSERT Logic**: `INSERT INTO alerts (...) VALUES (...) ON CONFLICT (symbol, breakout_type, scanner, alert_date) DO UPDATE SET status = EXCLUDED.status, stop_loss = EXCLUDED.stop_loss`.
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

-- 5. Parquet Cache Table
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

-- 9. User Sessions Table
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- 10. Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 11. Pledge Cache Table
CREATE TABLE IF NOT EXISTS pledge_cache (
    symbol TEXT PRIMARY KEY,
    pledge_pct REAL NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 12. Scan Failures Table
CREATE TABLE IF NOT EXISTS scan_failures (
    id SERIAL PRIMARY KEY,
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    result TEXT NOT NULL,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
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
        
        # Midnight Rotation at 00:00 IST
        if now.hour == 0 and now.minute == 0:
            session_context.rotate_session()
            gc.collect()
            time.sleep(60)
            
        # Daily Builder at 01:00 IST
        elif now.hour == 1 and now.minute == 0:
            daily_builder.build_daily_watchlist()
            time.sleep(60)
            
        # Market Hours Intraday Loop (09:15 - 15:30 IST)
        elif is_market_open(now):
            if now.minute % 5 == 0:
                safe_run_wealth_market_hours(cmp_only=True)
                performance_tracker.update_positions()
                
            if now.minute % 15 in (0, 15, 30, 45):
                run_multi_tf_scanner()
                safe_run_wealth_market_hours(cmp_only=False)
                multibagger.monitor_exits()
                
            time.sleep(30)
            
        # Evening Scanners Batch post-18:00 IST after Bhavcopy
        elif now.hour >= 18 and not evening_batch_ran_today():
            if delivery_data.is_bhavcopy_available():
                _run_eod_with_retries(force=True)
                _run_reversal_with_retries(force=True)
                _run_pullback_with_retries(force=True)
                
            time.sleep(300)
            
        else:
            time.sleep(15)
```

---

# 14. ALERT LIFECYCLE, STATE MACHINE & COOLDOWN RULES

## 14.1 Alert Status Lifecycle State Machine
- `OPEN`: Signal triggered, entry active.
- `PARTIAL_WIN_1`: Target 1 ($1.5 R$) hit. Stop loss trailed to **Breakeven (Entry Price)**.
- `PARTIAL_WIN_2`: Target 2 ($2.5 R$) hit. Stop loss trailed to **Target 1 Price**.
- `WIN`: Target 3 ($4.0 R$) or Target 4 ($6.0 R$) hit.
- `TRAILING`: Active stop loss trailing above entry price following EMA9/swing low.
- `LOSS`: Closing price dropped below active `stop_loss`.
- `EXPIRED`: Signal failed to reach T1 within 20 trading days.
- `NEUTRAL`: Position closed at breakeven.

## 14.2 Candidate State Machine & Cascade Transitions

```text
[ IDLE ] ──(1H Trend Pass)──> [ HOURLY_PASSED ] ──(30m Hold)──> [ SETUP_ARMED ]
                                                                       │
                                                            (15m 20D High Breakout)
                                                                       │
                                                                       ▼
[ COOLDOWN ] <──(Exit Hit)── [ TRADE_ACTIVE ] <──(5m VWAP)── [ ENTRY_READY ]
```

- `IDLE` $\rightarrow$ `HOURLY_PASSED`: Triggered when 1H EMA9 > EMA20 > EMA50 and Close > SMA200.
- `HOURLY_PASSED` $\rightarrow$ `SETUP_ARMED`: Triggered when 30m Close holds above EMA20.
- `SETUP_ARMED` $\rightarrow$ `ENTRY_READY`: Triggered when 15m Close breaks prior 20-bar 15m High.
- `ENTRY_READY` $\rightarrow$ `TRADE_ACTIVE`: Triggered when 5m execution criteria pass (Close $\ge$ VWAP, R:R $\ge 1.5$).
- `TRADE_ACTIVE` $\rightarrow$ `COOLDOWN`: Triggered on stop loss breach, target hit, or 20-day expiry.

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
  - *Description*: Computes centralized 0–100 composite quality and breakout score.
  - *Arguments*: `symbol` (ticker string), `df` (OHLCV daily dataframe), `regime_ctx` (market regime context dict).
  - *Returns*: Integer score clamped between 0 and 100.
  - *Caller Permissions*: EOD Scanner, Multi-TF Scanner, Reversal Scanner, Pullback Pipeline.

### 3. `SLTargetHelper` (`app/sl_target_helper.py`)
- `compute_sl_and_target(df: pd.DataFrame, mode: str) -> dict`
  - *Description*: Computes dynamic stop loss, anti-trap buffer, target laddering (T1..T4), and R:R ratio.
  - *Arguments*: `df` (OHLCV dataframe), `mode` (`"EOD"`, `"REVERSAL"`, `"MULTI_TF"`, `"PULLBACK"`).
  - *Returns*: Dict containing `stop_loss`, `target_1`..`target_4`, `rr_ratio`, `sl_method`, `is_valid`.
  - *Caller Permissions*: All scanners.

---

# 18. VERBATIM PRODUCTION CONFIGURATION (`app/config.py`)

Below is the verbatim source code of `app/config.py`:

```python
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

_thread_eod = os.getenv("THREAD_EOD")
_thread_multi_tf = os.getenv("THREAD_MULTI_TF")
_thread_1h = os.getenv("THREAD_1H")
_thread_reversal = os.getenv("THREAD_REVERSAL")

THREAD_EOD = int(_thread_eod) if _thread_eod else None
THREAD_MULTI_TF = int(_thread_multi_tf) if _thread_multi_tf else None
THREAD_1H = int(_thread_1h) if _thread_1h else None
THREAD_REVERSAL = int(_thread_reversal) if _thread_reversal else None

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_PATH = os.path.join(DATA_DIR, "elite_fundamental_watchlist.parquet")
DB_PATH = os.path.join(DATA_DIR, "alerts.db")

MEMORY_PROFILER_CONFIG = {
    "DEEP_DIAGNOSTIC_RSS_MB": 5.0,
    "MIN_DF_DELTA_MB": 1.0,
    "MAX_TRACEMALLOC_PEAK_MB": 20.0,
    "CONSECUTIVE_TRIGGER_COUNT": 3,
    "RATE_LIMIT_MINUTES": 30
}

DISABLE_NSE_SURVEILLANCE_FETCH = False
ENABLE_AI_SENTIMENT_SCORE = True

SCORE_THRESHOLDS = {
    "15m": 78,
    "1h": 80,
    "1d": 82,
}

ACTIVE_ALGO_VERSION = "SL_ENGINE_V7.1"

RS_BONUS = 10
SECTOR_BONUS = 8
MAX_MOMENTUM_BONUS = 15

MULTI_TF_CONFIG = {
    "MIN_SIGNALS": 2,
    "MIN_BODY_RATIO": 0.60,
    "MIN_CLOSE_POSITION": 0.70,
    "MAX_UPPER_WICK": 0.20,
    "MIN_VOLUME_RATIO": 2.5,
    "MIN_VOLUME_AVG": 150_000,
    "MIN_RSI": 52,
    "MAX_RSI": 87,
    "PULLBACK_TRIGGER_MODE": "PREVIOUS_HIGH",
}

LIVE_1H_CONFIG = {
    "MIN_SIGNALS": 3,
    "MIN_BODY_RATIO": 0.55,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK": 0.25,
    "MIN_VOLUME_RATIO": 2.0,
    "MIN_VOLUME_AVG": 100_000,
    "MIN_RSI": 55,
    "MAX_RSI": 86,
}

EOD_CONFIG = {
    "MIN_SIGNALS": 1,
    "MIN_BODY_RATIO": 0.45,
    "MIN_CLOSE_POSITION": 0.65,
    "MAX_UPPER_WICK": 0.35,
    "MIN_VOLUME_RATIO": 1.8,
    "MIN_VOLUME_AVG": 50_000,
    "MIN_RSI": 55,
    "MAX_RSI": 88,
}

EOD_ADVANCED_CONFIG = {
    "MAX_DISTANCE_FROM_52W_HIGH_PCT": 15.0,
    "MAX_SINGLE_DAY_MOVE_PCT": 15.0,
    "MAX_GAP_FROM_PRIOR_HIGH_PCT": 3.0,
    "GAP_LOOKBACK_BARS": 10,
    "MAX_EXTENDED_BREAKOUT_ATR_MULT": 1.5,
    "GAP_AND_GO_PENALTY_MULT": 10,
    "GAP_AND_GO_MAX_PENALTY": 20,
    "MIN_ATR_EXPANSION_RATIO": 0.9,
    "MIN_OBV_SLOPE": 0.0,
    "PRE_BREAKOUT_LOOKBACK_BARS": 5,
    "MAX_PRE_BREAKOUT_RED_CANDLES": 2,
    "TIGHT_BASE_BB_WIDTH_PCTILE": 0.35,
    "MAX_BB_WIDTH_PCTILE": 0.80
}

REVERSAL_CONFIG = {
    "MIN_DROP_FROM_52W_HIGH": 20.0,
    "MAX_DROP_FROM_52W_HIGH": 45.0,
    "RSI_OVERSOLD_THRESHOLD": 45,
    "RSI_CURL_MIN": 50,
    "MIN_VOLUME_RATIO": 2.0,
    "MIN_AVG_DAILY_VOLUME": 300_000,
    "MIN_ROE": 12.0,
    "MIN_YOY_REVENUE_GROWTH": 8.0,
    "MAX_DROP_BELOW_SMA200": 25.0,
    "REVERSAL_COOLDOWN_TRADING_DAYS": 30
}

ALERT_COOLDOWN_MINUTES = {
    "WEALTH": 1440,
    "MULTI_TF": 720,
    "EOD": 1440,
    "REVERSAL": 10080,
    "PULLBACK": 10080,
    "MULTIBAGGER": 43200
}

SCANNER_MAX_ALERTS = {
    "WEALTH": 50,
    "MULTI_TF": 100,
    "EOD": 10,
    "REVERSAL": 10,
    "PULLBACK": 10,
    "MULTIBAGGER": 10,
}

MAX_SL_DISTANCE_PCT = 8.0
ACCOUNT_RISK_BUDGET_PCT = 1.0
MAX_POSITION_PCT = 0.25

PULLBACK_CONFIG = {
    "VERSION": "pb-1.0.0",
    "LOOKBACK": 10, "CONFIRM": 3,
    "MIN_IMPULSE_GAIN_PCT": 8.0, "MIN_IMPULSE_ATR": 3.0, "MAX_IMPULSE_BARS": 20,
    "MIN_DEPTH_PCT": 5.0, "MAX_DEPTH_PCT": 15.0,
    "MIN_DURATION": 3, "MAX_DURATION": 20,
    "MAX_INTERNAL_SWINGS": 2, "MAX_PB_VOLUME_RATIO": 0.75,
    "TRIGGER_VOL_MULT": 1.3, "MIN_CLOSE_LOCATION": 0.75,
    "MIN_BODY_ATR": 0.5, "MAX_UPPER_WICK": 0.25, "MAX_ENTRY_GAP_PCT": 3.0,
    "MAX_BONUS": 5, "PRIOR_WINDOW": 30,
    "OUTAGE_THRESHOLD_BUMP": 3,
    "MIN_HISTORY": 200,
    "MODE": "LIVE", "DEBUG_SWINGS": False,
}

QUALITY_VALIDATOR_VERSION = "V8.0"
QUALITY_SCORE_WEIGHTS = {
    "row_completeness": 40,
    "missing": 20,
    "price_sanity": 20,
    "continuity": 10,
    "freshness": 10,
}

SCORE_BANDS = [(70, 75), (75, 80), (80, 85), (85, 90), (90, 101)]
MAX_HISTORY_SHRINK = 0.30

SOURCE_RELIABILITY = {
    "NSE": 1.0,
    "Fyers": 1.0,
    "Cache": 0.95,
    "BSE": 0.70
}

ADX_MIN_THRESHOLD = 18
MIN_STOCK_PRICE = 100.0

MIN_DAILY_LIQUIDITY_RUPEES_WATCHLIST = 150_000_000
MIN_DAILY_LIQUIDITY_RUPEES_WEALTH = 10_000_000

DELIVERY_CONVICTION_THRESHOLDS = {
    "institutional": 60,
    "positional": 40,
    "moderate": 25,
    "intraday_churn": 0,
}

BATCH_DOWNLOAD_SIZE = 30
YAHOO_TIMEOUT = 30
PRICE_CACHE_TTL_SECONDS = 60

TELEGRAM_CHUNK_SIZE = 10
TELEGRAM_RETRIES = 3
TELEGRAM_TIMEOUT = 10
LOG_LEVEL = "INFO"

MIN_BREAKOUT_MARGIN = {"15m": 0.003, "1h": 0.005, "1d": 0.007}
MIN_BREAKOUT_VOLUME_RATIO = 1.5
BASE_TIGHTNESS_THRESHOLD = 1.5
BASE_VOLATILITY_THRESHOLD = 3.0

CLIMAX_VOLUME_LOOKBACK = 20
LOWER_HIGH_LOOKBACK = 6
MIN_CANDLE_RANGE_PCT = 0.003

ADAPTIVE_TARGET_CAPS = {
    "BULL": {"15m": 8.0, "1h": 10.0, "1d": 12.0},
    "BEAR": {"15m": 4.0, "1h": 6.0, "1d": 8.0},
    "NEUTRAL": {"15m": 6.0, "1h": 8.0, "1d": 10.0}
}

MIN_NATURAL_RR = {"MULTI_TF": 1.5, "EOD": 2.5, "REVERSAL": 2.0}
LOCK_WAIT_WARNING_SECONDS = float(os.environ.get("LOCK_WAIT_WARNING_SECONDS", "10.0"))
LOCK_HOLD_WARNING_SECONDS = float(os.environ.get("LOCK_HOLD_WARNING_SECONDS", "120.0"))

MAX_REASONABLE_RR = {"MULTI_TF": 6.0, "EOD": 8.0, "REVERSAL": 4.0}
MIN_TARGET_CONFIDENCE = 40

DATA_PROVIDER = os.getenv("DATA_PROVIDER", "auto")
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
    "yahoo": {"bulk": True, "live": False, "intraday": True, "historical": True},
    "fyers": {"bulk": False, "live": True, "intraday": True, "historical": True},
    "bse": {"bulk": True, "live": False, "intraday": False, "historical": True}
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

FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
FYERS_REDIRECT_URL = os.getenv("FYERS_REDIRECT_URL", "https://.../fyers/callback")
FYERS_TOKEN_PATH = os.path.join(DATA_DIR, "fyers_token.txt")

PARTIAL_EXIT = {"EOD": [40, 30, 30], "REVERSAL": [30, 30, 40]}
```

---

# 19. DETERMINISTIC RECONSTRUCTION ANSWERS (Q1 – Q36)

### Q1 – Q3: Production Config & Indicator Parameters
- **Exact Indicators Calculated**: EMA9, EMA20, EMA50, EMA200; SMA20, SMA50, SMA100, SMA200; ATR20 (Wilder smooth); ADX14 (Wilder smooth); RSI14 (Wilder smooth); MACD (12, 26, 9); OBV (On-Balance Volume); VWAP (intraday); BB Width (20-period, 2.0 std dev).

### Q4: Exact DataFrame Schemas
- **`watchlist`**: `["Stock", "Category", "sector", "ROCE %", "ROE %", "Debt/Equity", "YoY Revenue Growth %", "Pledge %", "Market Cap"]`
- **`ohlcv_daily`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "EMA_50", "SMA_50", "SMA_200", "ATR_20", "ADX_14", "RSI_14", "OBV"]`
- **`ohlcv_15m`**: `["Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_20", "VWAP", "RSI_14", "ATR_20"]`
- **`ohlcv_5m`**: `["Open", "High", "Low", "Close", "Volume", "VWAP", "EMA_20"]`

### Q5 – Q7: Risk Engine & Target Allocation
- **No Structural Stop Fallback**: If no swing low or pivot support is identified, stop loss defaults to $\text{Entry} - (2.0 \times \text{ATR}_{20})$.
- **Multiple Resistance Levels**: Targets selected by ascending order of resistance levels ($R_1 < R_2 < 52W\text{ High}$).
- **RR Acceptance**: A trade is accepted IF AND ONLY IF `natural_rr >= MIN_NATURAL_RR[mode]`.

### Q8 – Q10: Scanner Rejection & Exception Handling
- **Rejection Codes**: `EOD001` (Illiquid), `EOD002` (Candle quality failure), `REV004` (Fallen knife cooldown), `MTF013` (VWAP violation).
- **Exception Rule**: On symbol exception inside a chunk, log error to `scan_failures` table and CONTINUE to next symbol.

### Q11 – Q13: Provider Retries & Circuit Breaker
- Primary retries: 3 attempts with exponential backoff ($2^{\text{attempt}} \times 1\text{s}$). Provider circuit breaker stays OPEN for 300 seconds (5 mins) after 3 consecutive failures.

### Q14 – Q15: Scheduler & Overrun Policy
- Scanner overrun (>10m) triggers a process warning log and forces completion before starting next batch.
- On Railway restart at 11:40 AM, session re-attaches to active trading day state without clearing database tables.

### Q16 – Q18: Database Indexes & Pool Options
- **Indexes**: `CREATE INDEX idx_alerts_symbol ON alerts(symbol);`, `CREATE INDEX idx_alerts_date ON alerts(alert_date);`.
- **Pool Settings**: Min=5, Max=50 connections, 15s acquire timeout. Isolation level: `READ COMMITTED`.

### Q19 – Q22: Presentation, WebSockets & Notifications
- **WebSocket Protocol**: `{ "type": "ALERT_NEW", "payload": { "symbol": "RELIANCE", "scanner": "EOD", "entry": 2450.0 } }`.
- **Notification Order**: DB persistence FIRST $\rightarrow$ Telegram broadcast SECOND $\rightarrow$ Web Push THIRD.

### Q23 – Q26: Session Lifecycle & Memory Eviction
- **Midnight Rotation**: Clears ephemeral RAM caches, destroys `SessionContext`, and triggers `gc.collect()`.
- **Cache Eviction**: EPHEMERAL tier evicted when RSS exceeds 400 MB RAM (LRU policy).

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

### Q29 – Q36: System Execution & AI Reconstruction Sequence
- Complete step-by-step module dependency order: `config.py` $\rightarrow$ `core_enums.py` $\rightarrow$ `database.py` $\rightarrow$ `lock_utils.py` $\rightarrow$ `price_cache.py` $\rightarrow$ `indicator_manager.py` $\rightarrow$ `unified_fetcher.py` $\rightarrow$ `scoring_engine.py` $\rightarrow$ `scanners` $\rightarrow$ `main.py`.

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
    assert mem["rss_mb"] < 450.0, f"Memory threshold breached: {mem['rss_mb']} MB"
```

1. **Gate 1: Cold Start Import Speed**: Verify total import latency $\le 5.0\text{s}$.
2. **Gate 2: Unsupported Imports Audit**: Ensure zero forbidden external libraries (`scikit-learn`, `tensorflow`, `ta-lib`, etc.).
3. **Gate 3: Smoke Execution**: Run full scanner smoke test suite in $\le 30.0\text{s}$.
4. **Gate 4: AST Method Signature Reflection Audit**: Validate public function signatures across all 88 modules.
5. **Gate 5: Railway Integration Contract**: Verify environment variable resolution (`DATABASE_URL`, `PORT`).
6. **Gate 6: Production Readiness Checklist**: Verify RAM usage budget ($\text{RSS} < 450.0\text{ MB}$ with explicit `gc.collect()`).
7. **Gate 7: Dependency Reproducibility**: Ensure all requirements in `requirements.txt` are strictly pinned.
8. **Gate 8: Scheduler 24h Timeline Simulation**: Simulate 24-hour cycle execution without blocking threads.
9. **Gate 9: Memory Budget Assertions**: Verify thread pool count $< 60$ and peak RAM $< 450.0\text{ MB}$.
10. **Gate 10: Alert Contract Schema Compliance**: Ensure alert JSON payloads contain required keys (`symbol`, `entry_price`, `stop_loss`, `target_1`..`target_4`, `score`).
11. **Gate 11: Scanner Execution Invariants**: Enforce `entry_price > stop_loss` and `target_1 >= entry_price`.
12. **Gate 12: DB Connection Pool Timeout**: Verify database pool acquires connection within $\le 15.0\text{s}$.
13. **Gate 13: `/version` Endpoint Health**: Validate build metadata, git commit hash, and release gate status.
14. **Gate 14: Earnings Calendar Safety**: Ensure blackout dates are respected for corporate earnings releases.
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
    score = scoring_engine.calculate_score("RELIANCE", df, regime_ctx={"regime": "BULL"})
    sl_res = compute_sl_and_target(df, mode="EOD")
    
    # Expected Deterministic Output Values
    assert score == 86, f"Expected Score 86, got {score}"
    assert sl_res["stop_loss"] == 2410.50, f"Expected SL 2410.50, got {sl_res['stop_loss']}"
    assert sl_res["target_1"] == 2509.25, f"Expected T1 2509.25, got {sl_res['target_1']}"
    assert sl_res["rr_ratio"] >= 2.5, f"Expected R:R >= 2.5, got {sl_res['rr_ratio']}"
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
└── app/telemetry_manager.py   # (Timeline & funnel logging)

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
├── app/trade_ranking_engine.py# (Multi-factor candidate ranking)
├── app/macro_utils.py         # (Market regime engine & sector rankings)
├── app/strategy_policy.py     # (Regime-aware threshold modifiers)
├── app/forensic_engine.py     # (Forensic risk tiers & CFO/PAT gates)
└── app/quality_trajectory.py  # (Fundamentals trajectory score)

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
  - Minimum Container Requirement: **1.0 GB RAM (1024 MB)**.
  - Transient RSS peaks during bulk 300-symbol pandas rolling window calculations reach **800–888 MB** in process memory, operating safely within the 1.0 GB allocation before garbage collection.
  - **Mitigation Protocol**: Memory Profiler triggers emergency cache eviction and `gc.collect()` if RSS exceeds 900 MB for more than 3 consecutive 5-minute ticks.

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
- **Cache TTL**: 1 hour (`_NIFTY_CACHE_TTL = 3600`). Shared via `SessionContext.market_regime_manager`.

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
*End of Complete Technical Architecture & Zero-Code Reconstruction Specification — `docs/SYSTEM_ARCHITECTURE.md`*
