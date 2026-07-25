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
4. [Context Model & Dataclass Specifications](#4-context-model--dataclass-specifications)
5. [Core System Enums & Data Models](#5-core-system-enums--data-models)
6. [Quantitative Algorithms, Scoring Formulas & Risk Engines](#6-quantitative-algorithms-scoring-formulas--risk-engines)
7. [Exhaustive Internal Scanner Execution Code Flows (All 6 Scanners)](#7-exhaustive-internal-scanner-execution-code-flows-all-6-scanners)
8. [Fundamentals Data Pipeline & Watchlist Generation](#8-fundamentals-data-pipeline--watchlist-generation)
9. [Data Acquisition, Provider Routing & Resiliency Topology](#9-data-acquisition-provider-routing--resiliency-topology)
10. [Price Cache Infrastructure & Parquet Sidecars](#10-price-cache-infrastructure--parquet-sidecars)
11. [Database Architecture & Complete PostgreSQL DDLs (All Operational Tables)](#11-database-architecture--complete-postgresql-ddls-all-operational-tables)
12. [Concurrency, Synchronization & Lock Hierarchy](#12-concurrency-synchronization--lock-hierarchy)
13. [Autonomous Scheduler & 24/7 Execution Blueprint](#13-autonomous-scheduler--247-execution-blueprint)
14. [Alert Lifecycle, Trailing Stop Mechanics & Cooldown Rules](#14-alert-lifecycle-trailing-stop-mechanics--cooldown-rules)
15. [Complete REST API Specifications & Streaming Protocols](#15-complete-rest-api-specifications--streaming-protocols)
16. [Exhaustive Repository Module Inventory (All Active Codebase Files)](#16-exhaustive-repository-module-inventory-all-active-codebase-files)
17. [UI/UX Specifications & Streaming Contracts](#17-uiux-specifications--streaming-contracts)
18. [Verbatim Production Configuration (`app/config.py`)](#18-verbatim-production-configuration-appconfigpy)
19. [Deterministic Reconstruction Answers (Q1 – Q36)](#19-deterministic-reconstruction-answers-q1--q36)
20. [Deployment Verification, Production Test Gates & Golden Test Suites](#20-deployment-verification-production-test-gates--golden-test-suites)
21. [V9 Clean Architecture Blueprint & Deprecation Protocol Log](#21-v9-clean-architecture-blueprint--deprecation-protocol-log)
22. [AI Reconstruction Checklist & Module Dependency Blueprint](#22-ai-reconstruction-checklist--module-dependency-blueprint)

---

# 1. ARCHITECTURAL PHILOSOPHY & SYSTEM RUNTIME MODEL

## 1.1 Process Architecture & Deployment Budget
- **Runtime Environment**: Single Python 3.9 process running inside a Linux/Railway container.
- **Resource Budget**: Strictly bounded at **500 MB RAM**.
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

# 20. DEPLOYMENT VERIFICATION, PRODUCTION TEST GATES & GOLDEN TEST SUITES

The system enforces **17 Production Deployment Gates** (`tests/test_production_deployment_gates.py`) and 8 Golden Test Suites to guarantee system stability and prevent regressions prior to release:

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

## 20.2 Golden Test Suites & Stability Verification Catalog

1. **Golden Scenarios Test Suite (`tests/test_golden_scenarios.py`)**:
   - Evaluates 12 deterministic market scenarios to verify scanner precision:
     - *Scenario 1: Bullish Breakout Confirmation*: High volume expansion ($\ge 2.5\text{x}$), tight consolidation base, Close $> 20$-day High. Expected: Score $\ge 82$, status `OPEN`.
     - *Scenario 2: Fakeout Reversal Defense*: High upper wick ($> 0.20$), volume contraction. Expected: Scanner REJECT with code `EOD002`.
     - *Scenario 3: Tight Base Expansion*: Narrow Bollinger Band width percentile ($\le 0.35$). Expected: Tight Base Bonus (+5 pts).
     - *Scenario 4: Reversal Oversold Bounce*: Drop depth 30%, RSI 38 curling to 52, SMA50 reclaim. Expected: Reversal Score $\ge 62$.
     - *Scenario 5: Extended Breakout Penalty*: Price $> 1.5\text{x}$ ATR above 20D high. Expected: $-10$ pts penalty.
     - *Scenario 6: OBV Divergence Penalty*: OBV slope $\le 0$. Expected: $-5$ pts penalty.
     - *Scenario 7: Gap & Go Trap Defense*: Opening gap $> 3\%$. Expected: Gap penalty applied.
     - *Scenario 8: Fallen Knife Reversal Cooldown*: Symbol hit reversal alert 15 days ago. Expected: REJECT with code `REV004`.
     - *Scenario 9: High Volatility Rejection*: Candle range $> 15\%$. Expected: REJECT.
     - *Scenario 10: Low Liquidity Filter*: Average daily volume $< 50,000$ shares or liquidity $< ₹15\text{ Cr}$. Expected: REJECT with code `EOD001`.
     - *Scenario 11: Sector Tailwind Surge*: Stock in Top-3 RS sector. Expected: Sector Bonus (+8 pts).
     - *Scenario 12: Promoter Pledge Penalty*: Pledge $\% > 10\%$. Expected: Pledge penalty applied.

2. **Golden Rules Invariants (`tests/test_golden_rules.py`)**:
   - Asserts IEEE 754 floating-point precision safety, zero-float drift, monotonic timestamp ordering in `PriceCache`, non-empty alert payload fields, and risk-reward ratio limits ($\text{RR} \ge 1.5$).

3. **Golden Pipeline Harness (`tests/test_golden_pipeline.py`)**:
   - End-to-end pipeline runner verification executing synthetic OHLCV data through `ScannerPipeline` and asserting DB persistence.

4. **Fort Knox Pullback Test Suite (`tests/test_fort_knox_pullback.py`)**:
   - Resiliency testing for the Pullback Pipeline under choppy market regimes and impulse wave contractions.

5. **Quant Engine Test Suite (F01–F07) (`tests/test_f01_to_f07_quant_engine.py`)**:
   - Verifies mathematical exactness for RSI 14, ADX 14, EMA 9/20/50/200, ATR 20, `FM_Score`, and dynamic stop-loss anti-trap buffers.

6. **Advanced Analytics (F13) (`tests/test_f13_advanced_analytics.py`)**:
   - Validates post-trade win-rate tracking, performance attribution, and score band classification (`(70, 75)`, `(75, 80)`, `(80, 85)`, `(85, 90)`, `(90, 101)`).

7. **Fault Injection & Disaster Recovery (`tests/test_fault_injection.py`)**:
   - Simulates primary provider (Fyers) HTTP timeouts, corrupt Parquet files, PostgreSQL connection disconnects, and thread lock contention to verify graceful fallback execution.

8. **N+1 Query Detection (`tests/test_nplus1_loopholes.py`)**:
   - Monitored SQL execution harness asserting zero N+1 database queries during candidate scoring and alert batch insertion.

---
*End of Complete Technical Architecture & Zero-Code Reconstruction Specification — `docs/SYSTEM_ARCHITECTURE.md`*
