# Daily Builder Controlled Parameter Tournament Master Certification Report

## Executive Summary & Final Decision

* **Final Tournament Decision**: **V5.29 REMAINS CHAMPION**
* **Authoritative Benchmark**: `V5.29_DB_SHADOW` (Certified)
* **Tournament Champion**: `DB_TOURN_SWEEP_RS_OFF`
* **Champion Architecture**: `RS Acceleration Weight = OFF (0.0)`
* **Recommendation**: While `DB_TOURN_SWEEP_RS_OFF` showed positive point estimate (+0.035R), it failed statistical lower-bound clearance (CI includes zero or p >= 0.05). Following multiple-testing control, V5.29 remains champion.
* **Production Action**: **NONE** (Zero modifications to live capital V5.25 or shadow V5.28/V5.29)

---

## 1. Experiment & Dataset Definition

* **Target Scanner**: `DAILY_BUILDER`
* **Candidate Event Population**: 17540 candidate events across 500 trading sessions.
* **Data Splits**:
  - **Period A (Development)**: 250 Sessions (1–250)
  - **Period B (Validation)**: 125 Sessions (251–375)
  - **Period C (Final Untouched Holdout)**: 125 Sessions (376–500)
* **Hard Calendar Invariants**:
  - Saturday Candles: `0`
  - Sunday Candles: `0`
  - Lookahead Violations: `0`
  - Duplicate Events: `0`

---

## 2. Full Parameter Grid Summary

Total Configurations Evaluated: `34`

| Config ID | Category | Score Floor | Exh Cliff | Freshness $\lambda$ | RS Wt | Veto Wick | Veto Ext | 30m Trigger |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `DB_TOURN_000_V529_BENCHMARK` | BENCHMARK | 60.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_58` | SWEEP_SCORE_FLOOR | 58.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_59` | SWEEP_SCORE_FLOOR | 59.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_61` | SWEEP_SCORE_FLOOR | 61.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_62` | SWEEP_SCORE_FLOOR | 62.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_63` | SWEEP_SCORE_FLOOR | 63.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FLOOR_64` | SWEEP_SCORE_FLOOR | 64.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_EXHAUST_18` | SWEEP_EXHAUSTION | 60.0 | 18.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_EXHAUST_20` | SWEEP_EXHAUSTION | 60.0 | 20.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_EXHAUST_24` | SWEEP_EXHAUSTION | 60.0 | 24.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_EXHAUST_26` | SWEEP_EXHAUSTION | 60.0 | 26.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FRESH_3D` | SWEEP_FRESHNESS | 60.0 | 22.0 | 0.2310 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FRESH_5D` | SWEEP_FRESHNESS | 60.0 | 22.0 | 0.1386 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FRESH_9D` | SWEEP_FRESHNESS | 60.0 | 22.0 | 0.0770 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_FRESH_12D` | SWEEP_FRESHNESS | 60.0 | 22.0 | 0.0578 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_RS_OFF` | SWEEP_RS_WEIGHT | 60.0 | 22.0 | 0.0990 | 0.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_RS_LOW` | SWEEP_RS_WEIGHT | 60.0 | 22.0 | 0.0990 | 5.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_RS_HIGH` | SWEEP_RS_WEIGHT | 60.0 | 22.0 | 0.0990 | 15.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_VETO_WICK_200` | SWEEP_VETO_WICK | 60.0 | 22.0 | 0.0990 | 10.0 | 20.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_VETO_WICK_225` | SWEEP_VETO_WICK | 60.0 | 22.0 | 0.0990 | 10.0 | 22.5% | 2.5R | ON |
| `DB_TOURN_SWEEP_VETO_WICK_275` | SWEEP_VETO_WICK | 60.0 | 22.0 | 0.0990 | 10.0 | 27.5% | 2.5R | ON |
| `DB_TOURN_SWEEP_VETO_WICK_300` | SWEEP_VETO_WICK | 60.0 | 22.0 | 0.0990 | 10.0 | 30.0% | 2.5R | ON |
| `DB_TOURN_SWEEP_VETO_EXT_225` | SWEEP_VETO_EXT | 60.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.25R | ON |
| `DB_TOURN_SWEEP_VETO_EXT_275` | SWEEP_VETO_EXT | 60.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.75R | ON |
| `DB_TOURN_SWEEP_VETO_EXT_300` | SWEEP_VETO_EXT | 60.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 3.0R | ON |
| `DB_TOURN_ABLATION_NO_30M` | ABLATION_30M | 60.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | OFF |
| `DB_TOURN_INTER_61_EX20_F7D` | INTERACTION_GRID | 61.0 | 20.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_INTER_61_EX22_F9D` | INTERACTION_GRID | 61.0 | 22.0 | 0.0770 | 10.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_INTER_61_EX22_F7D_RS15` | INTERACTION_GRID | 61.0 | 22.0 | 0.0990 | 15.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_INTER_62_EX20_F7D_RS15` | INTERACTION_GRID | 62.0 | 20.0 | 0.0990 | 15.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_INTER_61_EX20_W225` | INTERACTION_GRID | 61.0 | 20.0 | 0.0990 | 10.0 | 22.5% | 2.5R | ON |
| `DB_TOURN_INTER_61_EX22_W275` | INTERACTION_GRID | 61.0 | 22.0 | 0.0990 | 10.0 | 27.5% | 2.5R | ON |
| `DB_TOURN_INTER_60_EX20_RS15` | INTERACTION_GRID | 60.0 | 20.0 | 0.0990 | 15.0 | 25.0% | 2.5R | ON |
| `DB_TOURN_INTER_62_EX22_F7D` | INTERACTION_GRID | 62.0 | 22.0 | 0.0990 | 10.0 | 25.0% | 2.5R | ON |

---

## 3. Period A (Development) Sensitivity Sweeps

### Score Floor Sweeps (Exh=22, Lambda=0.099, Veto=25%)

| Score Floor | Config ID | N Trades | Total R | E[R] | Delta E[R] | Win Rate | Profit Factor | MaxDD |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 58.0 | `DB_TOURN_SWEEP_FLOOR_58` | 897 | +1089.07R | +1.214R | -0.013R | 83.6% | 11.08 | 2.75R |
| 59.0 | `DB_TOURN_SWEEP_FLOOR_59` | 888 | +1086.08R | +1.223R | -0.004R | 83.9% | 11.36 | 2.75R |
| 60.0 | `DB_TOURN_000_V529_BENCHMARK` | 877 | +1076.35R | +1.227R | +0.000R | 84.2% | 11.54 | 2.75R |
| 61.0 | `DB_TOURN_SWEEP_FLOOR_61` | 864 | +1066.77R | +1.235R | +0.008R | 84.4% | 11.79 | 2.75R |
| 62.0 | `DB_TOURN_SWEEP_FLOOR_62` | 855 | +1058.78R | +1.238R | +0.011R | 84.4% | 11.83 | 2.75R |
| 63.0 | `DB_TOURN_SWEEP_FLOOR_63` | 844 | +1050.85R | +1.245R | +0.018R | 84.7% | 12.10 | 2.75R |
| 64.0 | `DB_TOURN_SWEEP_FLOOR_64` | 833 | +1032.83R | +1.240R | +0.013R | 84.6% | 12.01 | 2.75R |

---

## 4. Shortlist Validation on Period B (Validation Period)

| Rank | Config ID | Category | N Trades | Total R | E[R] | Delta vs V5.29 | Win Rate | Profit Factor | MaxDD |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| #1 | `DB_TOURN_SWEEP_RS_OFF` | SWEEP_RS_WEIGHT | 470 | +584.89R | +1.244R | **+0.032R** | 85.7% | 13.18 | 2.77R |
| #2 | `DB_TOURN_SWEEP_FLOOR_64` | SWEEP_SCORE_FLOOR | 478 | +589.34R | +1.233R | **+0.021R** | 84.7% | 12.05 | 2.76R |
| #3 | `DB_TOURN_SWEEP_FLOOR_63` | SWEEP_SCORE_FLOOR | 484 | +595.24R | +1.230R | **+0.018R** | 84.5% | 11.84 | 2.76R |
| #4 | `DB_TOURN_SWEEP_FLOOR_61` | SWEEP_SCORE_FLOOR | 488 | +598.01R | +1.225R | **+0.013R** | 84.4% | 11.70 | 2.76R |
| #5 | `DB_TOURN_INTER_61_EX20_F7D` | INTERACTION_GRID | 488 | +598.01R | +1.225R | **+0.013R** | 84.4% | 11.70 | 2.76R |
| #6 | `DB_TOURN_INTER_61_EX20_W225` | INTERACTION_GRID | 488 | +598.01R | +1.225R | **+0.013R** | 84.4% | 11.70 | 2.76R |
| #7 | `DB_TOURN_INTER_61_EX22_W275` | INTERACTION_GRID | 488 | +598.01R | +1.225R | **+0.013R** | 84.4% | 11.70 | 2.76R |
| #8 | `DB_TOURN_INTER_61_EX22_F9D` | INTERACTION_GRID | 490 | +599.92R | +1.224R | **+0.012R** | 84.5% | 11.67 | 2.76R |
| #9 | `DB_TOURN_SWEEP_FLOOR_62` | SWEEP_SCORE_FLOOR | 487 | +596.16R | +1.224R | **+0.012R** | 84.4% | 11.66 | 2.76R |
| #10 | `DB_TOURN_INTER_62_EX22_F7D` | INTERACTION_GRID | 487 | +596.16R | +1.224R | **+0.012R** | 84.4% | 11.66 | 2.76R |

---

## 5. Candidate Population Reconciliation & Funnel Audit (Period C Holdout)

> **Universe Parity Certification**: All 4 configurations are evaluated against the **exact same 4,331 candidate events** (identical event IDs across 125 holdout sessions). The differences in executed trade counts arise purely from deterministic filtering rules (Scoring Floor, Veto Logic, Daily Top 5 Slot Allocation, and 30m Execution Trigger).

| Funnel Stage | V5.25 Production Baseline | V5.28 Shadow Baseline | V5.29 Certified Baseline | Tournament Champion (`DB_TOURN_SWEEP_RS_OFF`) |
| :--- | :---: | :---: | :---: | :---: |
| **Raw Market Events Evaluated** | `4331` | `4331` | `4331` | `4331` |
| **Vetoes Triggered (Exclusions)** | 0 | 0 | 1967 | 1967 |
| **Pre-Qualified Alerts** | 1,842 | 974 | 728 | 694 |
| **Daily Top-5 Slots Selected** | 625 | 602 | 574 | 542 |
| **30m Traps Avoided (Unconfirmed)** | 0 | 0 | 43 | 38 |
| **Final Executed Trades (N)** | **559** | **495** | **452** | **422** |

---

## 6. Final Period C (Untouched Holdout) Head-to-Head Certification

| Metric | V5.25 Production Baseline | V5.28 Shadow Baseline | V5.29 Certified Baseline | Tournament Champion (`DB_TOURN_SWEEP_RS_OFF`) | Incremental Delta (Champ - V5.29) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Evaluated Candidates** | 4331 | 4331 | 4331 | 4331 | 0 |
| **Executed Trades (N)** | 559 | 495 | 452 | 422 | -30 |
| **Total R Output** | +651.56R | +610.40R | +580.09R | +556.08R | **-24.01R** |
| **Expected Value (E[R])** | +1.166R | +1.233R | +1.283R | +1.318R | **+0.035R** |
| **Win Rate (%)** | 81.2% | 84.0% | 87.6% | 89.3% | +1.7% |
| **Profit Factor** | 8.97 | 10.96 | 13.85 | 16.50 | **+2.65** |
| **Maximum Drawdown** | 3.12R | 3.12R | 3.00R | 3.00R | **+0.00R** |
| **LOO1 E[R]** | +1.162R | +1.229R | +1.279R | +1.313R | +0.034R |
| **Winsorized E[R]** | +1.164R | +1.232R | +1.282R | +1.315R | +0.033R |

### Paired Statistical Significance on Holdout
* **Observed Paired Delta E[R]**: `+0.035R`
* **95% Bootstrap Confidence Interval**: `[-0.063R, +0.178R]`
* **Paired Permutation Test p-value**: `0.1930`

---

## 7. Regime Performance Breakdown (Holdout)

| Market Regime | V5.29 N | V5.29 E[R] | V5.29 PF | Champion N | Champion E[R] | Champion PF | Delta E[R] |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **STRONG_BULL** | 180 | +1.334R | 16.50 | 181 | +1.324R | 16.22 | **-0.010R** |
| **NEUTRAL_BULL** | 173 | +1.401R | 23.15 | 175 | +1.428R | 26.12 | **+0.027R** |
| **CHOPPY_RANGE** | 93 | +0.963R | 6.05 | 66 | +1.007R | 7.54 | **+0.044R** |
| **NEUTRAL_BEAR** | 6 | +1.334R | 9.30 | 0 | +0.000R | 0.00 | **-1.334R** |
| **SHARP_SELLOFF** | 0 | +0.000R | 0.00 | 0 | +0.000R | 0.00 | **+0.000R** |

---

## 8. Multiple-Testing Disclosure & Governance

1. **Total Configurations Tested**: 34
2. **Strict Partitioning**: Period A Discovery -> Period B Validation -> Period C Untouched Holdout.
3. **Zero Production Mutation**: `V5.25_PRODUCTION`, `V5.28_DB_SHADOW`, and `V5.29_SHADOW` remained 100% untouched.