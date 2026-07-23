# SYSTEM GUIDE

> **Note:** This document is the true operational runbook for the platform. It details system lifecycle, failure modes, recovery strategies, and performance baselines.

## 1. System Lifecycle & Operational Flow

The system is a fully governed, quantitative trading platform operating autonomously 24/7. 

### 1.1 The Daily Schedule (IST)
The `scheduler.py` drives the platform's heartbeat:
- **00:01 - Session Rotation:** The `LifecycleManager` forces a burn-down of yesterday's `SessionContext` and initializes a fresh trading day context. Ephemeral data is wiped.
- **09:10 - Watchlist & Universe Generation:** The `DailyBuilder` cross-references wealth constraints, verifies symbols aren't delisted, and locks in the fundamental trading universe for the day.
- **09:15 to 15:30 - Live Market Hunting:**
  - `MultiTFScanner` runs aggressively (e.g. every 15 mins).
  - Scanners request data -> `UnifiedFetcher` fetches -> `IndicatorManager` computes bundles -> Scanners generate signals.
- **18:00 - Data Acquisition (Post-Market):** Fetches official NSE Bhavcopy, Delivery Percentages, and institutional Block/Bulk Deals using the residential proxy (ScraperAPI).
- **21:00 to 23:59 - EOD Processing:** 
  - The heavy `EOD Breakout`, `Reversal`, and `Pullback` scanners execute on perfectly settled, adjusted daily candles.
  - Final alerts are generated and dispatched via Telegram / stored in DB for the dashboard.

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
- **Recovery:** The system leverages local SQLite fallback caches (for internal state) or drops the alert gracefully with a loud logger error. It attempts reconnection on the next cycle.

---

## 3. Performance Baselines & SLAs

Expected operational baselines for the platform under typical conditions:

| Operation | SLA Target | Explanation |
| :--- | :--- | :--- |
| **Data Fetch (1D - 300 stocks)** | `< 3.0 seconds` | Parallel batched fetching using async/Threadpool. |
| **Live Quotes Fetch** | `< 1.0 second` | Critical for intraday Multi-TF entry signals. |
| **Indicator Computation** | `O(1) Matrix` | Fully vectorized Pandas/Numpy execution. No `.iterrows()`. |
| **Scanner Execution (EOD)** | `< 20.0 seconds`| The complete loop over 300 stocks including scoring. |
| **Session Rotation (Midnight)**| `< 5.0 seconds` | Immediate teardown and rebuild of the SessionContext. |
| **Container Memory Target** | `250MB - 400MB`| Stable operating window. Should never breach 512MB hard limit. |

---

## 4. Runbook: Manual Operations

If manual intervention is needed:

- **Forcing a Blacklist Refresh:**
  Run `force_refresh_blacklist()` to pull the latest NSE ban list immediately.
- **Re-running an EOD Scan:**
  Run `eod_scanner.start(force=True)`. The `force=True` flag overrides the 21:00-23:59 time lock.
- **Purging the Fundamentals Cache:**
  If fundamental categories (Wealth Compounders) look stale, delete the local parquet files or invoke the `DailyBuilder` manually.
