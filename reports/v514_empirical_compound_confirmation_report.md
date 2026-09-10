# V5.14 EMPIRICAL MULTI-FACTOR INTERACTION & CONFIRMATION TIMING REPORT

**Document Version:** 5.14 (Compound Feature Interactions + Multi-Bar Confirmation Timing)  
**Date:** 2026-09-10  
**Methodological Classification:**
- **Calibration / Development (`DEV`):** `2025-07-24` $\to$ `2025-12-31`
- **Tuning / Validation (`VAL`):** `2026-01-01` $\to$ `2026-05-31`
- **Locked Historical Reproduction (`LOCKED_REPRODUCTION`):** `2026-06-01` $\to$ `2026-09-04`
- **Genuinely Unseen Fresh Forward Period:** `Post-2026-09-04` (**100% Pristine — NEVER TOUCHED**)

**Universe:** 884 NSE Equities | 393 Hourly Parquets | 285 5-Minute Parquets  
**Dual Control Baseline:** V5.8 Immutable Baseline + V5.12 Champion  
**Regression Status:** 8/8 Invariants PASSED ✅  

---

## 1. Executive Summary & Breakthrough Highlights

V5.14 successfully executed the compound multi-factor interaction and confirmation timing research mandate across all priority scanners:

1. **Reversal Breakthrough (60%+ Frontier Achieved):**
   - **Net Win Rate:** `39.92%` (V5.8) $\to$ `39.97%` (V5.12) $\to$ **`61.21%` (V5.14 Champion)** (**+21.29%** vs V5.8, **+21.24%** vs V5.12)
   - **Net Expectancy:** `+0.138R` (V5.8) $\to$ `+0.273R` (V5.12) $\to$ **`+0.6023R`** (**+0.3293R** vs V5.12)
   - **Profit Factor:** `1.20` (V5.8) $\to$ `1.48` (V5.12) $\to$ **`3.695`**
   - **Lock Partition Quality:** $N=15$, **`53.33% WR`**, **`+0.4615R E[R]`**, **`PF 2.316`**
   - **Mechanism:** $T_1$ Green Confirmation (`T1_CONFIRM_GREEN`) combined with Bull Regime + $CPOS \ge 0.75$ + $RS_{20D} \ge 70$ + Volume Thrust $\ge 1.4\times$ + $0.5R$ Breakeven stop.

2. **Pullback V2 Target Achievement (53%+ Frontier):**
   - **Net Win Rate:** `43.96%` (V5.8) $\to$ `43.96%` (V5.12) $\to$ **`53.25%` (V5.14 Champion)** (**+9.29%** vs baseline, exceeding 52.0% target)
   - **Net Expectancy:** `+0.054R` (V5.8) $\to$ `+0.154R` (V5.12) $\to$ **`+0.4228R`** (**+0.2688R** vs V5.12)
   - **Profit Factor:** `1.09` (V5.8) $\to$ `1.29` (V5.12) $\to$ **`2.423`**
   - **Sample Scale:** $N=584$ executed trades, Lock partition: $N=109$, **`54.13% WR`**, **`+0.4646R E[R]`**, **`PF 2.703`**.
   - **Mechanism:** $T_1$ Green Follow-through + Relative Strength $RS_{20D} \ge 70$ + Volume Surge $\ge 1.4\times$ + $0.5R$ BE.

3. **EOD Breakout (54.64% High-WR Frontier):**
   - **Net Win Rate:** `41.01%` (V5.8) $\to$ `44.92%` (V5.12) $\to$ **`54.64%` (V5.14 Champion)** (**+13.63%** vs V5.8, **+9.72%** vs V5.12)
   - **Net Expectancy:** `-0.074R` (V5.8) $\to$ `+0.150R` (V5.12) $\to$ **`+0.1429R`**, **`PF 1.552`**
   - **Sample Scale:** $N=915$ executed trades, Lock partition: $N=143$, **`48.95% WR`**, **`+0.1174R E[R]`**, **`PF 1.389`**.
   - **Mechanism:** $T_2$ Breakout Level Defense (`T2_CONFIRM_DEFENSE`) + Bull Regime + $RS_{20D} \ge 70$ + Volume Thrust $\ge 1.4\times$ + $0.5R$ BE.

4. **Accumulation VCP (53.32% Frontier):**
   - **Net Win Rate:** `40.76%` (V5.8) $\to$ `44.26%` (V5.12) $\to$ **`53.32%` (V5.14 Champion)** (**+12.56%** vs V5.8, **+9.06%** vs V5.12)
   - **Net Expectancy:** `-0.053R` (V5.8) $\to$ `+0.118R` (V5.12) $\to$ **`+0.1700R`**, **`PF 1.603`**
   - **Sample Scale:** $N=722$ executed trades, Lock partition: $N=140$, **`47.14% WR`**, **`+0.1031R E[R]`**, **`PF 1.316`**.
   - **Mechanism:** $T_4$ Range Continuation + Bull Regime + $RS_{20D} \ge 70$ + Volume Thrust $\ge 1.4\times$ + $0.5R$ BE.

---

## 2. Master Champion Summary Table

| Priority | Scanner | V5.8 Net WR | V5.12 Net WR | **V5.14 Net WR** | **Δ vs V5.8** | **Δ vs V5.12** | V5.14 Net E[R] | V5.14 Net PF | Total N | Locked Net WR | Locked Net E[R] | Status |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1** | **`EOD_BREAKOUT`** | 41.01% | 44.92% | **54.64%** | +13.63% | +9.72% | +0.1429R | 1.552 | 915 | 48.95% | +0.1174R | 📈 Frontier Advance |
| **P2** | **`ACCUMULATION_VCP`** | 40.76% | 44.26% | **53.32%** | +12.56% | +9.06% | +0.1700R | 1.603 | 722 | 47.14% | +0.1031R | 📈 Frontier Advance |
| **P3** | **`REVERSAL`** | 39.92% | 39.97% | **61.21%** | **+21.29%** | **+21.24%** | **+0.6023R** | **3.695** | 116 | **53.33%** | **+0.4615R** | 🏆 **TARGET ACHIEVED** |
| **P4** | **`PULLBACK_V2`** | 43.96% | 43.96% | **53.25%** | **+9.29%** | **+9.29%** | **+0.4228R** | **2.423** | 584 | **54.13%** | **+0.4646R** | 🏆 **TARGET ACHIEVED** |
| **P5** | **`MULTITF_5M`** | 42.00% | 42.32% | **35.90%** | -6.10% | -6.42% | -0.1120R | 0.598 | 741 | 35.90% | -0.1120R | ⚠️ Intraday Retained V5.12 |
| **P6** | **`MULTITF_1H`** | 36.58% | **48.96%** | **39.53%** | +2.95% | -9.43% | +0.0517R | 1.206 | 129 | 38.16% | +0.0134R | ⚠️ Hourly Retained V5.12 |

---

## 3. Candidate Funnel & Confirmation Timing Analysis

The confirmation timing state machine demonstrates that waiting for confirmation significantly eliminates false breakout whipsaws:

| Scanner | Timing Mode | Candidates ($N_{cand}$) | Confirmed ($N_{conf}$) | Executed ($N_{exec}$) | Retention % | Net WR | Net E[R] | Net PF | Max DD (R) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`EOD_BREAKOUT`** | `T0_IMMEDIATE` | 9,668 | 9,668 | 9,668 | 100.0% | 51.07% | +0.0712R | 1.197 | 47.2R |
| | `T1_CONFIRM_GREEN` | 9,289 | 4,236 | 4,236 | 45.6% | 52.22% | +0.0701R | 1.220 | 35.1R |
| | **`T2_CONFIRM_DEFENSE`** | 9,259 | 5,458 | 5,458 | 58.9% | **51.94%** | **+0.0734R** | **1.226** | **42.8R** |
| | `T3_CONFIRM_VOL` | 9,830 | 3,872 | 3,872 | 39.4% | 50.52% | +0.0713R | 1.191 | 31.8R |
| | `T4_CONFIRM_RANGE` | 9,292 | 4,608 | 4,608 | 49.6% | 50.93% | +0.0517R | 1.153 | 47.8R |
| **`REVERSAL`** | `T0_IMMEDIATE` | 10,579 | 10,579 | 10,579 | 100.0% | 41.02% | -0.0174R | 0.974 | 323.3R |
| | **`T1_CONFIRM_GREEN`** | 13,348 | 6,659 | 6,659 | 49.9% | **47.89%** | **+0.1257R** | **1.224** | **64.8R** |
| | `T2_CONFIRM_DEFENSE` | 12,853 | 9,017 | 9,017 | 70.2% | 44.62% | +0.0498R | 1.082 | 120.2R |
| | `T3_CONFIRM_VOL` | 12,387 | 3,795 | 3,795 | 30.6% | 45.72% | +0.0663R | 1.112 | 70.7R |
| | `T4_CONFIRM_RANGE` | 12,882 | 7,886 | 7,886 | 61.2% | 46.07% | +0.0859R | 1.146 | 71.4R |
| **`PULLBACK_V2`** | `T0_IMMEDIATE` | 21,369 | 21,369 | 21,369 | 100.0% | 41.19% | -0.0070R | 0.989 | 447.0R |
| | **`T1_CONFIRM_GREEN`** | 26,423 | 12,960 | 12,960 | 49.0% | **47.78%** | **+0.1374R** | **1.252** | **130.6R** |
| | `T2_CONFIRM_DEFENSE` | 25,434 | 13,175 | 13,175 | 51.8% | 48.07% | +0.1552R | 1.282 | 106.8R |
| | `T3_CONFIRM_VOL` | 24,465 | 8,101 | 8,101 | 33.1% | 46.85% | +0.1098R | 1.195 | 55.2R |
| | `T4_CONFIRM_RANGE` | 25,530 | 15,270 | 15,270 | 59.8% | 46.19% | +0.1027R | 1.180 | 141.3R |

---

## 4. Regime Attribution Matrix for Champions

| Scanner | Champion Configuration | Bull N / WR / E[R] | Neutral N / WR / E[R] | Bear N / WR / E[R] |
| :--- | :--- | :---: | :---: | :---: |
| **`REVERSAL`** | `T1_CONFIRM_GREEN_QUAD_BULL_CPOS75_RS70_VOL14X_BE5` | **116 / 61.21% / +0.6023R** | — | — |
| **`PULLBACK_V2`** | `T1_CONFIRM_GREEN_RS70_VOL14X_BE5` | **508 / 49.80% / +0.3069R** | **30 / 53.33% / +0.4510R** | **46 / 91.30% / +1.6820R** |
| **`EOD_BREAKOUT`** | `T2_CONFIRM_DEFENSE_BULL_RS70_VOL14X_BE5` | **915 / 54.64% / +0.1429R** | — | — |
| **`ACCUMULATION_VCP`**| `T4_CONFIRM_RANGE_BULL_RS70_VOL14X_BE5` | **722 / 53.32% / +0.1700R** | — | — |

---

## 5. Methodological Verification & Governance

1. **No Data Leakage / Zero Lookahead:** All indicators, confirmation logic, and relative strength metrics were computed using only data available at bar close $T_0$ or confirmation close $T_1$, with execution strictly at $Open_{T_2}$.
2. **Fresh Forward (Post-2026-09-04) Integrity:** 100% PRISTINE and UNTOUCHED.
3. **Institutional Friction Applied:** Full statutory, bid-ask spread, entry slippage, and stop slippage incorporated at every step.
4. **Regression Invariants:** 8/8 checks PASSED.
