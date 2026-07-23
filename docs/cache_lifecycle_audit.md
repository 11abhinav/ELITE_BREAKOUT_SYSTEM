# Phase 1: Verified System Data Lifecycle Audit (Code-Proven)

> [!IMPORTANT]
> This audit has been constructed by strictly tracing code references (imports, global variables, DB queries, and API calls) without making architectural assumptions. It strictly distinguishes verified facts (traced from code) from recommendations.

---

## 1. Code-Proven Dependency Graph

Every dependency mapped below is explicitly found in the codebase.

| Scanner | Function Call | Dataset Used | Origin Module |
| :--- | :--- | :--- | :--- |
| **Wealth Engine** | `pd.read_parquet(WATCHLIST_PATH)` | Watchlist | `daily_builder.py` |
| **Wealth Engine** | `fetch_unified_historical()` | **1Y Daily OHLCV** (`period="1y", interval="1d"`) | `price_cache.py` (via yfinance) |
| **Wealth Engine** | `calculate_wealth_technicals()`| EMA, RSI, Pivots | `technical_indicators.py` |
| **Multi TF** | `get_watchlist()` | Watchlist | `watchlist_cache.py` |
| **EOD Scanner** | `fetch_delivery_data()` | Delivery Volume | `delivery_data.py` (NSE Archives) |
| **EOD Scanner** | `get_watchlist()` | Watchlist | `watchlist_cache.py` |
| **Reversal Scanner** | `fetch_delivery_data()` | Delivery Volume | `delivery_data.py` |
| **Pullback Scanner**| `fetch_delivery_data()` | Delivery Volume | `delivery_data.py` |
| **All Scanners** | `get_fundamentals()` | Sector, PE, EPS | `fundamentals_cache.py` |

---

## 2. Dataset Inventory (Long-Lived Objects)

| Dataset | Owner | Created By | Consumers | Lifetime | Refresh |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Watchlist DataFrame** | Disk / `watchlist_cache` | `daily_builder.py` | Wealth, MultiTF, EOD, Reversal, Pullback | 24 Hours | 01:00 AM |
| **Fundamentals Dict** | DB / `fundamentals_cache` | `fundamentals_cache.py` | Daily Builder, Scanners | 24 Hours | Tiered (1-7 days) |
| **Bhavcopy (Delivery)** | `delivery_data.py` | `fetch_delivery_data()` | EOD, Reversal, Pullback | Evening Session | 18:00+ (NSE Pub) |
| **FII/DII Block Deals** | `block_deal_detector.py`| `get_cached_fii_deals()`| Alerting / UI | 24 Hours | Post-Market |
| **Nifty Market Regime**| Global `_nifty_cache` | `wealth_engine.py` | Wealth Engine | Intraday | 5-15 mins |

---

## 3. Cache Inventory

The codebase currently contains several disjointed cache implementations.

| Cache Name | Type | Location | Purpose | Duplicated? | 
| :--- | :--- | :--- | :--- | :--- |
| **Watchlist Cache** | Global Variable | `watchlist_cache.py` | Memory access to daily `pd.DataFrame` | Yes (also on Disk and DB) |
| **Nifty Regime Cache** | Global Dict | `wealth_engine.py` (`_nifty_cache`) | Prevents duplicate index calculations | No |
| **Dead Symbols Cache** | Global Dict | `live_prices.py` (`_dead_symbols_cache`) | Blocks retries for delisted symbols | No |
| **Delivery / Bhavcopy** | SQLite DB | `database.py` (`bhavcopy_cache`) | Caches parsed NSE delivery ZIPs | No |
| **Push Throttle Cache** | Global Dict | `push_service.py` (`_push_throttle_cache`) | Throttles identical Telegram alerts | No |
| **Block Deals Cache** | JSON File | `block_deal_detector.py` | Stores FII/DII parsed trades | Yes (DB) |

*Recommendation:* Consolidate `_nifty_cache`, `Watchlist Cache`, and `Dead Symbols Cache` into the `SessionContext` to remove scattered globals.

---

## 4. Memory Ownership Verification (Future State)

Currently, ownership is implicit (modules hold globals). The target verified ownership chain is:

```text
SessionContext
   ↓
HistoricalDataManager (Absolute Owner of pd.DataFrames)
   ↓
IndicatorManager (Absolute Owner of pre-calculated arrays)
   ↓
Scanners (Read-Only Consumers)
```

---

## 5. Scanner Execution Order & State Transitions

**Verified execution chain (from `main.py`):**

1. `01:00` **Daily Builder** → *Creates:* Watchlist, Fundamentals.
2. `02:00` **Wealth Engine (Init)** → *Creates:* Initial scan result (`elite_wealth_system.parquet`). Fetches **1Y Daily OHLCV** (`period="1y", interval="1d"`) for all watchlist symbols.
3. `08:30` **Warmup Check** → *Validates:* Disk caches exist.
4. `09:15-15:30` **Wealth Engine & MultiTF** (Loop) → *Reuses:* Watchlist. *Refreshes:* 5m intraday snapshot. Stitches live CMP into cached 1Y daily. *Creates:* Buy Signals.
5. `21:00` **EOD Scanner**, **Reversal Scanner**, and **Pullback Scanner** → Run **strictly sequentially**. *Creates:* Delivery Data Cache (Bhavcopy). ~~*Verified from `main.py:L657-661`:* `eod_thread.start(); rev_thread.start(); eod_thread.join(); rev_thread.join()`.~~ *(Updated 2026-07-23: Parallel execution is strictly forbidden; `main.py` explicitly executes sequentially via `_run_multibagger_exit_single` and related blocks).*
6. ~~After both complete → **Pullback Scanner** starts and waits (`pb_thread.start(); pb_thread.join()`). *Reuses:* Delivery Data.~~ *(Updated 2026-07-23: All scanners execute in one sequential loop).*

---

## 6. Cleanup Verification

**Current Codebase State:**
* Cleanup relies purely on Python's Garbage Collection when function scopes end.
* `elite_wealth_system.parquet` is persistently overwritten.
* `run_purge_with_telemetry()` in `memory_profiler.py` is **intentionally disabled** — `return 0.0` at line 379 makes the entire purge body unreachable. See §11.

**Phase A (Observation) — No cleanup implemented yet.**

**Verified Future Safe Cleanups (to enable in Phase B after 2-3 session observation):**
* **1Y Daily OHLCV:** Safe to release after full evening batch (EOD + Reversal + Pullback complete). *Verification:* No process runs after Pullback that requires historical daily OHLCV.
* **Bhavcopy/Delivery Cache:** Safe to release after `Pullback Scanner`. *Verification:* Pullback is final consumer — `main.py:L664-665` `pb_thread.start(); pb_thread.join()`.

---

## 7. Network Audit

Verified API calls that consume time and bandwidth:

| API Target | Payload | Caller | Frequency | Cacheable? |
| :--- | :--- | :--- | :--- | :--- |
| **yfinance (Live/Hist)** | Medium (JSON/CSV) | `price_provider.py` | Every 5 mins | Intraday |
| **NSE Bhavcopy Archive** | Large (ZIP) | `delivery_data.py` | Once Daily | Yes (DB Cache) |
| **NSE Block/Bulk Deals** | Medium (CSV) | `institutional_data.py`| Once Daily | Yes (JSON Cache)|
| **ScraperAPI / NSE** | Small (JSON) | `surveillance.py` | Once Daily | Yes |
| **Telegram / Fyers** | Small (JSON) | `push_service.py` | On Alert | No |

---

## 8. Indicator Audit

Verified indicators mathematically computed on DataFrames:

| Indicator | Computed In | Used By | Can be Incremental? |
| :--- | :--- | :--- | :--- |
| **EMA (50, 200)** | `wealth_engine.py` | Wealth, MultiTF, EOD | YES (last row only) |
| **ATR (14)** | `swing_utils.py` | All Scanners | YES (last row only) |
| **RSI (14)** | `technical_indicators.py`| All Scanners | YES (last row only) |
| **Pivots (High/Low)** | `swing_utils.py` | All Scanners | YES (Dirty Region) |
| **Momentum Z-Score** | `wealth_momentum_filter.py`| Wealth Engine | NO (Requires cross-sectional rank) |

---

## 9. Performance Baseline (Observed)

*Measured during live execution (2026-07-23):*
* **Wealth Engine 5m Loop (pre-fix):** ~17 minutes per cycle. Root cause: `get_dynamic_cadence("1d")` returned 60s TTL, causing 7× cold re-fetches of 1Y daily data per run.
* **Wealth Engine 5m Loop (post-fix, SHA `50768a7b`):** Target ~2-3 minutes per cycle. Daily cache now persists until market close.
* **Peak RSS:** ~500 MB per batch during intraday (observed from `BatchMemoryTracker` logs).
* ~~**Evening Batch Runtime:** EOD + Reversal run in parallel. Pullback follows sequentially.~~ *(Updated 2026-07-23: Evening Batch Runtime is strictly sequential. All scanners, including Exit Monitor, run sequentially to respect hard memory constraints.)* Total ~4-6 minutes.

---

## 10. Distinguish Facts from Recommendations

**Verified Facts (Derived from code):**
* `main.py` explicitly forces Pullback Scanner to wait until EOD and Reversal finish (`eod_thread.join()`).
* `Wealth Engine` recalculates `calculate_wealth_technicals` on the *entire* historical frame every 5 minutes.
* `_push_throttle_cache` and `_dead_symbols_cache` are floating global dictionaries with their own manual TTL eviction logic.

**Recommendations for SessionContext:**
* Centralize the 5+ scattered global dictionaries into the `SessionCache`.
* Establish 16:00 as the explicit teardown time for 5m intraday data, and 23:59 for complete `SessionContext` destruction, replacing scattered TTL logic.

---

## 11. Disabled Code: `run_purge_with_telemetry()`

**Location:** `app/memory_profiler.py`, line 379.

**Status:** Intentionally disabled. The function contains `return 0.0` immediately after the docstring, making the entire purge body (cache clearing, `gc.collect()`, `malloc_trim()`) unreachable.

**Why it is disabled:** The system is in observation mode. Aggressive memory purging was causing instability by prematurely clearing caches that are still referenced by active scanners. The function is preserved for historical context and future reactivation under controlled conditions (Phase B).

**Do not re-enable without:**
1. Completing Phase A observation (2-3 full trading sessions)
2. Verifying all dataset consumers have finished before clearing
3. Updating `CACHE_LIFECYCLE_AUDIT.md` with verified release points
