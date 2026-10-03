# FORENSIC SYSTEM AUDIT REPORT
## ELITE BREAKOUT SYSTEM — PRODUCTION SCANNERS & EXIT MONITORS AUDIT

**Date:** October 3, 2026  
**System Version:** v1.0 Production  
**Status:** Certified & Active  
**Audit Purpose:** Comprehensive forensic review of data ingestion, scanner execution, alert generation, database persistence, position monitoring, and exit execution.

---

## 1. SYSTEM ARCHITECTURE & GOVERNANCE INVARIANTS

1. **Timezone Invariant:** All server operations, cron triggers, telemetry logs, and SQL timestamps operate strictly in **Asia/Kolkata (IST / UTC+5:30)**.
2. **Currency Invariant:** All financial values (Revenue, Net Profit, Debt, Equity, Market Cap) are denominated in **Indian Rupee (INR / ₹ / Cr)**.
3. **Data Integrity Invariant:** The system operates under a **Fail-Closed Policy**. Missing data, unverified provenance, or Live vs Local filing divergence exceeding **15%** triggers a `DATA_CONFLICT` / `DATA_BLOCKED` state, hard-blocking alert generation.
4. **Sequential Execution Invariant:** All major scans (`DAILY_BUILDER`, `TECHNICAL`, `FUNDAMENTAL`, `QUALITY_COMPOUNDER`, `QUALITY_VALUE_RECOVERY`) execute sequentially using dedicated in-memory locks to prevent CPU/memory resource contention.

---

## 2. DATA INGESTION & RECONCILIATION PIPELINE (HOW VALUES ARE FETCHED)

```text
                                MARKET DATA PROVIDERS
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼                             ▼                             ▼
       UPSTOX API                     NSE XBRL API             CERTIFIED LOCAL FILINGS
 (Real-time Exchange Feed)         (Official Exchange Filings)      (pit_raw_filings/*.json)
            │                             │                             │
            └─────────────────────────────┼─────────────────────────────┘
                                          ▼
                         FUNDAMENTAL SOURCE ROUTER
                 (app/data_providers/fundamental_source_router.py)
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
       DUAL-SOURCE RECONCILIATION                    FAIL-CLOSED GOVERNANCE GATE
   • Compare Live vs Local (Rev & PAT)               • If Divergence > 15% -> DATA_CONFLICT
   • Tolerance Threshold: <= 5%                      • Hard Block Candidate Selection
                  │
                  ▼
         CANONICAL PIT PARQUET
   (data/canonical_pit_rebuilt.parquet)
```

### 2.1 Fetching Mechanism
* **Price & OHLCV Data:** Sourced directly from Upstox Historical Data API and local Parquet history (`data/history/1d/*.parquet`).
* **Financial Data Routing:** Sourced dynamically via `FundamentalSourceRouter`:
  1. **Upstox API:** Consolidated annual and quarterly financials.
  2. **NSE XBRL API:** Audited exchange filings.
  3. **Certified Local Raw Filings:** Validated historical Parquet/JSON cache.

### 2.2 Point-in-Time (PIT) Normalization
* **Share Count & Cash Resolution:** Net Debt and EV/EBITDA calculations use point-in-time share counts and cash/equivalents. If cash or shares are missing/unverifiable, `EV_EBITDA` status is marked as `DATA_INSUFFICIENT_VALUATION`.
* **CAGR Calculation:** Calculated using `compute_cagr_pit` over strict 5-year annual filing windows. Non-positive base periods return `-999.0` (flagged as non-positive base).

---

## 3. ALL ACTIVE PRODUCTION SCANNERS (HOW ALERTS GENERATE & ARE SAVED)

| Scanner Name | Execution Trigger | Primary Focus | Strategy Type | Target Table |
| :--- | :--- | :--- | :--- | :--- |
| **`DAILY_BUILDER`** | Daily 05:00 IST | Watchlist & Metric Construction | Universe Preparation | `elite_fundamental_watchlist.parquet` |
| **`TECHNICAL`** | Daily 18:15 IST | Multi-Pattern Technical Breakouts | Technical Swing (`BULL`) | `alerts`, `alerts_master` |
| **`FUNDAMENTAL`** | Daily 18:30 IST | Quality + Growth + 20D Breakout | Fundamental Breakout (`ALL`) | `alerts`, `wealth_buy_alert` |
| **`QUALITY_COMPOUNDER`** | Daily 17:00 IST | Quality × Value Compounder | Secular Growth (`ALL`) | `alerts`, `wealth_buy_alert` |
| **`QUALITY_VALUE_RECOVERY`** | Daily 17:15 IST | Undervalued Mean-Reversion | Model E3 Recovery (`ALL`) | `alerts`, `wealth_buy_alert` |
| **`FILING_WATCHER`** | Periodic (08:00, 16:30, 21:00) | Corporate Filings Watcher | Filing Freshness | `corporate_filings_freshness` |

---

### 3.1 Scanner Breakdown & Alert Generation Details

#### 1. `DAILY_BUILDER` (Watchlist Builder)
* **File:** `app/daily_builder.py`
* **How Values Are Fetched:** Scans NSE equity universe (886 symbols), fetches historical OHLCV, calculates 5Y financial metrics from PIT parquet.
* **Alert Generation & Storage:** Does not emit trade alerts directly; builds certified fundamental watchlist Parquet (`data/elite_fundamental_watchlist.parquet`) used by downstream scanners.

#### 2. `TECHNICAL` (Multi-Pattern Technical Scanner)
* **File:** `app/technical_scanner.py`
* **How Values Are Fetched:** Sourced from 1-day Parquet price history. Evaluates 20D/50D/200D SMA, Relative Strength vs Nifty 50, Volume Ratio.
* **Alert Types Emitted:**
  - **`DAILY_BREAKOUT` (EOD Breakout):** 20-day high breakout + Volume > 1.5x 20MA + RS > 0.
  - **`BREAKOUT_REVERSAL` (Reversal):** Key level reversal from 50D/200D support with volume thrust.
  - **`WEEKLY_MULTI_TF` (Multi-TF):** Alignment across daily and weekly timeframe breakout channels.
* **How Alerts Are Saved:** Saved into PostgreSQL `alerts` table with `record_type = 'ALERT_EVENT'`, `status = 'OPEN'`, and `idempotency_key = '{symbol}_{scanner}_{date}'`.

#### 3. `FUNDAMENTAL` (Fundamental Breakout Scanner)
* **File:** `app/live_fundamental_scanner.py`
* **How Values Are Fetched:** Reads certified `data/canonical_pit_rebuilt.parquet`.
* **Gate Cascade:**
  1. `ROCE_5Y_AVG >= 15.0%`
  2. `SALES_CAGR_5Y >= 10.0%` AND `PAT_CAGR_5Y >= 10.0%`
  3. `DEBT_TO_EQUITY <= 0.5`
  4. 20-day price breakout (`Close >= 20D High`)
* **How Alerts Are Saved:** Saved into `alerts` table and `wealth_buy_alert` table with `status = 'OPEN'`.

#### 4. `QUALITY_COMPOUNDER` (Quality Compounder Scanner)
* **File:** `app/live_fundamental_scanner.py`
* **How Values Are Fetched:** Sourced from `data/canonical_pit_rebuilt.parquet`.
* **Gate Cascade:**
  1. `ROCE_5Y_AVG >= 15.0%`
  2. Growth Acceleration: `Trailing 3Q PAT Growth > 5Y PAT CAGR`
  3. `DEBT_TO_EQUITY <= 0.35`
  4. Valuation: `EV/EBITDA <= 25.0` or PEG ratio <= 1.5
* **How Alerts Are Saved:** Saved into `alerts` table (`scanner = 'QUALITY_COMPOUNDER'`) and `wealth_buy_alert`.

#### 5. `QUALITY_VALUE_RECOVERY` (Model E3 Quality Value Recovery Scanner)
* **File:** `app/live_fundamental_scanner.py`
* **How Values Are Fetched:** Sourced from `data/canonical_pit_rebuilt.parquet`.
* **Gate Cascade (Model E3 Entry Rules):**
  1. `ROCE_5Y_AVG >= 12.0%` (Quality floor)
  2. `DEBT_TO_EQUITY <= 0.8` (Solvency floor)
  3. **Valuation Discount Gate:** `Current EV/EBITDA <= 0.85 * 3Y Median EV/EBITDA` (Minimum 15% valuation discount)
  4. Technical Catalyst: Price within 5% of 50D/200D support or early 20D breakout.
* **How Alerts Are Saved:** Saved into `alerts` table (`scanner = 'QUALITY_VALUE_RECOVERY'`) and `wealth_buy_alert`.

---

## 4. ALL EXIT MONITORS (HOW THEY ARE MONITORED & HOW THEY EXIT)

```text
                                ACTIVE TRADES & ALERTS
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼                             ▼                             ▼
   PERFORMANCE_TRACKER              WEALTH_EXIT_V1                WEALTH_EXIT_V2
 (Technical Exit Monitor)     (Wealth Intraday Monitor)      (Model E3 Dual-Pulse Guard)
  • Intraday 5m loop           • Intraday 5m loop             • 15:15 IST Pre-Close Pulse
  • Monitors Technical         • Monitors Portfolio           • 18:30 IST EOD Final Exit
    Swing Alerts                 Holdings (PnL & CMP)         • Evaluates E3 4-Gate Rules
```

---

### 4.1 Exit Monitor 1: `PERFORMANCE_TRACKER`
* **Display Name:** `Exit Monitor — Technical (EOD, REVERSAL, MULTI_TF)`
* **System ID:** `PERFORMANCE_TRACKER`
* **Monitored Scanners:** `DAILY_BREAKOUT` (EOD), `BREAKOUT_REVERSAL`, `WEEKLY_MULTI_TF`
* **Execution Cadence:** Every 5 minutes during market hours (09:15–15:30 IST)
* **How Monitored:** Queries `alerts` table for `status IN ('OPEN', 'ACTIVE')` where `scanner IN ('TECHNICAL', 'DAILY_BREAKOUT', 'BREAKOUT_REVERSAL', 'WEEKLY_MULTI_TF')`. Fetches real-time price tick via `live_prices.py`.
* **Exit Rules:**
  1. **Stop Loss Hit:** Price $\le$ `entry_price * (1 - SL_PCT)` (e.g. 5% or 7% SL) $\rightarrow$ Exit `STOP_LOSS`.
  2. **Target 1 Hit:** Price $\ge$ `Target_1` $\rightarrow$ Transition state `PARTIAL_WIN_1` (Locks 50% profit, trails SL to entry).
  3. **Target 2 Hit:** Price $\ge$ `Target_2` $\rightarrow$ Exit `TARGET_2_HIT` (Closed).
  4. **Trailing SL / Channel Exit:** Close below 20-day Low or trailing ATR stop $\rightarrow$ Exit `TRAILING_SL_HIT`.
* **How Exited:** Updates `alerts` table status to `CLOSED`, sets `exit_price`, `exit_time`, `exit_reason`, and calculates net trade R-multiples.

---

### 4.2 Exit Monitor 2: `WEALTH_EXIT_V1`
* **Display Name:** `Exit Monitor V1 — Wealth (COMPOUNDER, QUALITY_RECOVERY)`
* **System ID:** `WEALTH_EXIT_V1`
* **Monitored Scanners:** `QUALITY_COMPOUNDER`, `QUALITY_VALUE_RECOVERY`, `FUNDAMENTAL`
* **Execution Cadence:** Every 5 minutes during market hours (09:15–15:30 IST)
* **How Monitored:** Queries `manual_portfolio` and `wealth_buy_alert` for open positions. Fetches real-time CMP for open symbols.
* **Intraday Role:**
  1. Updates real-time position prices (`update_position_real_time_prices`).
  2. Recalculates unrealized PnL % and hold scores.
  3. Monitors open target hits.
* **How Exited:** If an intraday target is hit, updates position state in `wealth_buy_alert` and logs execution history into `scan_execution_history`.

---

### 4.3 Exit Monitor 3: `WEALTH_EXIT_V2`
* **Display Name:** `Exit Monitor V2 — Model E3 (COMPOUNDER, QUALITY_RECOVERY)`
* **System ID:** `WEALTH_EXIT_V2`
* **Monitored Scanners:** `QUALITY_COMPOUNDER`, `QUALITY_VALUE_RECOVERY`
* **Execution Cadence:** Dual-Pulse Daily:
  - **15:15 IST:** Pre-Close Warning Pulse
  - **18:30 IST:** EOD Audited Final Exit Pulse
* **How Monitored:** Sourced from `live_wealth_monitor.py`. Queries `alerts` for active compounder/recovery trades.

#### Exit Execution Rules:

##### A. For `QUALITY_VALUE_RECOVERY` (Model E3 4-Gate Exit Evaluator):
1. **Gate 1 — Technical SMA200 Break / Stop Loss:**
   - 2 consecutive daily closes below 200-DMA (`close_t < sma200_t` AND `close_t_prev < sma200_t_prev`) $\rightarrow$ Exit `SMA200_BREAK`.
   - OR Price breaches 10% hard stop loss (`close_t <= 0.90 * entry_price`) $\rightarrow$ Exit `STOP_LOSS_10PCT_HIT`.
2. **Gate 2 — Valuation Mean-Reversion Re-Rating:**
   - Current EV/EBITDA reaches/exceeds 3-year median EV/EBITDA (`current_ev_ebitda >= ev_ebitda_3y_median`) $\rightarrow$ Exit `VALUATION_RE_RATED` (Mean-reversion thesis complete!).
3. **Gate 3 — PAT Growth Deceleration:**
   - Trailing 3-quarter PAT growth drops negative (`pat_growth_trailing_3q < 0.0`) $\rightarrow$ Exit `PAT_DECELERATION`.
4. **Gate 4 — Holding Window Expiry:**
   - Holding time reaches 10 trading sessions (~14 calendar days) $\rightarrow$ Exit `MAX_HOLDING_EXPIRED`.

##### B. For `QUALITY_COMPOUNDER` (Secular Quality Exit Evaluator):
1. **Technical Break:** 2 consecutive daily closes < SMA200 $\rightarrow$ Exit `SMA200_BREAK`.
2. **Fundamental Deterioration:** ROCE drops below 10.0% OR drops by >25% relative to initial 5Y ROCE baseline $\rightarrow$ Exit `FUNDAMENTAL_DETERIORATION`.

#### How Exited:
1. **15:15 IST Warning:** Saves `EXIT_CHECK_PRE_CLOSE` event into `v2_exit_events` and marks state as `ORANGE`.
2. **18:30 IST Final Exit:** Saves `FINAL_EXIT` event, marks state as `RED` (`CLOSED`), records `exit_reason`, and updates alert record in database.

---

## 5. AUDIT VERIFICATION & GOVERNANCE SUMMARY

| Audit Metric | Verdict | Status |
| :--- | :--- | :--- |
| **Data Provenance** | Upstox API + Certified Local Parquet | **CERTIFIED** |
| **Causality & Point-in-Time** | Strict No-Lookahead Window | **VERIFIED** |
| **Sequential Execution Locking** | In-Memory Mutex (`_acquisition_lock`) | **ACTIVE** |
| **Fail-Closed Gate** | Divergence > 15% -> `DATA_CONFLICT` | **ENFORCED** |
| **Decommissioned Scanners & Workers** | `AI Worker` & `Pledge Worker` Removed | **PURGED** |
| **Telemetry Audit Trail** | Logged to `scan_execution_history` & `scanner_health` | **100% COMPLETE** |

---
*End of Forensic System Audit Report.*
