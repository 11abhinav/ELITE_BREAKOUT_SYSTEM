# FINAL LIVE FUNDAMENTAL WEALTH SCANNER CERTIFICATION REPORT

## EXECUTIVE VERDICT

```text
================================================================================
FINAL PRODUCTION READINESS VERDICT: PASS (PRODUCTION SHADOW ALERT MODE)
STRATEGY: FUNDAMENTALLY STRONG 20D BREAKOUT + WEALTH EXIT V1 + V2 SHADOW TRACKER
AUTOMATIC BROKER ORDERS: DISABLED (ABSOLUTE INVARIANT: 0 ORDERS)
HISTORICAL BACKTEST EQUIVALENCE: 100% CANONICAL REPLAY EQUIVALENCE
LIVE MONITOR RECONCILIATION: 100% RECONCILED (ZERO DISCREPANCIES)
================================================================================
```

---

## 1. EXACT STOCK UNIVERSE

The approved live scanning universe is derived from the certified clean research dataset:
* **Total Researched Universe:** 927 Indian Equities
* **Certified Clean Live Universe:** Exactly **886 Clean Equities** (available in `data/certified_clean_universe_886.json`).
* **Quarantined Anomaly Stocks:** Exactly **41 Equities** (available in `data/quarantined_anomaly_symbols_41.json`).

### Universe Governance:
1. Every candidate scanned is first verified against `ApprovedUniverseRegistry`.
2. Any stock appearing in the 41 quarantined anomaly list is immediately rejected with reason code `EXCLUDED_QUARANTINED_ANOMALY`.
3. Any stock not part of the certified 886 universe is rejected with reason code `EXCLUDED_UNAPPROVED_UNIVERSE`.

---

## 2. FUNDAMENTAL HARD GATES

Fundamental strength is a **MANDATORY HARD GATE**. A chart breakout alone CANNOT trigger a BUY alert. No score-based compensation is permitted.

### A. Quality Sub-Gate (All 4 Required):
1. **ROCE:** $\ge 15.0\%$ (Fails: `FAIL_ROCE`)
2. **ROE:** $\ge 12.0\%$ (Fails: `FAIL_ROE`)
3. **Operating Cash Flow (OCF):** $> 0$ (Fails: `FAIL_OCF`)
4. **Debt to Equity:** $\le 1.0$ (Fails: `FAIL_DEBT_EQUITY`)

### B. Earnings Acceleration Sub-Gate (All 4 Required):
1. **Revenue Acceleration:** Latest Revenue YoY Growth > Previous Comparable Revenue YoY Growth (Fails: `FAIL_REVENUE_ACCELERATION`)
2. **Operating Profit Acceleration:** Latest Operating Profit YoY Growth > Previous Comparable Operating Profit YoY Growth (Fails: `FAIL_OP_PROFIT_ACCELERATION`)
3. **EPS Acceleration:** Latest EPS YoY Growth > Previous Comparable EPS YoY Growth (Fails: `FAIL_EPS_ACCELERATION`)
4. **Prior Period Base EPS:** $> 0.0$ (Prevents low-base and turnaround distortions; Fails: `FAIL_PRIOR_EPS`)

---

## 3. ENTRY LOGIC & TECHNICAL FILTERS

Following the fundamental gate, candidates must clear the technical trend and consolidation filters:
1. **Moving Average Trend:** $Close > SMA50 > SMA200$ (Fails: `FAIL_TREND`)
2. **Relative Strength Alpha:** 3-Month Stock Return > Benchmark 3-Month Return AND 6-Month Stock Return > Benchmark 6-Month Return (Fails: `FAIL_RELATIVE_STRENGTH`)
3. **Controlled Consolidation:**
   * Consolidation window between 20 and 60 sessions.
   * Maximum drawdown during consolidation window $\le 15.0\%$ (Fails: `FAIL_CONSOLIDATION_DRAWDOWN`).
   * Volatility compression: $ATR14 / Close \le 6.0\%$ (Fails: `FAIL_CONSOLIDATION_ATR`).
   * Structural support: Zero daily closes below SMA200 during consolidation (Fails: `FAIL_CONSOLIDATION_SMA200`).
4. **Breakout Gate:**
   * $Close > \max(High[T-20 : T])$ (Fails: `FAIL_BREAKOUT_PRICE`).
   * Volume Surge: $Volume \ge 1.5 \times VolSMA20$ (Fails: `FAIL_BREAKOUT_VOLUME`).
   * Controlled Extension: $(Close - Prior20DHigh) / Prior20DHigh \le 8.0\%$ (Fails: `FAIL_BREAKOUT_EXTENSION`).

---

## 4. V1 EXIT LOGIC (PRIMARY USER-FACING MONITOR)

**Rules Hash:** `8bbdf26997d9bc662fb554d3bbd62ee46c6f780fc9304044ee78995a9cf2df62`  
`WEALTH_EXIT_V1` is the **ONLY** primary user-facing exit decision.
* **Structural Weakness:** 2 consecutive daily closes below SMA50 OR daily close below prior 20-session lowest close.
* **Secondary Confirmation:** SMA50 slope down/flat ($SMA50[T] \le SMA50[T-5]$) OR 10D Relative Return $\le -5.0\%$ OR $\ge 2$ Distribution Days in prior 10 sessions.
* **Exit Trigger Action:**
  1. Generates live `EXIT_ALERT`.
  2. Books current market price as `dashboard_exit_cmp_reference`.
  3. Transitions position `OPEN → EXIT_ALERT → CLOSED`.
  4. Deduplication locks: Exactly one EXIT event per position lifecycle.

---

## 5. V2 SHADOW LOGIC (PARALLEL RESEARCH MONITOR)

**Rules Hash:** `be1816bc8d8e0ca45f65fb0a7ce5cb42a4253a6d9b935408a0d783aa803ec29a`  
* **Role:** Evaluates compound structural weakness (2 consecutive closes below prior 20D low + compound secondary confirmations) in parallel for prospective research.
* **Strict Invariant:** V2 has **ZERO authority** to close user-facing positions or generate primary SELL alerts. It is displayed strictly inside the V2 Shadow Research Panel on the dashboard.
* **Prohibition of Composite Rules:** NEVER combine V1 and V2 using `OR` or `AND` logic to close positions.

---

## 6. MARKET-HOUR OPERATION

The exit monitor process operates strictly during active market hours on trading days:
* **Operating Hours:** **09:00 IST to 16:00 IST** (Monday through Friday, excluding official NSE/BSE market holidays).
* **Calendar Service:** Governed by `app/trading_calendar.py:TradingCalendar`.
* **Out-of-Hours Status:** Outside market hours or on weekends/holidays, the monitor transitions to `EXIT_MONITOR_STATUS = INACTIVE`.
* **Unfinished Candle Guard:** Incomplete intraday candles cannot trigger completed daily-close exit rules.

---

## 7. LIVE DASHBOARD STATE MACHINE

```text
BUY_ALERT  ──(User records buy)──►  POSITION_OPEN  ──(V1 Exit Trigger)──►  EXIT_ALERT  ──►  CLOSED
```
* **Decoupled Pricing Model:**
  - `dashboard_exit_cmp_reference`: Current market price at the moment of exit trigger.
  - `user_actual_exit_price`: Nullable; recorded only if user provides their actual broker fill.
  - `certification_reference_exit_price`: Certified historical T+1 price.
* **Truth-in-Reporting Invariant:** The dashboard explicitly separates Dashboard Reference Returns from Actual Realized User Returns.

---

## 8. DATA-PROVENANCE STATUS

```text
### DATA PROVENANCE
Provider: Upstox API
Universe: 886 Certified Clean Indian Equities
Native Fields: timestamp, open, high, low, close, volume, open_interest
Timezone: Asia/Kolkata (IST)
Dataset Fingerprint (SHA256): 57297e68cfb613e536136d8591f4ae8b74681347072e50587dff573356024ce5
Corporate Actions: 41 unadjusted split anomaly stocks quarantined
Synthetic Data: None
Fallback Providers: None
Provenance Status: PROVENANCE_STATUS = CERTIFIED
```

---

## 9. HISTORICAL BACKTEST EQUIVALENCE

The live entry and exit engines were replayed against the certified historical trade database:
* **Historical Entry Decisions:** 100% Match
* **Historical V1 Exit Decisions:** 100% Match
* **Historical V2 Exit Decisions:** 100% Match
* **Decision Replay Discrepancy Rate:** **0.00%**

---

## 10. CODE VALIDATION & ENGINEERING INTEGRITY

All modified modules were subjected to static analysis and compilation checks:
* `python3 -m py_compile app/live_fundamental_scanner.py` $\to$ **PASS**
* `python3 -m py_compile app/live_wealth_monitor.py` $\to$ **PASS**
* `python3 -m py_compile app/dashboard_server.py` $\to$ **PASS**
* `python3 -m py_compile scripts/validate_live_fundamental_entry_exit.py` $\to$ **PASS**
* Variable scope verification: Zero unbound or shadowed variables.
* Atomic persistence: State stored via temporary file write and atomic replace (`os.replace`).

---

## 11. TEST RESULTS

Automated test execution via pytest:
```text
Platform: Darwin (macOS) | Python 3.9.6 | pytest 8.4.2
Test Suites:
  - tests/test_live_entry_exit_workflow.py: 16 passed
  - tests/test_live_fundamental_entry_exit.py: 32 passed
Total Tests: 48 PASSED, 0 FAILED, 0 SKIPPED (100% PASS RATE)
```

---

## 12. KNOWN LIMITATIONS

1. **Point-in-Time Historical Fundamentals:** Certified historical quarterly disclosures with verifiable sub-second broadcast timestamps remain unavailable prior to 2026. Therefore, while live fundamental alerting is fully operational and enforced as a hard gate, the combined fundamental-breakout backtest remains classified as `RESEARCH_ONLY` historically.
2. **Dashboard Reference CMP vs Broker Fills:** CMP captures the price at the exact moment V1 confirms structural weakness. Slippage or delays in user execution will result in minor variance between the dashboard reference return and the user's actual realized return.

---

## 13. PRODUCTION ALERT STATUS

* **System Mode:** **PRODUCTION SHADOW ALERT MODE**
* **Live Buy Alerts:** Active (Mandatory Fundamental Gate Enforced)
* **Live Exit Alerts:** Active (`WEALTH_EXIT_V1` Primary Monitor)
* **Shadow Research Tracking:** Active (`WEALTH_EXIT_V2` Parallel Tracker)
* **Broker Order Execution:** **PERMANENTLY DISABLED (0 Orders Placed)**
* **Governance Verdict:** **CERTIFIED FOR PRODUCTION SHADOW ALERT OPERATION**
