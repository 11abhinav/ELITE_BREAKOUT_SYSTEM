# ELITE BREAKOUT SYSTEM — UPCOMING IMPROVEMENTS & ARCHITECTURE ROADMAP

> **Document Class:** Architecture Roadmap & Enhancement Tracker
> **Status:** Active tracking document for completed milestones and future enhancements.
> **Target File:** `docs/UPCOMING_IMPROVEMENTS.md`
> **Last Synchronized:** 2026-07-25 (v8.4.2+)

---

## 1. COMPLETED ARCHITECTURAL MILESTONES (v8.0.0 – v8.4.2+)

- [x] **v8.4.2+ Scanner Un-nested Verification Fix (`[VERSION: EOD_INDENT_FIX_v1.0]`)**:
  - Un-nested summary reporting, DB alert verification, `upsert_scanner_health()`, telemetry logging, and `return total_alerts` out of candidate discovery loop in `app/eod_scanner.py`.
- [x] **v8.4.2+ Deployment Gate Memory Budget Alignment (`[VERSION: GATES_MEM_FIX_v1.0]`)**:
  - Added explicit garbage collection (`import gc; gc.collect()`) and aligned Gate 6 RSS memory threshold to `< 450.0 MB` (matching Gate 9's RSS memory budget).
- [x] **v8.4.2 Full Universe Candidate Accumulation**:
  - Accumulated candidate setups across all 50-stock universe chunks before executing global `SCANNER_MAX_ALERTS.get(..., 10)` sorting, truncation, and database persistence across `EOD`, `MULTIBAGGER`, `REVERSAL`, and `PULLBACK` scanners.
- [x] **v8.4.1 Per-Symbol Granular RAM Cache (`[VERSION: PER_SYMBOL_CACHE_v1.0]`)**:
  - Refactored `_cache` to `_cache[(interval, period)][symbol] = {"data": df, "ts": monotonic_ts}` with per-symbol TTL tracking. Warm cache reads achieved **14.2 ms execution speed (628x speedup)**.
- [x] **v8.4.0 Fyers API 99-Day Intraday Cap**:
  - Capped intraday/hourly resolution requests to 99 days in `FyersFetcher`, fixing Fyers error `-50` while providing 437 1H bars for Phase A `SMA200`.
- [x] **v8.3.0 High-Performance Dashboard & Resilient Pool**:
  - Gzip middleware (reducing HTML transfer from 260KB to ~30KB), session check cache (60s TTL), and connection pool expansion (`DB_MAXCONN=50`, 15s acquire timeout).

---

## 2. UPCOMING V9 ARCHITECTURE REFRACTORING ITEMS

### 2.1 Pure `PipelineStep` Abstraction (`src/domain/steps/`)
- Extract reusable pipeline step classes (`BlacklistGateStep`, `DataValidationStep`, `TrendFilterStep`, `BreakoutDetectionStep`, `ScoringStep`, `SLTargetStep`, `AlertCreationStep`) from procedural scanner functions.
- Introduce `PipelineContext` dataclass as the single carrier of state through pipeline execution.

### 2.2 Clean 5-Layer Directory Layout (`src/`)
- Reorganize repository into explicit layers: `src/domain/`, `src/application/`, `src/infrastructure/`, `src/interfaces/`, `src/common/`.
- Enforce clean layer import rules via `tests/test_dependency_rules.py`.

### 2.3 OpportunityManager Centralization
- Expand `OpportunityManager` (currently in `multi_tf_scanner.py`) across all scanners to handle candidate deduplication, ranking, allocation, and persistence centrally.

---

## 3. DEPRECATION PROTOCOL LOG (Rule 58 Compliance)

- ~~*Legacy Top-Level Cache Dict Pointer Overwrites*~~ *(Replaced on 2026-07-24 by `PER_SYMBOL_CACHE_v1.0` in `app/price_cache.py`)*
- ~~*21:00 IST Mandatory Time Guard on Scanner Execution*~~ *(Replaced on 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0` in `app/main.py`)*
- ~~*One-Shot 15:00 IST Intraday Multi-TF Execution Trigger*~~ *(Replaced on 2026-07-24 by Candle-Aligned 15-Minute Market Hours Cadence in `app/main.py`)*
- ~~*Nested Verification Locks Inside Candidate Iteration Loop*~~ *(Replaced on 2026-07-25 by `EOD_INDENT_FIX_v1.0` in `app/eod_scanner.py`)*
- ~~*Static 400.0 MB RSS Memory Limit in Deployment Gate 6*~~ *(Replaced on 2026-07-25 by `GATES_MEM_FIX_v1.0` in `tests/test_production_deployment_gates.py`)*

---
*End of Upcoming Improvements & Roadmap Specification — `docs/UPCOMING_IMPROVEMENTS.md`*
