# ELITE BREAKOUT SYSTEM — COMPLETE TECHNICAL ARCHITECTURE & PRODUCTION SPECIFICATION

> **Document Class:** Developer, Architect & Quantitative Engineering Blueprint
> **Target Audience:** Systems Engineers, Quantitative Developers, AI Coding Models
> **Status:** Canonical Master Technical Architecture and Implementation Specification for the Active Production Codebase.
> **Target File:** `docs/SYSTEM_ARCHITECTURE.md`
> **Last Synchronized:** 2026-10-04 (Master Consolidation — Active Production Engines Only, Authoritative Outbox Pattern, Quality Data Availability Auditor, Broker Integrations, Corporate Action Event Framework)

---

## TABLE OF CONTENTS

1. [Architectural Philosophy & System Runtime Model](#1-architectural-philosophy--system-runtime-model)
2. [Ownership Matrix & Cache Topology](#2-ownership-matrix--cache-topology)
3. [Abstract Pipeline Architecture & Step Library](#3-abstract-pipeline-architecture--step-library)
4. [Context Model, Dataclasses & Canonical Dataframe Schemas](#4-context-model-dataclasses--canonical-dataframe-schemas)
5. [Core System Enums & Data Models](#5-core-system-enums--data-models)
6. [Quantitative Algorithms, Indicator Specifications & Scoring Engines](#6-quantitative-algorithms-indicator-specifications--scoring-engines)
7. [Active Production Scanner Execution Code Flows](#7-active-production-scanner-execution-code-flows)
8. [Fundamentals Data Pipeline & Point-in-Time Integrity](#8-fundamentals-data-pipeline--point-in-time-integrity)
9. [External Market Data Provider Routing & Broker Integrations](#9-external-market-data-provider-routing--broker-integrations)
10. [Quality Data Availability Auditor & Recovery-Diagnostics](#10-quality-data-availability-auditor--recovery-diagnostics)
11. [Authoritative Outbox Persistence & PostgreSQL Database Architecture](#11-authoritative-outbox-persistence--postgresql-database-architecture)
12. [Corporate Action Event Framework & Trading Calendar](#12-corporate-action-event-framework--trading-calendar)
13. [Concurrency, Synchronization & Lock Hierarchy](#13-concurrency-synchronization--lock-hierarchy)
14. [Autonomous Scheduler & 24/7 Execution Blueprint](#14-autonomous-scheduler--247-execution-blueprint)
15. [Alert Lifecycle, State Machine & Exit Management](#15-alert-lifecycle-state-machine--exit-management)
16. [Complete REST API Specifications & Streaming Protocols](#16-complete-rest-api-specifications--streaming-protocols)
17. [Exhaustive Repository Module Inventory & Interface Contracts](#17-exhaustive-repository-module-inventory--interface-contracts)
18. [UI/UX Specifications & Client-Side Architecture](#18-uiux-specifications--client-side-architecture)
19. [Production Configuration Reference (`app/config.py`)](#19-production-configuration-reference-appconfigpy)
20. [Deployment Verification, Failure Matrix & Golden Test Suites](#20-deployment-verification-failure-matrix--golden-test-suites)

---

# 1. ARCHITECTURAL PHILOSOPHY & SYSTEM RUNTIME MODEL

## 1.1 Process Architecture & Deployment Budget
- **Runtime Model**: ~~Single Python 3.9 process running inside a secure Linux/Railway container.~~ Single Python 3.9 process running inside a secure Contabo VPS + Coolify Docker container. *(Updated 2026-10-04: Enforced Rule 66 Contabo VPS + Coolify deployment architecture)*
- **Resource Budget**: **2.0 GB RAM (2048 MB)** Container Operating Budget (Minimum floor = **1.0 GB RAM**). Warning/eviction threshold = 1200 MB (60%), peak transient = 1400–1600 MB, emergency GC kill = 1800 MB (90%).
- **Process Isolation Directive**: Microservices are explicitly prohibited due to RAM duplication, inter-process serialization overhead, and latency. All subsystems run in-process using managed thread pools, shared memory structures, and non-blocking asynchronous loops.
- **System Invariants**:
  - **IST Timezone**: All timing, candle boundaries, trading schedules, and database timestamps MUST be evaluated in **IST (Asia/Kolkata - UTC+5:30)**.
  - **Rupee Currency**: All financial figures, stop losses, target gains, and portfolio CMPs MUST be denominated in **Indian Rupees (₹ / RS)**.
  - **Zero-Synthetic-Fallback**: Missing prices, fundamentals (ROCE, ROE, Sales CAGR, PAT CAGR, CFO/PAT, D/E), or valuation discounts MUST NEVER be populated with hardcoded synthetic values or dummy watchlists. Missing data fails closed (`DATA_INSUFFICIENT`).

## 1.2 Daily 24-Hour Lifecycle Timeline (`app/main.py`)
Background operations are governed by an autonomous 24/7 scheduler loop (`run_system_scheduler()`) executing the following timeline:

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
       │ DAILY WATCHLIST BUILDER                                    │
       │ Owner: WatchlistService (app/daily_builder.py)             │
       │ Input: TradingView & Exchange universe (~940+ equities)    │
       │ Output: data/watchlist.parquet + DB build_manifest         │
 01:45 └────────────────────────────────────────────────────────────┘
       │
 02:00 ┌────────────────────────────────────────────────────────────┐
       │ WEALTH ENGINE INITIAL SWEEP                                │
       │ Owner: WealthEngine (app/wealth_engine.py)                 │
       │ Input: Watchlist + 1Y Daily OHLCV + PIT Fundamentals       │
       │ Output: wealth_portfolio table + initial buy candidates    │
 02:30 └────────────────────────────────────────────────────────────┘
       │
 04:00 ┌────────────────────────────────────────────────────────────┐
       │ MULTIBAGGER ENGINE COLD START                              │
       │ Owner: Multibagger Engine (app/multibagger.py)             │
       │ Output: Initial conviction tier ranking (Prime & High Q)   │
 04:30 └────────────────────────────────────────────────────────────┘
       │
 07:00 ┌────────────────────────────────────────────────────────────┐
       │ MASTER SYMBOLS REGISTRY REFRESH                            │
       │ Owner: SymbolResolutionEngine (/api/v1/admin/master_symbols)│
       │ Action: Refresh master_symbols PostgreSQL table            │
 07:15 └────────────────────────────────────────────────────────────┘
       │
 08:30 ┌────────────────────────────────────────────────────────────┐
       │ READINESS VERIFICATION CHECK                               │
       │ Owner: Scheduler (app/main.py)                             │
       │ Action: Verify watchlist freshness & DB health             │
       │ Transition: SessionContext → READY                         │
 08:35 └────────────────────────────────────────────────────────────┘
       │
 09:14 ┌────────────────────────────────────────────────────────────┐
       │ PRE-MARKET WARMUP (09:14:30 IST)                           │
       │ Owner: Scheduler                                           │
       │ Action: Pre-fetch intraday data to prevent tick lag        │
 09:15 └────────────────────────────────────────────────────────────┘
       │
 09:15 ┌────────────────────────────────────────────────────────────┐
       │ MARKET OPEN (SessionContext → MARKET_OPEN)                 │
       │                                                            │
       │ ┌─────── MARKET HOURS INTRADAY LOOP (Locked) ──────────┐  │
       │ │ Every 5 min:                                         │  │
       │ │   → Wealth Engine Fast CMP Exit Updates (<3.0s)      │  │
       │ │   → Performance Tracker Position Updates             │  │
       │ │                                                      │  │
       │ │ Every 15 min (:00, :15, :30, :45):                   │  │
       │ │   → Technical Breakout Scanner (BULL regime)         │  │
       │ │   → Wealth Engine Full BUY Scan                      │  │
       │ │   → Multibagger Exit Monitor                         │  │
       │ │                                                      │  │
       │ │ Continuous:                                          │  │
       │ │   → Authoritative Outbox Parquet Reconciler          │  │
       │ └──────────────────────────────────────────────────────┘  │
 15:30 ── MARKET CLOSE (SessionContext → POST_MARKET)
       │
 15:30 ┌────────────────────────────────────────────────────────────┐
       │ POST-MARKET EARNINGS / RESULT CALENDAR REFRESH             │
       │ Owner: EarningsCalendarService (app/earnings_calendar.py)  │
       │ Window: 15:30 - 18:00 IST (Post-market close window)       │
       │ Priority 1: Stocks with results expected TODAY re-checked  │
       │ Priority 2: Rest of universe (45d TTL known, 7d TTL missing)│
 18:00 └────────────────────────────────────────────────────────────┘
       │
 19:00 ┌────────────────────────────────────────────────────────────┐
       │ MULTIBAGGER DAILY SCANNER RUN                              │
       │ Owner: Multibagger Engine (app/multibagger.py)             │
       │ Output: DB alerts + candidate ranking                      │
 19:30 └────────────────────────────────────────────────────────────┘
```

---

# 2. OWNERSHIP MATRIX & CACHE TOPOLOGY

Data flows through four strictly partitioned cache layers:

| Cache Component | File | Memory Tier | TTL / Lifecycle | Purpose |
|---|---|---|---|---|
| **`PriceCache`** | `app/price_cache.py` | RAM + Parquet | 1 Trading Day | Daily & Intraday OHLCV caching |
| **`WatchlistCache`** | `app/watchlist_cache.py` | RAM + Parquet | Daily (01:00 IST) | Approved trading universe |
| **`FundamentalsCache`** | `app/fundamentals_cache.py` | RAM + SQLite/Postgres | Quarter / Filing | Audited annual statements & ratios |
| **`CorporateEventCache`** | `app/corporate_events.py` | RAM | 1 Hour | Priority corporate event badges |

---

# 3. ABSTRACT PIPELINE ARCHITECTURE & STEP LIBRARY

All market scans execute through a standardized pipeline architecture (`app/pipeline_runner.py`):

```text
PipelineContext (symbol, universe, session, metadata)
       │
       ▼
┌──────────────────┐
│   PipelineStep   │─── executes step 1 (e.g. PriceFloorGate)
└────────┬─────────┘
         │  (returns StepResult.PASS, FAIL, or SKIP)
         ▼
┌──────────────────┐
│   PipelineStep   │─── executes step 2 (e.g. TrendAlignmentGate)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   PipelineStep   │─── executes step 3 (e.g. ScoringEngine)
└────────┬─────────┘
         │
         ▼
DecisionContext (approved candidates, rejection reasons, telemetry)
```

- **Fail-Fast Semantics**: Hard gates abort downstream evaluation immediately to conserve CPU and memory.
- **Near-Miss Telemetry**: Rejected symbols that cleared $\ge 80\%$ of gates are preserved in `near_miss_tracker.py` for parameter calibration.

---

# 4. CONTEXT MODEL, DATACLASSES & CANONICAL DATAFRAME SCHEMAS

## 4.1 Canonical OHLCV Schema
All market data providers must normalize data to the canonical OHLCV schema:

| Column | Type | Invariant | Description |
|---|---|---|---|
| `timestamp` | `datetime64[ns, Asia/Kolkata]` | Monotonic, Unique | Bar close timestamp in IST |
| `open` | `float64` | `> 0.0` | Open price in INR |
| `high` | `float64` | `>= max(open, close)` | Highest traded price |
| `low` | `float64` | `<= min(open, close)` | Lowest traded price |
| `close` | `float64` | `> 0.0` | Close price in INR |
| `volume` | `int64` | `>= 0` | Traded share volume |
| `open_interest` | `int64` | `>= 0` | Open interest (0 for cash equity) |

## 4.2 Canonical Fundamental Schema
| Field | Type | Unit | Invariant |
|---|---|---|---|
| `roce` | `float` | Percentage | $\ge 0$ |
| `roe` | `float` | Percentage | $\ge 0$ |
| `debt_to_equity` | `float` | Ratio | $\ge 0.0$ |
| `sales_cagr_3yr` | `float` | Percentage | Realized 3Y revenue growth |
| `pat_cagr_3yr` | `float` | Percentage | Realized 3Y profit growth |
| `cfo_to_pat` | `float` | Ratio | Operating cash flow / PAT |
| `peg_ratio` | `float` | Ratio | Bubble ceiling $\le 3.0$ |
| `promoter_pledge` | `float` | Percentage | Strict ceiling $\le 15.0\%$ |

---

# 5. CORE SYSTEM ENUMS & DATA MODELS

Defined in `app/core_enums.py` and `app/core_models.py`:

```python
class MarketRegime(str, Enum):
    BULL = "BULL"
    SIDEWAYS = "SIDEWAYS"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"

class ScannerType(str, Enum):
    DAILY_BUILDER = "DAILY_BUILDER"
    TECHNICAL = "TECHNICAL"
    WEALTH = "WEALTH"
    MULTIBAGGER = "MULTIBAGGER"
    QUALITY_VALUE_RECOVERY = "QUALITY_VALUE_RECOVERY"

class AlertStatus(str, Enum):
    OPEN = "OPEN"
    TRAILING = "TRAILING"
    WIN = "WIN"
    PARTIAL_WIN = "PARTIAL_WIN"
    LOSS = "LOSS"
    EXPIRED = "EXPIRED"

class ConvictionTier(str, Enum):
    PRIME = "PRIME"
    HIGH_QUALITY = "HIGH_QUALITY"
    STANDARD = "STANDARD"
```

---

# 6. QUANTITATIVE ALGORITHMS, INDICATOR SPECIFICATIONS & SCORING ENGINES

## 6.1 Composite Quality Scoring Engine (`app/scoring_engine.py`)
Computes an objective 0–100+ composite score:

1. **Technical Base & Breakout Profile (Max 37 pts)**:
   - Candle body ratio $\ge 45\%$: $+10\text{ pts}$
   - Close position $\ge 65\%$ of range: $+10\text{ pts}$
   - Upper wick $\le 35\%$: $+6\text{ pts}$
   - 20-Day high breakout: $+6\text{ pts}$
   - 50-Day high breakout bonus: $+5\text{ pts}$
2. **Volume & Institutional Footprint (Max 20 pts)**:
   - Volume surge $\ge 1.8\text{x}$ 20D median: $+10\text{ pts}$
   - Volume surge $\ge 2.5\text{x}$ 20D median: $+15\text{ pts}$
   - Delivery percentage $\ge 40\%$: $+5\text{ pts}$
3. **Trend Stack & Momentum (Max 31 pts)**:
   - Moving Average alignment ($\text{Close} > \text{EMA}_{20} > \text{SMA}_{50} > \text{SMA}_{200}$): $+16\text{ pts}$
   - RSI Momentum ($55 \le \text{RSI} \le 72$): $+10\text{ pts}$
   - Relative Strength vs Nifty ($RS_{6m} > 0$): $+5\text{ pts}$
4. **Penalties & Deduction Gates**:
   - Extension above $\text{EMA}_{20} > 1.5\text{ ATR}$: $-10\text{ pts}$
   - Promotor pledge $> 10\%$: $-15\text{ pts}$
   - Young listing ($< 200$ bars): $-5\text{ pts}$

---

# 7. ACTIVE PRODUCTION SCANNER EXECUTION CODE FLOWS

All active scanners execute sequentially or concurrently under strict mutex lock protection:

## 7.1 Daily Watchlist Builder (`app/daily_builder.py`)
```python
def run_daily_builder():
    """
    Constructs the authoritative daily universe.
    Executes at 01:00 AM IST daily.
    """
    raw_universe = fetch_exchange_universe()  # ~940+ equities
    approved_symbols = []

    for symbol in raw_universe:
        # 1. Price floor gate
        if symbol.cmp < config.MIN_STOCK_PRICE (100.0):
            continue
        # 2. Minimum liquidity & history
        if symbol.turnover_cr < 1.0 or symbol.avg_volume_20d < 100000:
            continue
        if symbol.bar_count < 50:
            continue
        # 3. Promoter pledge ceiling
        pledge_pct = nse_pledge_fetcher.get_pledge_pct(symbol)
        if pledge_pct is not None and pledge_pct > 15.0:
            continue
        # 4. Circuit filter & surveillance
        if symbol.is_in_gsm_or_asm or symbol.has_5pct_circuit:
            continue

        approved_symbols.append(symbol)

    save_watchlist_parquet(approved_symbols, path="data/watchlist.parquet")
    upsert_build_manifest(approved_symbols)
    return len(approved_symbols)
```

## 7.2 Technical Breakout Scanner (`app/technical_scanner.py`)
```python
def run_technical_scanner():
    """
    High-Conviction Technical Momentum Breakout.
    Certified and active strictly in BULL regime.
    """
    if current_regime != MarketRegime.BULL:
        logger.info("⏸️ [TECHNICAL] Market regime is not BULL. Scanner suspended.")
        return 0

    universe = watchlist_cache.get_watchlist()
    approved_candidates = []

    for symbol, df in price_provider.fetch_batch_1d(universe).items():
        if df is None or len(df) < 50: continue
        latest = df.iloc[-1]

        # 1. 20-Day High Breakout
        prior_20d_high = df["High"].iloc[-21:-1].max()
        if latest["Close"] <= prior_20d_high: continue

        # 2. Distance from 52W High <= 15%
        dist_52w = (df["High"].iloc[-252:].max() - latest["Close"]) / latest["Close"] * 100.0
        if dist_52w > 15.0: continue

        # 3. Candle Geometry
        range_hl = latest["High"] - latest["Low"]
        if range_hl <= 0: continue
        if (latest["Close"] - latest["Open"]) / range_hl < 0.45: continue
        if (latest["Close"] - latest["Low"]) / range_hl < 0.65: continue
        if (latest["High"] - latest["Close"]) / range_hl > 0.35: continue

        # 4. Volume Surge
        median_vol = df["Volume"].iloc[-21:-1].median()
        if latest["Volume"] < 1.8 * median_vol: continue

        # 5. ATR Volatility Expansion
        atr_20 = calculate_atr(df, period=20)
        if (range_hl / atr_20) < 0.9: continue

        # 6. Non-Extended Extension Gate
        ema_20 = calculate_ema(df["Close"], span=20)
        if (latest["Close"] - ema_20) / atr_20 > 1.5: continue

        # 7. Quality Score
        score = scoring_engine.calculate_score(symbol, df)
        if score < 82: continue

        # 8. Dynamic Stop Loss & Target Placement
        sl_result = compute_sl_and_target(df, mode="TECHNICAL")
        if sl_result["rr_ratio"] < 2.0: continue

        approved_candidates.append({
            "symbol": symbol, "score": score, "entry": latest["Close"], "sl_result": sl_result
        })

    # Sort descending by score and commit top candidates via Authoritative Outbox
    approved_candidates.sort(key=lambda x: x["score"], reverse=True)
    return commit_outbox_alerts(approved_candidates[:10], scanner="TECHNICAL")
```

## 7.3 Quality Compounder & Wealth Engine (`app/wealth_engine.py`, `app/live_fundamental_scanner.py`)
```python
def run_wealth_scan():
    """
    Evaluates 4 fundamental buckets with Point-in-Time integrity.
    Runs every 15 minutes during market hours.
    """
    universe = watchlist_cache.get_watchlist()
    approved = []

    for symbol in universe:
        pit_data = get_certified_pit_fundamentals(symbol)
        if not pit_data.is_complete():
            continue  # Fails closed if data is incomplete

        # Enforce Extreme Bubble Valuation Ceiling
        if pit_data.peg > 3.0:
            continue

        # Evaluate Fundamental Buckets
        bucket = None
        if pit_data.is_core_compounder():
            bucket = "CORE_COMPOUNDER"
        elif pit_data.is_growth_multiplier():
            bucket = "GROWTH_MULTIPLIER"
        elif pit_data.is_quality_on_sale():
            bucket = "QUALITY_ON_SALE"
        elif pit_data.is_opportunistic():
            bucket = "OPPORTUNISTIC"

        if not bucket:
            continue

        # Timing Gate
        df = price_provider.get_daily_candles(symbol)
        if df.iloc[-1]["Close"] <= calculate_sma(df["Close"], 200):
            continue

        # Pre-BUY Filing Freshness Fence
        if has_newer_exchange_filing_published(symbol, pit_data.filing_date):
            log_suppression(symbol, reason="UPDATE_PENDING_FRESH_FILING")
            continue

        approved.append({"symbol": symbol, "bucket": bucket, "pit_data": pit_data})

    return commit_outbox_alerts(approved, scanner="WEALTH")
```

## 7.4 Multibagger Engine (`app/multibagger.py`)
```python
def run_multibagger_scan():
    """
    Multi-year compounder screening.
    Enforces strict non-null pledge and Piotroski score gates.
    """
    universe = watchlist_cache.get_watchlist()
    approved = []

    for symbol in universe:
        fund = get_certified_pit_fundamentals(symbol)
        if fund.piotroski_f_score is None or fund.promoter_pledge_pct is None:
            continue  # Hard fail-closed on missing fundamental metrics

        score = calculate_multibagger_composite_score(symbol, fund)
        tier = None

        if score >= 75 and fund.piotroski_f_score >= 7 and fund.promoter_pledge_pct <= 10.0:
            tier = "PRIME"
        elif score >= 65 and fund.promoter_pledge_pct <= 15.0:
            tier = "HIGH_QUALITY"
        else:
            continue

        approved.append({"symbol": symbol, "score": score, "tier": tier})

    return commit_outbox_alerts(approved, scanner="MULTIBAGGER")
```

---

# 8. FUNDAMENTALS DATA PIPELINE & POINT-IN-TIME INTEGRITY

The fundamental pipeline enforces strict financial integrity invariants:

```text
               RAW UPSTREAM INGESTION
          (Upstox API / NSE XBRL / Exchange)
                         │
                         ▼
             DATA AUDIT & VALIDATION
          (No-Zero-Denominator, Range Checks)
                         │
                         ▼
               NEVER-DOWNGRADE GATE
     (Newer filing period replaces older;
      Never overwrite complete filing with incomplete)
                         │
                         ▼
             CANONICAL PIT STAGING STORE
             (Cryptographically Hashed)
                         │
                         ▼
             SOURCE FRESHNESS FENCE
        (Pre-BUY Exchange Watermark Check)
                         │
                         ▼
                 BUY DECISION GATE
```

1. **Point-in-Time (PIT) Causality**: Decisions are strictly conditioned on filings publicly published prior to the decision timestamp.
2. **Never-Downgrade Gate**: A previously certified, high-completeness financial snapshot is permanently protected against being overwritten by an incomplete or downgraded record.
3. **Source Freshness Fence**: No BUY alert may be committed when a newer valid filing is known to have been published on the exchange prior to the alert commit transaction.
4. **Single-Point Provenance Certification Gate Invariant (Rule 65 / Rule 67 Clean Architecture)**:
   - Within `LiveFundamentalBuyScanner`, `prov_valid` starts strictly initialized to `False` (`prov_valid = False`).
   - Zero premature certifications are permitted in any pre-recovery snapshot branch or intermediate PIT recovery branch.
   - Exactly ONE closed Boolean certification gate exists in the entire pipeline:
     ```python
     prov_valid = False
     # ... snapshot integration / recovery / field derivations / normalization ...
     prov_valid = compute_fundamental_provenance_valid(funds, is_data_stale=is_data_stale)
     ```
   - Only `compute_fundamental_provenance_valid(...)` can set `prov_valid = True`, validating: (1) 4-field quality completeness (`roce`, `roe`, `debt_equity`, `operating_cash_flow`), (2) strict provider-status pair binding (`VALID_PROVIDER_PROVENANCE_COMBINATIONS`), (3) annual filing basis (`quality_source_basis == 'ANNUAL' and annual_filing_present is True`), (4) PIT freshness and non-staleness (`snapshot_status in {'FRESH', 'CERTIFIED'}` and `not is_data_stale`), and (5) verifiable PIT period/filing metadata.
   - Eliminates all historical state leaks and ensures trivial Rule 65 auditability.

5. **Dataset Schema Identity Gate (`DATASET_SCHEMA_IDENTITY`)**:
   - Registered in `app/database.py`. Strictly segregates distinct Parquet datasets to prevent cross-contamination:
     - `daily_builder_master_v2`: requires financial ratio columns (`symbol`, `roce`, `roe`, `debt_equity`, `operating_cash_flow`, `pat_growth_cagr_5y`).
     - `canonical_pit_rebuilt`: requires statement-level accounting columns (`symbol`, `period_end`, `filing_date`, `equity_share_capital`, `total_debt`).
     - `pit_fundamentals_v1`: requires PIT ratio columns (`symbol`, `period_end`, `roce`).
     - `pit_recovery_status`: requires negative cache audit columns (`symbol`, `field`, `status`, `expires_at`).
   - Every `upload_parquet_to_db` and sync operation verifies `ParquetFile.schema.names` in <1ms before database writes. Any mismatched dataset upload is aborted with `RuntimeError`, permanently preventing wrong-dataset DB publication.

6. **Deprecation of Startup Boot-Seed Overwrite**:
   - ~~On startup, `app/main.py` copied `canonical_pit_rebuilt.parquet` into `master_v2_path` and uploaded it as `daily_builder_master_v2`.~~ *(Deprecated 2026-10-04: Removed boot overwrite. Raw accounting statements lacked derived ratio columns like `roce`, causing `roce_null_frac=100.00%` and triggering catastrophic 38-minute live network recoveries)*.

7. **Network-Free Scheduled Scanner Architecture & Performance SLA (<5.0s)**:
   - For all scheduled/cron executions (`scheduler_name != "MANUAL"` or `trigger_type != "MANUAL"`), `allow_live_refresh` is forced to `False`.
   - The scanner loop relies strictly on local pre-built datasets (`daily_builder_master_v2`, `canonical_pit_rebuilt`, and local filing cache).
   - In-scanner network recovery (`_get_pit_filings(sym, allow_live_refresh=True)`) is eliminated from the hot evaluation path. Missing fields fail closed immediately in 0ms.
   - Warm 886-symbol scan runtime SLA dropped from 2,285 seconds (~38 min) to <5.0 seconds (empirically measured at 4.66s), with 0 HTTP calls and 0 broker calls during scanning.

8. **Durable Negative Availability Cache in PostgreSQL (`pit_recovery_status`)**:
   - Implemented via `app/pit_recovery_cache.py`. Negative recovery cache records (fields proven unavailable from upstream with TTL) are persisted to local disk and synchronously/asynchronously synchronized with PostgreSQL table `pit_recovery_status`.
   - On container restart, the negative cache is downloaded from PostgreSQL, preventing redundant HTTP re-queries across application reboots while keeping negative markers strictly out of the canonical filing dataset.

9. **Monotonic Per-Symbol Conflict Isolation in Canonical Publisher**:
   - In `scripts/canonical_pit_publisher.py`, the Never-Downgrade gate is preceded by per-symbol conflict preservation.
   - If a batch of 41 recovered symbols contains 1 symbol with a lower metric (e.g., ROCE 5Y count 831 → 830), the publisher preserves the old verified metric for that isolated symbol while atomically promoting the remaining 40 valid recoveries. Eliminates coarse whole-batch rejections.

10. **Pre-BUY Integrity Alert Suppression & Telemetry Funnel Reconciliation**:
    - When strategy rules pass but Pre-BUY data integrity gates block an alert (e.g. unverified/stale filing), the suppression is recorded in `AlertTelemetryCollector` with reason `PRE_BUY_INTEGRITY_BLOCKED`.
    - Reconciliation invariant: `buy_eligible == buy_alerts_created + buy_alerts_suppressed`.
    - If telemetry integrity fails or reconciliation fails, health is marked `DEGRADED`, and is strictly prohibited from being overridden to `OK`.

11. **Classification Precedence in Data Availability Auditor**:
    - In `app/data_providers/data_availability_auditor.py`, classification order strictly enforces:
      1. `INSUFFICIENT_HISTORICAL_DEPTH` (e.g. symbol has only 3–4 annual filings; 5Y CAGR cannot mathematically be calculated).
      2. `HISTORICAL_FILING_GAP` (discontinuous filing periods).
      3. `INVALID_CAGR_BASE` (zero or negative base-year metric).
      4. `PARSER_OR_FIELD_MAPPING_FAILURE` (only emitted when adequate historical filing depth exists but extraction yielded 0 fields).

---

# 9. EXTERNAL MARKET DATA PROVIDER ROUTING & BROKER INTEGRATIONS

To avoid Cloudflare WAF IP blocks on datacenter VPS hosting, all high-frequency market data routes through authorized broker APIs:

```text
                      MARKET DATA REQUEST
                               │
                               ▼
                    FetchCoordinator (In-Process)
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
          UpstoxProvider               FyersProvider
         (Primary Broker)           (Secondary Fallback)
          • OHLCV 1m/5m/1d            • Quotes API v3
          • 500-Symbol Quotes         • Failover Health Score
          • LTP v3 Quotes             • Provenance Audited
```

## 9.1 Upstox API v2 Integration (`UpstoxProvider`)
- **Primary Provider** for daily and intraday OHLCV candles and high-density market quotes.
- **Endpoints**:
  - `GET https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}`
  - `GET https://api.upstox.com/v2/market-quote/quotes`: Batch quotes up to **500 symbols per single HTTP request**.
  - `GET https://api.upstox.com/v3/market-quote/ltp`: High-speed LTP batch queries.
- **Concurrency**: Managed via `ThreadPoolExecutor` (10 workers) with rate limiters adhering to Upstox's 100 req / 10s ceiling.

## 9.2 FYERS API v3 Integration (`FyersProvider`)
- **Secondary Fallback Provider** for real-time market quotes and failover pricing.
- **Authentication**: Base URL `https://api-t1.fyers.in/api/v3`. Computes SHA-256 `appIdHash` across both `-100` and `-200` app ID suffixes.
- **Endpoints**:
  - `POST /api/v3/history`: Historical daily/intraday candles.
  - `GET /api/v3/quotes`: Full quotes with 5-level market depth.
- **Capability & Provenance Boundary**:
  ```text
  FYERS PUBLIC API V3
  ├── Market Quotes (/api/v3/quotes) → machine-readable diagnostic source
  └── Public REST Fundamentals → NOT AVAILABLE / DOCUMENTED
  FYERS WEB/PLATFORM FUNDAMENTALS
  └── EV/EBITDA, ROCE, ROE, etc. → reference evidence only unless an explicitly authorized machine-readable interface exists
  ```
  - Approved diagnostic broker source strictly for the capabilities exposed by its public API v3 (`/data/quotes`, `lp`, volume, depth, OHLC).
  - Public REST fundamentals / key-ratios are NOT AVAILABLE / DOCUMENTED. Web/platform fundamentals (EV/EBITDA, ROCE, ROE) serve as reference evidence only unless an explicitly authorized machine-readable interface exists.

## 9.3 Yahoo Finance Gateway (`app/safe_yf_call.py`)
- Strictly restricted to **Earnings Calendar Dates** and isolated secondary cross-checks.
- All outbound requests pass through the thread-safe `safe_yf_call` gateway featuring exponential backoff and a 10-minute circuit breaker upon receiving HTTP 429.
- Declared earnings dates are cached with a **45-day TTL**.

## 9.4 AI Concall Analysis Engine (`app/ai_analyzer.py`)
- Analyzes quarterly concalls and management investor presentations fetched from exchange feeds.
- **Google Gemini API**: Dynamic model discovery prioritizing `gemini-2.0-flash` with dual key authentication (`AIza...` and Authorization Auth `AQ...`).
- **OpenAI Fallback**: Automatically invokes `gpt-4o-mini` if Gemini quotas are exhausted.

---

# 10. QUALITY DATA AVAILABILITY AUDITOR & RECOVERY-DIAGNOSTICS

Implemented in `app/data_providers/data_availability_auditor.py` and `app/fundamental_pre_recovery.py`:

## 10.1 Strict 3-Tier Authority Hierarchy
- **Tier 1 — Production-Authoritative**: Upstox Fundamentals API, Upstox Key Ratios, NSE Corporate Filings XBRL, Certified PIT cache.
- **Tier 2 — Approved Diagnostic Broker Source**: FYERS API v3. Approved diagnostic broker source strictly for public API v3 capabilities (market quotes, LTP, volume, depth). Public REST fundamentals / key-ratios are not available / documented.
- **Tier 3 — Forensic Reference Only**: Screener.in. Strictly an offline diagnostic oracle (`canonical_pit_write = FALSE`, `buy_allowed = FALSE`).

## 10.2 Four Mandatory Governance Diagnostic Conditions
1. `PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE`: Primary recovery on Upstox/NSE failed, but data was found in an approved broker feed (FYERS).
2. `SCREENER_ONLY_DATA_SOURCE`: Missing across all production sources and FYERS, but present on Screener.
   - **Semantic Classification Meaning**: *"Only verified/accessible reference source found"*. Specifically, among programmatically verifiable sources in our audit layer, only Screener currently exposes this reference information. It highlights an upstream ingestion gap in our primary pipeline, but does NOT assert that the data is absent from other non-public screens or web platform UI views.
   - Dispatches discrete stock-level notices to the Admin Dashboard Bell Icon (`global_notifications`).
3. `PARSER_OR_FIELD_MAPPING_FAILURE`: Provider returned HTTP 200 with raw records, but 0 usable fields could be extracted. Flags upstream parser/taxonomy bug.
4. `DATA_UNAVAILABLE_VERIFIED`: Confirmed missing across all primary, secondary, and forensic providers.

## 10.3 Database Schema: `data_availability_audit`
```sql
CREATE TABLE IF NOT EXISTS data_availability_audit (
    audit_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    isin TEXT,
    scanner TEXT NOT NULL,
    field TEXT NOT NULL,
    required_for_gate TEXT,
    upstox_status TEXT,
    nse_status TEXT,
    exchange_filing_status TEXT,
    pit_status TEXT,
    local_cache_status TEXT,
    fyers_status TEXT,
    screener_status TEXT,
    classification TEXT NOT NULL,
    severity TEXT NOT NULL,
    upstox_key_ratios TEXT,
    admin_action TEXT,
    production_value_written BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (production_value_written = FALSE),
    buy_allowed BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (buy_allowed = FALSE),
    admin_alert_generated BOOLEAN NOT NULL DEFAULT FALSE,
    run_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (audit_date, symbol, field)
);
```

---

# 11. AUTHORITATIVE OUTBOX PERSISTENCE & POSTGRESQL DATABASE ARCHITECTURE

The Database Transactional Outbox (`buy_alerts_journal`) is the **authoritative single source of truth**, with filesystem Parquet files acting as idempotent derived materializations.

```text
Order of Operations:
  1. Pre-commit Decision Gate (Snapshot SHA drift, Watcher state, Source Freshness Fence).
  2. Authoritative DB Outbox commit (status = 'COMMITTED', materialized_to_parquet = 0).
  3. Idempotent Parquet file materialization.
  4. DB completion marker update (materialized_to_parquet = 1, materialized_at = NOW()).
```

## 11.1 Crash-Recovery Reconciler (`reconcile_alerts_outbox_materialization`)
Executed continuously and on boot:
- Resolves unmaterialized DB records (`materialized_to_parquet = 0`).
- Reconstructs corrupted Parquet files from the database journal.
- Prunes orphaned records present in Parquet without DB backing.

## 11.2 Core PostgreSQL DDLs

### Alerts Outbox Journal: `buy_alerts_journal`
```sql
CREATE TABLE IF NOT EXISTS buy_alerts_journal (
    id SERIAL PRIMARY KEY,
    alert_uuid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    symbol VARCHAR(30) NOT NULL,
    scanner VARCHAR(50) NOT NULL,
    breakout_type VARCHAR(50) NOT NULL,
    entry_price NUMERIC(12, 4) NOT NULL,
    stop_loss NUMERIC(12, 4) NOT NULL,
    target_1 NUMERIC(12, 4) NOT NULL,
    target_2 NUMERIC(12, 4) NOT NULL,
    target_3 NUMERIC(12, 4) NOT NULL,
    target_4 NUMERIC(12, 4),
    score NUMERIC(6, 2) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'COMMITTED',
    materialized_to_parquet INT NOT NULL DEFAULT 0,
    materialized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Global Admin Notifications: `global_notifications`
```sql
CREATE TABLE IF NOT EXISTS global_notifications (
    id SERIAL PRIMARY KEY,
    notification_type VARCHAR(60) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'INFO',
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    symbol VARCHAR(30),
    field VARCHAR(60),
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Scanner Health Telemetry: `scanner_health`
```sql
CREATE TABLE IF NOT EXISTS scanner_health (
    scanner_name VARCHAR(50) PRIMARY KEY,
    status VARCHAR(30) NOT NULL DEFAULT 'IDLE',
    last_run_time TIMESTAMPTZ,
    alerts_generated INT NOT NULL DEFAULT 0,
    duration_seconds NUMERIC(8, 2) DEFAULT 0.0,
    error_msg TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

# 12. CORPORATE ACTION EVENT FRAMEWORK & TRADING CALENDAR

Implemented in `app/corporate_events.py` and `app/trading_calendar.py`:

- **Decoupled Architecture**: `TradingCalendar` computes true exchange trading days, skipping weekends and official NSE holidays.
- **Priority Event Badges**:
  - `EARNINGS` (Priority 100): `E in 3d` / `E 2d ago`
  - `DIVIDEND` (Priority 80): `D`
  - `SPLIT` (Priority 70): `S`
  - `BONUS` (Priority 60): `B`
- **Client-Side Rendering (`renderEventBadges`)**: Renders up to 2 primary badges with a touch-friendly `+N` overflow pill and popover tooltip.

---

# 13. CONCURRENCY, SYNCHRONIZATION & LOCK HIERARCHY

To guarantee absolute memory and file integrity, process locks are managed hierarchically via `app/lock_utils.py`:

```text
Lock Hierarchy (Acquisition Order):
  Level 1: SystemLock (Global System State)
    Level 2: WatchlistLock (Watchlist Generation)
      Level 3: PriceCacheLock (Market Data Downloads)
        Level 4: ScannerProcessLock (Individual Scanner Execution)
          Level 5: DatabaseOutboxLock (Transactional Journal Write)
```

- **Process Lock Telemetry (`/api/lock-stats`)**: Exposes lock acquisition counts, wait times, hold times, and contention events across all subsystems.
- **Execution Banners**: Every scanner emits standardized log banners upon lock acquisition and release.

---

# 14. AUTONOMOUS SCHEDULER & 24/7 EXECUTION BLUEPRINT

Governed by `run_system_scheduler()` in `app/main.py`:
- Replaces third-party cron daemons with a self-healing, time-aware in-process scheduler.
- **Non-Market Boot Catch-Up Sequence**: If restarted outside market hours, sequentially runs `DAILY_BUILDER` $\rightarrow$ `TECHNICAL` $\rightarrow$ `FUNDAMENTAL` $\rightarrow$ `QUALITY_COMPOUNDER` $\rightarrow$ `QUALITY_VALUE_RECOVERY` to refresh caches and align health states cleanly.

---

# 15. ALERT LIFECYCLE, STATE MACHINE & EXIT MANAGEMENT

```text
       [ SIGNAL GENERATED ]
                │
                ▼
            ┌───────┐
            │ OPEN  │
            └───┬───┘
                │
       ┌────────┴────────┐
       ▼                 ▼
   Stop Hit           Target 1 Hit
       │                 │
       ▼                 ▼
   ┌───────┐        ┌──────────┐
   │ LOSS  │        │ TRAILING │ (Trail SL to Breakeven)
   └───────┘        └────┬─────┘
                         │
                ┌────────┴────────┐
                ▼                 ▼
            Trailing SL Hit    Target 2 Hit
                │                 │
                ▼                 ▼
        ┌──────────────┐     ┌──────────┐
        │ PARTIAL_WIN  │     │ TRAILING │ (Trail SL to T1)
        └──────────────┘     └────┬─────┘
                                  │
                                  ▼
                             Target 3 Hit
                                  │
                                  ▼
                              ┌───────┐
                              │  WIN  │ (100% Position Closed)
                              └───────┘
```

- **Exit Conditions**:
  1. Stop Loss Hit: Candle Low $\le$ active `stop_loss`.
  2. Trailing Stop Hit: Reversal touches trailed stop loss.
  3. Target Hit: Price touches T1, T2, or T3.
  4. Time Expiry (`EXPIRED`): Trade fails to hit T1 within 20 trading days.
- **Intrabar Precedence**: If a candle touches both Stop Loss and Target, **Stop Loss (`LOSS`) takes conservative precedence**.

---

# 16. COMPLETE REST API SPECIFICATIONS & STREAMING PROTOCOLS

## 16.1 Dashboard & Alerts Endpoints
- `GET /api/alerts`: Active and closed alerts with CMP, PnL %, and event badges.
- `GET /api/viewers`: Real-time active viewer counter.
- `GET /api/notifications`: Returns unread admin notifications for the Admin Bell Icon.
- `GET /api/notifications/stream`: Server-Sent Events (SSE) stream delivering real-time admin notifications.
- `POST /api/notifications/mark_read`: Marks notifications as read.

## 16.2 Data Availability Auditor Endpoints
- `GET /api/admin/data_availability/counts`: Real-time counts across all 11 diagnostic categories.
- `GET /api/admin/data_availability/audits`: Stock-by-stock audit records with 17 required provenance fields.

## 16.3 System Control Endpoints
- `POST /api/trigger-scanner`: Manually enqueues and triggers a scanner cycle.
- `GET /api/lock-stats`: Mutex lock telemetry and contention statistics.
- `GET /api/v1/symbols/suggest`: Sub-millisecond ticker autocomplete search.

---

# 17. EXHAUSTIVE REPOSITORY MODULE INVENTORY & INTERFACE CONTRACTS

| Module Path | Core Role | Primary Class / Functions |
|---|---|---|
| `app/daily_builder.py` | Universe Construction | `run_daily_builder()`, `evaluate_daily_builder_symbol()` |
| `app/technical_scanner.py` | Technical Breakout Engine | `run_technical_scanner()`, `evaluate_technical_symbol()` |
| `app/wealth_engine.py` | Fundamental Compounders | `run_wealth_scan()`, `evaluate_wealth_symbol()` |
| `app/fundamental_wealth_engine.py` | Value Recovery Scanner | `run_quality_recovery_scan()` |
| `app/multibagger.py` | Multibagger Screening | `run_multibagger_scan()`, `evaluate_multibagger_symbol()` |
| `app/data_providers/data_availability_auditor.py` | Pre-Recovery Diagnostics | `audit_universe_data_availability()`, `DataAvailabilityAuditor` |
| `app/performance_tracker.py` | Exit Management & CMP | `run_performance_tracker()`, `CMP Exit Monitor` |
| `app/corporate_events.py` | Corporate Action Badging | `decorate_events()`, `CorporateEventPipeline` |
| `app/trading_calendar.py` | Exchange Trading Days | `TradingCalendar` |
| `app/database.py` | DB & Outbox Persistence | `reconcile_alerts_outbox_materialization()`, outbox queries |
| `app/dashboard_server.py` | Flask Web Server & APIs | REST endpoints, SSE streams, template renderers |
| `app/stock_analyzer.py` | On-Demand Diagnostics | `validate_nse_bse_ticker()`, `analyze_watchlist()` |

---

# 18. UI/UX SPECIFICATIONS & CLIENT-SIDE ARCHITECTURE

- **Glassmorphic Aesthetic**: Modern HSL dark theme with subtle neon accents, CSS glassmorphism, and responsive layout.
- **Client-Side Autocomplete**: Pre-loads all official NSE equities into `window.MASTER_SYMBOLS_CLIENT_ARRAY` for $<0.1\text{ms}$ instantaneous searching.
- **Touch-Friendly Modals**: Background scroll locking (`overscroll-behavior: contain`), z-index isolation, and custom glassmorphism confirmation cards.

---

# 19. PRODUCTION CONFIGURATION REFERENCE (`app/config.py`)

Key production constants:
```python
MIN_STOCK_PRICE = 100.0
MIN_AVG_DAILY_VOLUME = 100000
MIN_TURNOVER_CR = 1.0
MAX_DISTANCE_FROM_52W_HIGH_PCT = 15.0
MIN_ATR_EXPANSION_RATIO = 0.9
SCORE_THRESHOLDS = {
    "TECHNICAL": 82,
    "WEALTH": 55,
    "MULTIBAGGER_HIGH_QUALITY": 65,
    "MULTIBAGGER_PRIME": 75,
}
EXIT_PROFILES = {
    "BALANCED": {"T1": 0.30, "T2": 0.40, "T3": 0.30},
}
```

---

# 21. FROZEN PRODUCTION DATA-RECOVERY & 7-DAY SCANNER QUARANTINE POLICY

### Priority Order & Policy Invariants
1. **P0: Fix Provider Parsing & Mapping First**:
   - `HTTP 200 + raw rows > 0 + usable fields == 0` $\rightarrow$ `PARSER_OR_FIELD_MAPPING_FAILURE`, never `DATA_UNAVAILABLE`.
   - Comprehensive exchange date normalization (`%Y-%m-%d`, `%d-%b-%Y`, `%d/%m/%Y`, `%d-%m-%Y`, `%b-%Y`, `%Y%m%d`), span-based period inference (span $\ge 300\text{d} \rightarrow$ ANNUAL), and parenthesis negative handling `(12.34) \rightarrow -12.34`.
2. **P0: Exhaust Approved Providers Before Declaring Unresolved**:
   - Sequence: `Canonical PIT / Daily Builder` $\rightarrow$ `NSE` $\rightarrow$ `BSE` $\rightarrow$ `Upstox` $\rightarrow$ `FYERS` $\rightarrow$ Screener reference lookup.
   - Listing-Aware: BSE-only/SME securities evaluate to `NSE = NOT_APPLICABLE`, `BSE = CHECKED`.
   - FYERS Real Exhaustion: `FYERS = NOT_CHECKED` is eliminated. Returns `UNSUPPORTED_FIELD` for balance sheet metrics under REST API v3.
3. **P0: Pre-Scan Recovery Decoupled from Scanner Decision Loop**:
   - Recovery sweeps occur strictly in offline/pre-scan phases (`FundamentalPreRecoveryEngine`). Scheduled scanners operate in `WARM` read-only mode (`allow_live_refresh=False`) with zero network calls.
4. **P1: Screener Reference-Only Fallback**:
   - Used purely as a forensic diagnostic oracle. If Screener has data but approved providers fail: `PRIMARY_PROVIDERS_UNAVAILABLE`, `REFERENCE_SOURCE_AVAILABLE` (Admin notified, canonical PIT write blocked, production BUY blocked).
5. **P1: 7-Day Scanner Quarantine & Stock List Exclusion**:
   - When even Screener has no data (`CONFIRMED_NO_DATA_ANYWHERE` / `CONFIRMED_SHORT_HISTORY` / `HISTORICAL_FILING_GAP`), a 7-day quarantine is enforced.
   - **Scanner Removal Invariant**: Quarantined stocks are excluded from the target universe before scanning so they are not evaluated or logged as incomplete data during the 7-day window. On Day 8, the scanner re-evaluates upstream; if found continue, else re-quarantined for 7 days.
   - **Dependency-Level Quarantine**: Scoped to `(symbol, field, scanner_family)` so missing financial metrics do not suppress unaffected technical scanners.
   - **Parser Defect Isolation**: 7-day cooldown NEVER applies to code defects (`PARSER_OR_FIELD_MAPPING_FAILURE` $\rightarrow$ 15m retry).
6. **P1: Durable PostgreSQL Negative Cache & Invalidation Fingerprint**:
   - Persisted in Parquet and synced to PostgreSQL. Stores `latest_filing_date` and `raw_record_count`. If a newer filing appears upstream on the exchange before 7 days, the cooldown is broken immediately.
7. **P1: Separation of Recovery Status vs. Promotion Status**:
   - Upstream recovery (`recovery_status = SUCCESS | PARTIAL_SUCCESS`) is strictly decoupled from canonical promotion (`promotion_status = PROMOTED | BLOCKED (NEVER_DOWNGRADE/VALIDATION)`).

---
*End of Complete Technical Architecture & Production Specification — `docs/SYSTEM_ARCHITECTURE.md`*

