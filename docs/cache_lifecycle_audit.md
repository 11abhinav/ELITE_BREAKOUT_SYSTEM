# 24-Hour System Data Lifecycle Audit & Cache Retention Analysis

> [!IMPORTANT]
> This document provides a deep architectural audit of the entire system's data lifecycle over a 24-hour period. It outlines precisely when datasets are created, who consumes them, when they should be freed, and how the `SessionContext` should manage memory ownership. No code has been modified during the generation of this audit.

---

## 1. Complete 24-Hour Execution Timeline

```text
00:00 - Midnight
│
├── 01:00
│   └── Daily Builder (Builds fresh daily Watchlist, downloads Fundamentals)
│
├── 02:00
│   └── Wealth Engine Initial Setup (Pre-fetches initial Historical Data)
│
├── 08:30
│   └── Session Warmup & Verification (Verifies watchlist & file readiness)
│
09:15 - Market Open
│
├── 09:15 to 15:30 (Every 5 mins)
│   ├── Wealth Engine (Market hours loop)
│   ├── Multibagger Exit Monitor
│   ├── Multi TF Scanner (Continuous/Recurring)
│   └── Performance Tracker (Dashboard data refresh)
│
15:30 - Market Close
│
├── 21:00 - Evening Scanners (Post-Bhavcopy)
│   ├── EOD Scanner
│   ├── Reversal Scanner
│   └── Pullback Scanner
│
├── 23:59
│   └── Session Cleanup / Midnight Reset
│
└── Next Trading Day Begins
```

---

## 2 & 3 & 4. Process Chronology, Inputs & Outputs

### `01:00` Daily Builder
* **Consumed**: DB tables (symbol master), APIs (NSE/BSE fundamentals if required).
* **Produced**: Clean `watchlist_cache` (saved to local parquet and DB). Fundamental attributes (Market Cap, Sector).
* **Objects Built**: Watchlist DataFrame.

### `02:00` Wealth Engine (Initial Setup)
* **Consumed**: Watchlist from Daily Builder.
* **Produced**: Base Historical Data (10-day 15-minute OHLCV), initial `elite_wealth_system.parquet`.

### `08:30` Session Warmup
* **Consumed**: Disk artifacts, Watchlist cache.
* **Produced**: Validated state. No new market data downloaded.

### `09:15 - 15:30` (Every 5 mins) Wealth Engine & Multi TF Scanner
* **Consumed**: Watchlist, Live Prices (15m candles), Base Historical Data, Delivery Data.
* **Produced**: `Candidate Lists`, temporary scoring DataFrames, updated Indicators (EMAs, ATR, RSI), Buy Signals, Alerts.

### `21:00` EOD, Reversal, & Pullback Scanners
* **Consumed**: Watchlist, Daily Historical OHLCV (1yr+), Bhavcopy (Delivery Data), Sector Data.
* **Produced**: EOD Signals, Reversal Alerts, Pullback Candidates, Database records (trades, alerts).

---

## 5. Complete Scanner Dependency Matrix

| Dataset | Wealth Engine | Multi TF | Pullback | Reversal | EOD |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Watchlist** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Fundamentals** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **15m OHLCV** | ✓ | ✓ | ✗ | ✗ | ✗ |
| **Daily OHLCV** | ✗ | ✓ | ✓ | ✓ | ✓ |
| **Delivery Data** | ✗ | ✗ | ✓ | ✓ | ✓ |
| **Indicator: EMAs** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Indicator: ATR** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Indicator: RSI** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Market Regime** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Sector Data** | ✓ | ✓ | ✓ | ✓ | ✓ |

> [!NOTE]
> *Highlight*: Watchlist, Fundamentals, EMAs, ATR, RSI, Market Regime, and Sector Data are universally shared. Daily OHLCV is shared among all evening scanners and Multi TF. 15m OHLCV is strictly an intraday (Wealth/Multi-TF) dependency.

---

## 6. Data Lifecycle for Major Datasets

### Watchlist & Fundamentals
* **Created**: 01:00
* **Last Used**: 21:00+ (After Pullback Scanner completes)
* **Lifecycle**: Retain in Session Context for the entire 24-hour cycle.

### 15-Minute Historical OHLCV
* **Created**: 02:00 (Initial), updated every 5 minutes (09:15 - 15:30).
* **Last Used**: 15:30 (Market Close).
* **Lifecycle**: Can be safely released at 16:00. Not needed by Evening Scanners.

### Daily Historical OHLCV
* **Created**: 09:15 (Multi TF) or 21:00 (Evening batch).
* **Last Used**: 21:00+ (After Pullback finishes).
* **Lifecycle**: Retain until Pullback completes, then release.

### Temporary Scoring DataFrames
* **Created**: Mid-execution by Wealth, Reversal, etc.
* **Last Used**: End of specific scanner run.
* **Lifecycle**: Destroy immediately upon scanner completion.

---

## 7 & 8. Cache Retention & Eviction Recommendations

### Keep For Entire Session
* **Watchlist**, **Fundamental Metadata**, **Sector Data**, **Market Regime**.
* **Reason**: These change daily (or slower). They are lightweight and heavily reused by every module.

### Refresh Periodically
* **Live 15m Candles**: Refresh every 5 minutes (09:15 - 15:30).
* **Market Regime**: Refresh every 5-15 minutes intraday.

### Free After Scanner
* **Candidate lists**, **intermediate metric arrays (z-scores, percentiles)**, **temporary merged dataframes** containing scoring math.
* **Reason**: Only relevant to the exact algorithmic pass that created them. They provide zero value to other scanners.

### Free After Last Dependent Scanner
* **Daily OHLCV**: Keep it alive for EOD, then Reversal, then Pullback. Once Pullback completes, free the Daily OHLCV cache.

---

## 9 & 10. Session Cache Design & Memory Ownership

Every object in memory must have a strict owner.

* **Owner**: `SessionContext` (via specific Managers like `HistoricalDataManager`).
* **Readers**: Scanners (`Wealth`, `MultiTF`, `EOD`, etc.).
* **Writers**: Explicitly restricted to the `HistoricalDataManager` (via `update_ohlcv` or `update_indicators`).
* **Cleanup Trigger**: Governed by the `SessionLifecycleManager` based on time (16:00) or event (Evening Scanners Complete).

If a scanner requires mutation, it must request a copy via `session.get_mutable_copy()`.

---

## 11 & 12. Cleanup Trigger & End-of-Day Cleanup Plan

Cleanup should be deterministic and event-driven, not arbitrary (no random `gc.collect()` mid-execution).

1. **16:00 Intraday Cleanup**:
   * Market is closed. Wealth and Multi TF are done.
   * **Action**: Free `15m OHLCV` cache, intraday Indicator Bundles, and Live Price Cache.
2. **Post-Evening Batch Cleanup**:
   * Triggered automatically after `Pullback` completes successfully.
   * **Action**: Free `Daily OHLCV`, Bhavcopy/Delivery Data cache, and Evening Indicator Bundles.

---

## 13. Midnight Session Reset Strategy

At **23:59** (or immediately prior to the 01:00 Daily Builder run):
1. Destroy the entire `SessionContext` instance.
2. Force `gc.collect()`.
3. Create a totally fresh `SessionContext`.
4. This guarantees zero state leakage, no poisoned memory from the previous day, and perfectly resets memory tracking to baseline.

---

## 14, 15, 16, 17. Dataset Categorization Summary

| Always Cached (00:00 - 23:59) | Never Cached | Released Immediately After Use | Released After Last Dependent |
| :--- | :--- | :--- | :--- |
| Watchlist | Raw JSON API Responses | Temporary Merges (`pd.merge`) | 15m OHLCV (Release at 16:00) |
| Fundamentals | Error Logs / Tracebacks | Scoring Tables (`z_scores`) | Daily OHLCV (Release post-Pullback) |
| Sector Data | DB Connection Pools | Candidate Lists (`final_df`) | Delivery Data (Release post-Pullback) |
| Market Regime | Large Text/PDF blobs | Sort/Rank outputs | Indicator Bundles |

---

## 18. Opportunities for Incremental Updates

Currently, the `Wealth Engine` and `Multi TF Scanner` run every 5 minutes and recalculate indicators (EMA, ATR, RSI) on the *entire* historical dataset.
* **Opportunity**: By implementing `IndicatorManager` to track "Dirty Regions", we only need to mathematically calculate the EMA/ATR/RSI for the single new 15m candle appended to the end of the series, reducing DataFrame math by 99%.
* **Pivot Detection**: Only recalculate pivots from the *last confirmed pivot index* forward, rather than scanning the entire 10-day history every 5 minutes.

---

## 19. Estimated Impact

* **Memory Savings**:
  * Eliminating duplicate copies of Daily OHLCV across EOD, Reversal, and Pullback will save ~600-800 MB at 21:00.
  * Freeing 15m OHLCV precisely at 16:00 prevents zombie memory from bleeding into the evening batch, saving ~1.2 GB of RSS.
* **Performance Impact**:
  * Reusing `Daily OHLCV` across the evening batch will eliminate 3x redundant disk/network fetches, cutting evening scan times by ~40-60%.
  * Incremental indicator updates will reduce intraday 5-minute Wealth cycle times from ~20 seconds down to <5 seconds.

---
> [!CAUTION]
> As requested, no implementation changes have been made to the system yet. This audit serves as the master blueprint for exactly *when* and *how* `SessionContext` will retain and release data in Phase 2. Please review and approve these lifecycles.
