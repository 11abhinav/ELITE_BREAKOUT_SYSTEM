# SYSTEM GUIDE

> **Note:** This document is the true operational runbook for the platform. It details system lifecycle, failure modes, recovery strategies, and performance baselines.

## 1. System Lifecycle & Operational Flow

The system is a fully governed, quantitative trading platform operating autonomously 24/7. 

### 1.1 The Daily Schedule (IST)
The `run_system_scheduler()` in `app/main.py` drives the platform's heartbeat:
- **00:00 - Session Rotation:** The `ApplicationContext` / `SessionContext` forces a burn-down of yesterday's trading session and clears daily state.
- **01:00 - Daily Builder:** The `DailyBuilder` screens fundamental metrics, verifies symbols aren't delisted, and locks in the fundamental trading universe parquet file (`watchlist.parquet`).
- **02:00 - Wealth Engine (Initial):** Evaluates fundamental watchlist against 1D historical technical positioning to classify stocks into BUY / HOLD / WATCH tiers.
- **08:30 - Verify Scans Checkpoint:** Validates system file readiness and cache freshness before market open.
- **09:15 to 15:30 - Live Market Hunting:**
  - `Wealth Engine` market-hours loop runs every 5 minutes updating BUY zone proximity.
  - `MultiTFScanner` runs every 15 minutes on completed candle boundaries (`:00`, `:15`, `:30`, `:45`) from 09:30 AM to 14:45 PM IST.
  - `Multibagger Exit Monitor` runs every 15 minutes.
  - `Performance Tracker` updates dashboard metrics every 5 minutes (async/debounced).
- **18:00+ - Post-Market Evening Scanners:**
  - The scheduler calls `wait_for_bhavcopy_or_fallback("EVENING_SCANNERS")`.
  - As soon as NSE Bhavcopy is ready (~18:30–19:30 IST), the scheduler sequentially executes `_run_eod_with_retries`, `_run_reversal_with_retries`, and `_run_pullback_with_retries`.
  - All three scanners are called with `force=True` *(Added 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0`)* so the scheduler's timing decision is authoritative and alerts are saved to the database immediately.
- ~~**21:00 to 23:59 - Legacy Fixed EOD Window**~~: *(Replaced on 2026-07-24 by `SCHEDULER_CORRECTNESS_v1.0`)* Scanners no longer require wall-clock time to be ≥ 21:00 IST to save alerts. `force=True` allows immediate production alert persistence upon Bhavcopy delivery (~18:30-19:30 IST).
- **19:00 - Multibagger Scanner:** Daily execution of long-term fundamental quality + technical buy-zone screener.

---

## 2. Failure Modes & Recovery Strategies

The system is designed to degrade gracefully rather than crash. If a dependency fails, a recovery strategy executes immediately.

### 2.1 Provider Outages (Fyers / Yahoo API Down)
- **Symptom:** HTTP 500s or timeouts during data fetch.
- **Recovery:** The `UnifiedFetcher` detects the failure and instantly routes to the next provider in the `fallback_chain` specified by the `ProviderSelector`.
- **Result:** System continues operating. A warning is logged indicating degraded provenance (fallback used).

### 2.2 Strict NSE Rate-Limiting / WAF Blocks
- **Symptom:** NSE API returns 403 Forbidden or blocks IP.
- **Recovery:** Official NSE compliance datasets (Bhavcopy, Pledges) strictly route through `ScraperAPI` (a residential proxy network) which rotates IPs automatically.
- **Result:** No permanent host bans.

### 2.3 Memory Exhaustion (OOM Risk)
- **Symptom:** System RAM hits 85%+ due to accumulating intraday (1m/15m) dataframes.
- **Recovery:** The `LifecycleManager` watchdog thread detects the pressure and forcefully evicts `EPHEMERAL` datasets from the `DatasetRegistry` (oldest first) and triggers garbage collection.
- **Result:** RAM stabilizes at 50-60%. No application restart required.

### 2.4 Database Unreachable (PostgreSQL)
- **Symptom:** Connection refused on DB writes.
- **Recovery:** The system leverages local fallback caches or logs an error and retries. Upserts to `scanner_health` handle transient connection failures gracefully.

---

## 3. Performance Baselines & SLAs

Expected operational baselines for the platform under typical conditions:

| Operation | SLA Target | Explanation |
| :--- | :--- | :--- |
| **Data Fetch (1D - 300 stocks)** | `< 3.0 seconds` | Parallel batched fetching using async/Threadpool. |
| **Live Quotes Fetch** | `< 1.0 second` | Critical for intraday Multi-TF entry signals. |
| **Indicator Computation** | `O(1) Matrix` | Fully vectorized Pandas/Numpy execution. No `.iterrows()`. |
| **Scanner Execution (All Scanners)**| `< 20.0 seconds`| Complete execution loop over watchlist. Recorded via `duration_seconds` in `scanner_health`. |
| **Database Pool Capacity** | `maxconn=50` | `DB_MAXCONN` default 50 with `15s` semaphore acquire timeout for concurrent loads. |
| **Session Cache Latency** | `< 1 ms` | `_cached_check_session` (60s TTL) eliminates DB hits on frontend polls. |
| **Gzip Response Compression** | `85-95% Reduction`| Compresses Admin HTML (260KB → 30KB) and performance payload (10MB → 500KB). |
| **Session Rotation (Midnight)**| `< 5.0 seconds` | Immediate teardown and rebuild of the SessionContext. |
| **Lock Wait Threshold** | `< 5.0 seconds` | `LOCK_WAIT_WARNING_SECONDS` warns if thread waits > 5s for `scanner_execution_lock`. |
| **Lock Hold Threshold** | `< 60.0 seconds`| `LOCK_HOLD_WARNING_SECONDS` warns if scanner holds lock > 60s. |
| **Container Memory Target** | `250MB - 400MB`| Stable operating window. Should never breach 512MB hard limit. |

---

## 4. Runbook: Manual Operations

If manual intervention is needed:

- **Manual Scanner Trigger via Admin Dashboard / API:**
  Call `/api/trigger-scanner` with `scanner_key`. Triggers run asynchronously in background threads with `force=True`, acquiring `scanner_execution_lock` and saving alerts to DB.
- **Lock Telemetry Monitoring:**
  Check `/api/lock-stats` to inspect `acquisitions_count`, `avg_wait_seconds`, `max_wait_seconds`, `avg_hold_seconds`, `max_hold_seconds`, and `contention_events_count`.
- **Forcing a Watchlist Rebuild:**
  Trigger `DAILY_BUILDER` from Admin Dashboard or invoke `daily_builder.main(force_rebuild=True)`.
- **Purging the Fundamentals Cache:**
  If fundamental categories look stale, delete local parquet files or invoke `DailyBuilder` manually.
