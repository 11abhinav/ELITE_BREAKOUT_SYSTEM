# LIVE FUNDAMENTAL ENTRY + EXIT IMPLEMENTATION PROOF

## ELITE BREAKOUT SYSTEM
**Strategy:** Fundamentally Strong Live Buy Scanner + Wealth Exit V1 (Primary) + Wealth Exit V2 (Shadow)  
**Governance Status:** CERTIFIED PRODUCTION SHADOW ALERT MODE  
**Git Commit / Reference SHA:** `35fe412d`  
**Execution Safety:** ALERT-ONLY DECISION SUPPORT — ABSOLUTE ZERO BROKER ORDER ROUTING  
**Date:** 2026-09-27  

---

## EXECUTIVE SUMMARY

This document provides formal, automated proof that the live dashboard implementation of the **Elite Breakout System** adheres strictly to the frozen, certified strategy specifications and governance rules without introducing uncertified logic, parameter tweaks, or score-based relaxations.

Key invariants proven:
1. **Mandatory Fundamental Gate:** A breakout chart alone CANNOT generate a BUY alert. Every candidate must pass hard Boolean filters for ROCE (≥ 15%), ROE (≥ 12%), Operating Cash Flow (> 0), Debt/Equity (≤ 1.0), and 4-point Earnings Acceleration before technical gates are evaluated.
2. **Approved Equity Universe:** Exactly 886 certified clean Indian equities are eligible. 41 corporate action anomaly stocks remain strictly quarantined.
3. **Primary Exit Control:** `WEALTH_EXIT_V1` is the sole user-facing exit decision authority.
4. **Shadow Research Tracker:** `WEALTH_EXIT_V2` evaluates compound weakness in parallel for prospective research only; it has zero authority to close user positions.
5. **No Broker Order Routing:** The system possesses zero broker API endpoints, zero order placement methods, and zero automatic buying or selling (`AUTOMATIC_BROKER_ORDERS = False`).
6. **CMP Reference Pricing:** Dashboard closure occurs at current market price (`dashboard_exit_cmp_reference`) and is strictly decoupled from certified historical T+1 backtest prices and actual broker execution prices (`user_actual_exit_price`).
7. **Market-Hour Confinement:** Exit monitoring executes strictly from 09:00 to 16:00 IST on exchange trading sessions. Outside hours, the monitor is inactive.
8. **Intraday Unfinished Candle Protection:** An incomplete daily candle cannot trigger completed daily-close exit rules.

---

## A. APPROVED UNIVERSE AUDIT PROOF

| Attribute | Specification | Implementation File | Verification Test |
| :--- | :--- | :--- | :--- |
| **Total Universe** | 927 Equities | `data/certified_clean_universe_886.json` & `quarantined_anomaly_symbols_41.json` | `test_live_fundamental_entry_exit.py` |
| **Certified Clean Universe** | Exactly 886 Equities | `app/live_fundamental_scanner.py:ApprovedUniverseRegistry` | `validate_live_fundamental_entry_exit.py` (Check 2) |
| **Quarantined Anomaly Stocks** | Exactly 41 Equities | `app/live_fundamental_scanner.py:ApprovedUniverseRegistry` | `validate_live_fundamental_entry_exit.py` (Check 2) |
| **Quarantine Policy** | Immediate Rejection | `app/live_fundamental_scanner.py:validate_symbol` | RejectionReason `EXCLUDED_QUARANTINED_ANOMALY` |
| **Unapproved Symbol Policy**| Immediate Rejection | `app/live_fundamental_scanner.py:validate_symbol` | RejectionReason `EXCLUDED_UNAPPROVED_UNIVERSE` |

### Quarantined 41 Anomaly Stocks List:
`AARTIPHARM`, `AGI`, `ALIVUS`, `ANGELONE`, `ARVIND`, `ASIANENE`, `BECTORFOOD`, `BOROLTD`, `CANBK`, `CNL`, `HOMEFIRST`, `IIFL`, `INDIAGLYCO`, `INDIGOPNTS`, `JASH`, `KAPSTON`, `KFINTECH`, `LATENTVIEW`, `MARINE`, `MAZDOCK`, `MEDANTA`, `MEDPLUS`, `PNB`, `POCL`, `POLICYBZR`, `PRIVISCL`, `QUESS`, `RAINBOW`, `RAJRATAN`, `SANOFI`, `SIEMENS`, `SKFINDIA`, `SPLPETRO`, `STAR`, `TEMBO`, `THOMASCOOK`, `TRIVENI`, `TVSHLTD`, `VEDL`, `VRLLOG`, `WEALTH`.

---

## B. FUNDAMENTAL QUALITY & ACCELERATION GATE PROOF

| Gate Rule | Threshold | Code Implementation | Failure Code | Automated Test |
| :--- | :--- | :--- | :--- | :--- |
| **ROCE** | `ROCE >= 15.0%` | `app/live_fundamental_scanner.py:FundamentalQualityGate` | `FAIL_ROCE` | `test_entry_case_2_roce_fails_plus_breakout_no_buy` |
| **ROE** | `ROE >= 12.0%` | `app/live_fundamental_scanner.py:FundamentalQualityGate` | `FAIL_ROE` | `test_entry_case_3_roe_fails_plus_breakout_no_buy` |
| **Operating Cash Flow** | `OCF > 0.0` | `app/live_fundamental_scanner.py:FundamentalQualityGate` | `FAIL_OCF` | `test_entry_case_4_ocf_fails_plus_breakout_no_buy` |
| **Debt / Equity** | `D/E <= 1.0` | `app/live_fundamental_scanner.py:FundamentalQualityGate` | `FAIL_DEBT_EQUITY` | `test_entry_case_5_debt_equity_fails_plus_breakout_no_buy` |
| **Revenue YoY Accel.** | `Rev_YoY[T] > Rev_YoY[T-1]` | `app/live_fundamental_scanner.py:EarningsAccelerationGate` | `FAIL_REVENUE_ACCELERATION` | `test_entry_case_6_revenue_acceleration_fails_plus_breakout_no_buy` |
| **Operating Profit Accel.**| `OpProfit_YoY[T] > OpProfit_YoY[T-1]` | `app/live_fundamental_scanner.py:EarningsAccelerationGate` | `FAIL_OP_PROFIT_ACCELERATION` | `test_entry_case_7_operating_profit_acceleration_fails_plus_breakout_no_buy` |
| **EPS YoY Accel.** | `EPS_YoY[T] > EPS_YoY[T-1]` | `app/live_fundamental_scanner.py:EarningsAccelerationGate` | `FAIL_EPS_ACCELERATION` | `test_entry_case_8_eps_acceleration_fails_plus_breakout_no_buy` |
| **Prior Base EPS** | `Prior_EPS > 0.0` | `app/live_fundamental_scanner.py:EarningsAccelerationGate` | `FAIL_PRIOR_EPS` | `test_entry_case_9_prior_eps_non_positive_fails_plus_breakout_no_buy` |
| **Data Completeness** | All fields present | `app/live_fundamental_scanner.py:FundamentalQualityGate` | `FUNDAMENTAL_DATA_MISSING` | `test_entry_case_14_missing_fundamental_no_buy` |
| **Data Freshness** | Unexpired, non-stale | `app/live_fundamental_scanner.py:scan_candidate` | `FUNDAMENTAL_DATA_STALE` | `test_entry_case_15_stale_fundamental_no_buy` |
| **Data Lineage** | Verified Provenance | `app/live_fundamental_scanner.py:scan_candidate` | `FUNDAMENTAL_PROVENANCE_INVALID` | `test_entry_case_16_invalid_fundamental_provenance_no_buy` |

---

## C. TECHNICAL ENTRY & BREAKOUT GATES PROOF

| Gate Rule | Threshold | Code Implementation | Failure Code | Automated Test |
| :--- | :--- | :--- | :--- | :--- |
| **Moving Average Trend** | `Close > SMA50 > SMA200` | `app/live_fundamental_scanner.py:TechnicalTrendGate` | `FAIL_TREND` | `test_entry_case_10_trend_fails_plus_breakout_no_buy` |
| **Relative Strength 3M** | `Stock_3M_Ret > Benchmark_3M` | `app/live_fundamental_scanner.py:TechnicalTrendGate` | `FAIL_RELATIVE_STRENGTH` | `test_entry_case_11_relative_strength_fails_plus_breakout_no_buy` |
| **Relative Strength 6M** | `Stock_6M_Ret > Benchmark_6M` | `app/live_fundamental_scanner.py:TechnicalTrendGate` | `FAIL_RELATIVE_STRENGTH` | `test_entry_case_11_relative_strength_fails_plus_breakout_no_buy` |
| **Consolidation Window** | `20 <= Window <= 60` | `app/live_fundamental_scanner.py:ConsolidationGate` | `FAIL_CONSOLIDATION_WINDOW` | `validate_live_fundamental_entry_exit.py` (Check 5) |
| **Consolidation Drawdown**| `Window_Drawdown <= 15.0%` | `app/live_fundamental_scanner.py:ConsolidationGate` | `FAIL_CONSOLIDATION_DRAWDOWN` | `test_entry_case_12_consolidation_fails_plus_breakout_no_buy` |
| **Consolidation Volatility**| `ATR14 / Close <= 6.0%` | `app/live_fundamental_scanner.py:ConsolidationGate` | `FAIL_CONSOLIDATION_ATR` | `validate_live_fundamental_entry_exit.py` (Check 5) |
| **Structural Support** | `Close[t] >= SMA200[t]` | `app/live_fundamental_scanner.py:ConsolidationGate` | `FAIL_CONSOLIDATION_SMA200` | `validate_live_fundamental_entry_exit.py` (Check 5) |
| **20D Price Breakout** | `Close > max(High[T-20:T])` | `app/live_fundamental_scanner.py:BreakoutGate` | `FAIL_BREAKOUT_PRICE` | `test_entry_case_13_breakout_fails_no_buy` |
| **Volume Surge** | `Volume >= 1.5 * VolSMA20` | `app/live_fundamental_scanner.py:BreakoutGate` | `FAIL_BREAKOUT_VOLUME` | `validate_live_fundamental_entry_exit.py` (Check 5) |
| **Breakout Extension** | `Extension <= 8.0%` | `app/live_fundamental_scanner.py:BreakoutGate` | `FAIL_BREAKOUT_EXTENSION` | `validate_live_fundamental_entry_exit.py` (Check 5) |

---

## D. CANONICAL WEALTH_EXIT_V1 (PRIMARY MONITOR) PROOF

**Rules Hash:** `8bbdf26997d9bc662fb554d3bbd62ee46c6f780fc9304044ee78995a9cf2df62`  
**Location:** `app/live_wealth_monitor.py:CanonicalV1ExitEvaluator`  

### Exact Mathematical Formula:
1. **Structural Weakness (Condition 1):**
   - $Close[T] < SMA50[T]$ AND $Close[T-1] < SMA50[T-1]$
   - OR
   - $Close[T] < \min(Close[T-20 : T])$
2. **Secondary Confirmation (Condition 2 on session T):**
   - $SMA50[T] \le SMA50[T-5]$ (Slope Down/Flat)
   - OR
   - $Relative\_Return_{10D} \le -5.0\%$
   - OR
   - $Distribution\_Days_{10D} \ge 2$ ($Close < Open$ with $Volume \ge 1.5 \times VolSMA20$)
3. **Exit Trigger:**
   - $Structural\_Weakness$ AND $Secondary\_Confirmation$

---

## E. CANONICAL WEALTH_EXIT_V2 (SHADOW TRACKER) PROOF

**Rules Hash:** `be1816bc8d8e0ca45f65fb0a7ce5cb42a4253a6d9b935408a0d783aa803ec29a`  
**Location:** `app/live_wealth_monitor.py:CanonicalV2ExitEvaluator`  

### Exact Mathematical Formula:
1. **Structural Weakness:**
   - 2 consecutive closes below prior 20-day low: $Close[T] < Low20[T]$ AND $Close[T-1] < Low20[T-1]$
   - OR 2 consecutive closes below SMA50.
2. **Compound Secondary Confirmation:**
   - $Relative\_Return_{10D} \le -5.0\%$ AND $Stock\_Return_{10D} < 0.0$
   - PLUS either ($SMA50\_Slope \le 0$ OR $Distribution\_Days_{10D} \ge 2$).
3. **Non-Interference Rule:**
   - Evaluated strictly in parallel. V2 has zero authority to close user positions (`test_exit_case_5_v1_hold_plus_v2_exit_user_hold_v2_shadow_only`).

---

## F. POSITION STATE MACHINE & CMP REFERENCE PROOF

```text
       ┌───────────────┐
       │   BUY_ALERT   │ (Generated when 6 mandatory entry gates pass)
       └───────┬───────┘
               │ (User executes independently through broker & records entry)
               ▼
       ┌───────────────┐
       │ POSITION_OPEN │ (Tracked by live V1 Primary Monitor + V2 Shadow)
       └───────┬───────┘
               │ (V1 triggers official exit signal during market hours)
               ▼
       ┌───────────────┐
       │  EXIT_ALERT   │ (Live user-facing exit instruction generated)
       └───────┬───────┘
               │ (Dashboard position closed immediately at CMP reference)
               ▼
       ┌───────────────┐
       │    CLOSED     │ (No duplicate alerts; future breakout gets new position_id)
       └───────────────┘
```

### Price Field Separation:
* `dashboard_exit_cmp_reference`: Current market price at the moment V1 triggers.
* `user_actual_exit_price`: Nullable; populated only if the user manually inputs their broker fill.
* `certification_reference_exit_price`: Preserved separately for backtest comparison.

---

## G. MARKET-HOUR & CALENDAR CONFINEMENT PROOF

| Time (IST) | Market Status | Exit Monitor State | Evaluation Result |
| :--- | :--- | :--- | :--- |
| **08:59** | CLOSED | `PRE_MARKET_INACTIVE` | Scans blocked; zero alerts |
| **09:00** | OPEN | `MARKET_OPEN` | Monitor active |
| **09:01** | OPEN | `ACTIVE` | Normal polling |
| **12:00** | OPEN | `ACTIVE` | Normal polling |
| **15:59** | OPEN | `ACTIVE` | Normal polling |
| **16:00** | OPEN | `ACTIVE` | Closing boundary scan |
| **16:01** | CLOSED | `POST_MARKET_INACTIVE` | Scans blocked; zero alerts |
| **Saturday / Sunday** | CLOSED | `WEEKEND_CLOSED` | Scans blocked; zero alerts |
| **NSE Holiday** | CLOSED | `EXCHANGE_HOLIDAY` | Scans blocked; zero alerts |

---

## H. BROKER ISOLATION PROOF

A full static and dynamic audit of `app/live_wealth_monitor.py` and `app/live_fundamental_scanner.py` proves:
* `AUTOMATIC_BROKER_ORDERS = False`
* Zero imports of broker trading modules (`fyers_api`, `kiteconnect`, `upstox_client.OrderApi`, etc.)
* Zero methods for order execution (`place_order`, `submit_order`, `modify_order`, `cancel_order`)
* Total Broker Orders Placed across all test batteries: **0 (Zero)**

---

## I. AUTOMATED TEST SUITE EXECUTION RESULTS

Command: `./venv/bin/pytest -v tests/test_live_fundamental_entry_exit.py`
```text
tests/test_live_fundamental_entry_exit.py::test_entry_case_1_fundamentally_strong_plus_breakout_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_2_roce_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_3_roe_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_4_ocf_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_5_debt_equity_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_6_revenue_acceleration_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_7_operating_profit_acceleration_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_8_eps_acceleration_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_9_prior_eps_non_positive_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_10_trend_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_11_relative_strength_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_12_consolidation_fails_plus_breakout_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_13_breakout_fails_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_14_missing_fundamental_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_15_stale_fundamental_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_16_invalid_fundamental_provenance_no_buy PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_17_all_gates_pass_exactly_one_buy_alert PASSED
tests/test_live_fundamental_entry_exit.py::test_entry_case_18_repeated_scan_no_duplicate_buy_alert PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_1_healthy_winner_v1_hold PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_2_temporary_weakness_v1_hold PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_3_exact_v1_exit_generates_exit_alert PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_4_v1_exit_plus_v2_hold_user_exit PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_5_v1_hold_plus_v2_exit_user_hold_v2_shadow_only PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_6_both_exit_one_user_exit PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_7_repeated_v1_exit_condition_one_exit_event PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_8_missing_data_no_fabricated_exit PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_9_stale_data_anomaly_no_fabricated_exit PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_10_closed_position_no_further_exit_alerts PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_11_restart_with_open_position_restored PASSED
tests/test_live_fundamental_entry_exit.py::test_exit_case_12_intraday_unfinished_candle_no_false_exit PASSED
tests/test_live_fundamental_entry_exit.py::test_market_hours_battery PASSED
tests/test_live_fundamental_entry_exit.py::test_zero_broker_routing_proof PASSED
============================== 32 passed in 1.61s ==============================
```

---

## J. REPRODUCIBLE VALIDATION COMMAND
The final reproducible validation command:
```bash
python scripts/validate_live_fundamental_entry_exit.py
```
**Result:** `PASS` (All 13 validation batteries verified).
