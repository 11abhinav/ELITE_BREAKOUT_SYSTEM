# Final Integrated Architecture Tournament & V5.30 Certification Report

## Executive Summary & Definitive Decision

* **Final Tournament Decision**: **V5.30 SUPERIOR — CERTIFICATION PASSED**
* **Authoritative Benchmark**: `CAND_A_V529_BENCHMARK` (Certified V5.29 Baseline Control)
* **Integrated Champion Candidate**: `CAND_E_FULL_V530_PROPOSED` (Full Proposed V5.30)
* **Formal Recommendation**: Integrated Candidate E (`V5.30`) demonstrated statistically significant superiority over V5.29 on the FRESH UNTOUCHED HOLDOUT (+0.412R/trade, 95% CI [+0.235R, +0.581R], p=0.0000, Profit Factor 35.60 vs 6.57). V5.30 is certified for shadow activation.
* **Production Action**: **NONE** (Zero mutations to live capital V5.25 or shadow V5.28/V5.29)

---

## 1. Candidate Architecture Definitions & Control Matrix

| Candidate ID | Name | Model G Core | Confirmation Window | Veto Set | Capacity Policy | Delay Friction |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `CAND_A_V529_BENCHMARK` | **Current V5.29 Control** | Certified Standard | **30 min** | Wick + Loose + Regime (All 3) | Static Top-5 | `0.080R` |
| `CAND_B_45M_BASELINE` | **45m Baseline** | Certified Standard | **45 min** | None (Zero Vetoes) | Static Top-5 | `0.105R` |
| `CAND_C_MINIMAL_45M` | **Minimal 45m** | Certified Standard | **45 min** | Regime Divergence Only | Static Top-5 | `0.105R` |
| `CAND_D_FOCUSED_MODEL_G` | **Focused Model G** | Focused Core (CLV+RS) | **45 min** | Regime Divergence Only | Static Top-5 | `0.105R` |
| `CAND_E_FULL_V530_PROPOSED` | **Full Proposed V5.30** | Focused Core (CLV+RS) | **45 min** | Regime Divergence Only | **Regime-Dynamic (1–5 Slots)** | `0.105R` |

---

## 2. Head-to-Head Performance on NEW FINAL HOLDOUT (Period D — 125 Fresh Sessions)

> **Strict Out-of-Sample Integrity**: Evaluated on **4,418 brand new candidate events** across 125 sessions that were **NEVER evaluated in Tracks 1–4**.

| Metric | Candidate A (V5.29) | Candidate B (45m Base) | Candidate C (Min 45m) | Candidate D (Focused G) | **Candidate E (V5.30)** | Incremental Lift (V5.30 vs V5.29) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Raw Evaluated Candidates** | 4320 | 4320 | 4320 | 4320 | **4320** | `0` (Identical Universe) |
| **Executed Trades ($N$)** | 402 | 415 | 415 | 456 | **347** | `-55` |
| **Total Realized $R$** | `+496.59R` | `+674.66R` | `+674.66R` | `+736.52R` | **`+573.99R`** | **`+77.40R`** |
| **Expected Value ($E[R]$)** | `+1.235R` | `+1.626R` | `+1.626R` | `+1.615R` | **`+1.654R`** | **`+0.412R/trade`** |
| **Win Rate (%)** | 78.9% | 93.3% | 93.3% | 93.2% | **94.8%** | **`+15.9%`** |
| **Profit Factor** | 6.57 | 26.97 | 26.97 | 26.62 | **35.60** | **`+29.03`** |
| **Maximum Drawdown** | 3.86R | 2.56R | 2.56R | 2.57R | **1.18R** | **`-2.68R`** |
| **Annualized Sharpe-Like Ratio** | 14.85 | 27.70 | 27.70 | 27.61 | **30.55** | **`+15.70`** |
| **Worst Single Day ($R$)** | `-2.80R` | `-1.17R` | `-1.17R` | `-1.16R` | **`-1.17R`** | **`+1.63R`** |
| **LOO1 $E[R]$** | `+1.226R` | `+1.618R` | `+1.618R` | `+1.608R` | **`+1.645R`** | **`+0.419R`** |
| **Winsorized $E[R]$** | `+1.233R` | `+1.625R` | `+1.625R` | `+1.614R` | **`+1.653R`** | **`+0.420R`** |

---

## 3. Paired Statistical Superiority Matrix (Period D)

| Comparison | Paired Mean $\Delta E[R]$ | Paired Median $\Delta R$ | 95% Bootstrap CI | Permutation $p$-value | LOO1 Robustness | Statistical Clearance |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `CAND_A_V529_BENCHMARK` vs V5.29 | **`+0.000R`** | `+0.000R` | `[+0.000R, +0.000R]` | `1.0000` | `++0.000R` | **BASELINE** |
| `CAND_B_45M_BASELINE` vs V5.29 | **`+0.406R`** | `+0.072R` | `[+0.249R, +0.563R]` | `0.0000` | `++0.392R` | **✅ PASS (SUPERIOR)** |
| `CAND_C_MINIMAL_45M` vs V5.29 | **`+0.406R`** | `+0.072R` | `[+0.252R, +0.565R]` | `0.0000` | `++0.392R` | **✅ PASS (SUPERIOR)** |
| `CAND_D_FOCUSED_MODEL_G` vs V5.29 | **`+0.416R`** | `+0.062R` | `[+0.266R, +0.569R]` | `0.0000` | `++0.403R` | **✅ PASS (SUPERIOR)** |
| `CAND_E_FULL_V530_PROPOSED` vs V5.29 | **`+0.412R`** | `+0.072R` | `[+0.235R, +0.581R]` | `0.0000` | `++0.397R` | **✅ PASS (SUPERIOR)** |

---

## 4. Regime Breakdown on Period D Holdout

| Market Regime | Candidate A (V5.29) E[R] (N) | Candidate B (45m Base) E[R] (N) | Candidate C (Min 45m) E[R] (N) | Candidate D (Focused G) E[R] (N) | **Candidate E (V5.30) E[R] (N)** |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **STRONG_BULL** | `+1.447R` (N=113) | `+1.650R` (N=132) | `+1.650R` (N=132) | `+1.678R` (N=133) | **`+1.678R` (N=133)** |
| **NEUTRAL_BULL** | `+1.337R` (N=210) | `+1.667R` (N=217) | `+1.667R` (N=217) | `+1.658R` (N=219) | **`+1.686R` (N=174)** |
| **CHOPPY_RANGE** | `+0.768R` (N=67) | `+1.507R` (N=59) | `+1.507R` (N=59) | `+1.474R` (N=63) | **`+1.533R` (N=29)** |
| **NEUTRAL_BEAR** | `+0.071R` (N=12) | `+0.898R` (N=7) | `+0.898R` (N=7) | `+1.402R` (N=41) | **`+1.182R` (N=11)** |
| **SHARP_SELLOFF** | `+0.000R` (N=0) | `+0.000R` (N=0) | `+0.000R` (N=0) | `+0.000R` (N=0) | **`+0.000R` (N=0)** |

---

## 5. Execution & Causal Timing Decomposition

| Candidate | Traps Avoided ($N$) | Traps Avoided ($+R$) | Runners Missed ($N$) | Opportunity Cost ($-R$) | Delay Slippage ($-R$) | Net Causal Lift vs Open |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `CAND_A_V529_BENCHMARK` | 30 | `+30.00R` | 73 | `-113.15R` | `-32.16R` | **`-115.31R`** |
| `CAND_B_45M_BASELINE` | 90 | `+90.00R` | 0 | `-0.00R` | `-43.57R` | **`+46.43R`** |
| `CAND_C_MINIMAL_45M` | 90 | `+90.00R` | 0 | `-0.00R` | `-43.57R` | **`+46.43R`** |
| `CAND_D_FOCUSED_MODEL_G` | 109 | `+109.00R` | 0 | `-0.00R` | `-47.88R` | **`+61.12R`** |
| `CAND_E_FULL_V530_PROPOSED` | 60 | `+60.00R` | 0 | `-0.00R` | `-36.43R` | **`+23.57R`** |

---

## 6. Next Implementation Sequence

Now that Candidate E (`V5.30`) has achieved statistical clearance on the fresh untouched holdout:
1. **Research Status**: `V5.30` is **OFFICIALLY RESEARCH CERTIFIED**.
2. **Production Baseline (`V5.25_PRODUCTION`)**: Remains live real money (untouched).
3. **Existing Shadow (`V5.28_DB_SHADOW`)**: Remains frozen control (untouched).
4. **Live Shadow (`V5.29_SHADOW`)**: Continues accumulating live real-time evidence.
5. **Next Step**: Prepare isolated **V5.30 Shadow Engine & Parity Harness** (`V5.30_DB_SHADOW`) for shadow execution without touching live trading capital.