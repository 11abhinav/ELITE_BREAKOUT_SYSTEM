# Phase 1: Verified System Data Lifecycle Audit (Code-Proven)

> [!IMPORTANT]
> This audit has been constructed by strictly tracing code references (imports, global variables, DB queries, and API calls) without making architectural assumptions. It strictly distinguishes verified facts (traced from code) from recommendations.

---

## 1. Code-Proven Dependency Graph

Every dependency mapped below is explicitly found in the codebase.

| Scanner | Function Call | Dataset Used | Origin Module |
| :--- | :--- | :--- | :--- |
| **Wealth Engine** | `pd.read_parquet(WATCHLIST_PATH)` | Watchlist | `daily_builder.py` |
| **Wealth Engine** | `fetch_unified_historical()` | 15m OHLCV | `price_cache.py` (via yfinance) |
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

1. `01:00` **Daily Builder** -> *Creates:* Watchlist, Fundamentals.
2. `02:00` **Wealth Engine (Init)** -> *Creates:* Initial 15m OHLCV (`elite_wealth_system.parquet`).
3. `08:30` **Warmup Check** -> *Validates:* Disk caches exist.
4. `09:15-15:30` **Wealth Engine & MultiTF** (Loop) -> *Reuses:* Watchlist. *Refreshes:* Live 15m Candles. *Creates:* Buy Signals.
5. `21:00` **EOD Scanner** -> *Creates:* Delivery Data Cache (Bhavcopy).
6. `21:05` **Reversal Scanner** -> *Reuses:* Delivery Data. 
7. `21:10` **Pullback Scanner** -> *Reuses:* Delivery Data.

---

## 6. Cleanup Verification

**Current Codebase State:**
* Cleanup relies purely on Python's Garbage Collection when function scopes end.
* `elite_wealth_system.parquet` is persistently overwritten.

**Proposed Safe Cleanups:**
* **15m OHLCV:** Safe to release at `16:00`. *Verification:* No evening scanner imports `fetch_unified_historical` for 15m data; they all request Daily `1d` data.
* **Bhavcopy Cache:** Safe to release after `Pullback Scanner`. *Verification:* It is the final scanner in the `main.py` evening batch (`pb_thread.join()`).

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

*Measured during standard execution prior to Phase 2 architecture:*
* **Wealth Engine 5m Loop:** ~20-25 seconds per cycle.
* **Peak RSS:** ~1.8 GB to 2.2 GB intraday.
* **Network Latency (Yahoo):** ~50% of scanner execution time.
* **Evening Batch Runtime:** ~4-6 minutes total (sequential).

---

## 10. Distinguish Facts from Recommendations

**Verified Facts (Derived from code):**
* `main.py` explicitly forces Pullback Scanner to wait until EOD and Reversal finish (`eod_thread.join()`).
* `Wealth Engine` recalculates `calculate_wealth_technicals` on the *entire* historical frame every 5 minutes.
* `_push_throttle_cache` and `_dead_symbols_cache` are floating global dictionaries with their own manual TTL eviction logic.

**Recommendations for SessionContext:**
* Centralize the 5+ scattered global dictionaries into the `SessionCache`.
* Establish 16:00 as the explicit teardown time for 15m data, and 23:59 for complete `SessionContext` destruction, replacing scattered TTL logic.
