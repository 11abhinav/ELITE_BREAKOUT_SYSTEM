# Technical Scanner Master Forensic Certification & Empirical Audit Report

**Generated**: 2026-09-18 00:28:27 IST  
**Historical Universe**: 879 Real BSE/NSE Equity Instruments (`data/history/1d/*.parquet`)  
**Evaluation Invariant**: Zero forward lookahead ($T \le t$), Asian/Kolkata (IST) Monday–Friday trading calendar. Same-bar SL/T1 touch strictly classified as **LOSS** (`INTRABAR_AMBIGUITY_LOSS`).  
**Ledgers Generated**:
- Pre-Gate Baseline Ledger: [technical_scanner_baseline_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/technical_scanner_baseline_ledger.csv) (7134 trades)
- Post-Gate Hardened Ledger: [technical_scanner_hardened_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/technical_scanner_hardened_ledger.csv) (4806 trades)
- Production Master Ledger: [technical_scanner_trade_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/technical_scanner_trade_ledger.csv) (4806 trades)

---

## 1. Executive Summary & Dual Production Policy Decision

### Formal Dual Certification Rule
A pattern qualifies for **`PRODUCTION` (`TIER_2_OOS_VALIDATED`)** if and only if it satisfies all 6 requirements:
1. **$\text{OOS WR} \ge 50.0\%$**
2. **$\text{OOS PF} \ge 1.50$**
3. **$\text{Full Hardened PF} \ge 1.50$**
4. **$\text{OOS Avg R} > 0.00\text{R}$**
5. **$\text{OOS Sample } N \ge 25$**
6. **Zero Data Integrity / Invariant Violations**

### Mechanical Classification Results:
1. **`WYCKOFF_SPRING_TYPE_2`**: **`PRODUCTION` (`TIER_2_OOS_VALIDATED`)**
   - *OOS (2026) Metrics*: **52.9% Win Rate**, **PF 1.63**, **+0.27R Expectancy** across 535 holdout trades.
   - *Full Hardened Metrics*: **52.7% Win Rate**, **PF 1.57**, **+0.24R Expectancy** across 888 trades.
   - *Confidence Intervals*: Hard WR 95% CI = [49.4%, 56.0%], OOS WR 95% CI = [48.7%, 57.1%].
   - *Assessment*: Positive OOS-validated expectancy with PF above production threshold (1.57 Hard / 1.63 OOS), with win-rate estimate statistically close to the 50% boundary.
   - *Regime Attribution*: **Bear WR = 69.3%** (N=202), **Sideways WR = 51.7%** (N=468), **Bull WR = 39.4%** (N=218).

2. **`HIGHER_LOW_REVERSAL`**: **`RESEARCH_ONLY`**
   - *OOS (2026) Metrics*: WR = 50.0%, PF = 1.44 (N=114).
   - *Full Hardened Metrics*: Hard PF = 1.32 (< 1.50 Production standard).
   - *Policy Action*: Retained in detector codebase for continuous research; blocked from live alert dispatch under the dual production gate.

3. **`RESEARCH_ONLY` (Sub-Threshold / Promising Expectancy)**:
   - `MULTI_MONTH_BASE_BREAKOUT` (Hard PF 1.34, +0.16R), `CUP_HANDLE` (Hard PF 1.21, +0.11R)
   - *Policy Action*: Retained in detector codebase for continuous research; blocked from live alert dispatch.

4. **`QUARANTINED` (Negative / Breakeven Expectancy)**:
   - `BULL_FLAG` (PF 1.25, OOS PF 1.22), `DOUBLE_BOTTOM` (PF 0.93), `V_REVERSAL` (PF 1.06), `SHAKEOUT_RECLAIM` (PF 0.93), `BULL_PENNANT` (PF 0.69)
   - *Policy Action*: Detectors preserved in repository; explicitly gated out in `PATTERN_STATUS`.

5. **`INSUFFICIENT_SAMPLE` (N < 25)**:
   - `ASCENDING_TRIANGLE` (Hardened N=23 < 25).

---

## 2. Anti-Fakeout Quality Gate Proof: Retained vs. Rejected Population

Below is the empirical proof comparing the **Pre-Gate Baseline**, **Post-Gate Hardened**, and **Rejected** candidate signal populations:

| Signal Population | N | Win Rate | Gross Profit R | Gross Loss R | Profit Factor (PF) | Total Net R | Expectancy (Avg R) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline Scanner (Pre-Gate)** | 7134 | 45.9% | +4162.6R | -3537.3R | **1.18** | +625.3R | +0.09R |
| **Hardened Scanner (Post-Gate)** | 4806 | **45.8%** | +2819.2R | -2400.2R | **1.17** | +419.1R | **+0.09R** |
| **Rejected by Anti-Fake Gates** | 2328 | 46.2% | +1343.4R | -1137.2R | **1.18** | +206.2R | +0.09R |

> **Empirical Finding on Gate Selectivity**: The anti-fake gates rejected **2328 of 7134 candidate signals (32.6%)**, but aggregate outcome metrics did not improve materially in the overall retained population. The effect of the anti-fake gates is pattern-specific rather than universally quality-accretive across all geometries.

---

## 3. Stepwise Gate Contribution (Ablation Waterfall) & Pattern-Specific Impact

### Universal Stepwise Waterfall:
| Step / Gate Applied | N | Win Rate (%) | Profit Factor (PF) | Expectancy (Avg R) | Signal Retention (%) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Baseline (Unfiltered Candidates)** | 7134 | 45.9% | 1.18 | +0.09R | 100.0% |
| **2. + CLV $\ge$ 0.70** | 7134 | 45.9% | 1.18 | +0.09R | 100.0% |
| **3. + RVOL $\ge$ 1.35** | 5737 | 46.0% | 1.18 | +0.09R | 80.4% |
| **4. + Upper Wick $\le$ 25%** | 4806 | 45.8% | 1.17 | +0.09R | 67.4% |
| **5. + Score $\ge$ 70 (Hardened)** | 4806 | 45.8% | 1.17 | +0.09R | 67.4% |

### Pattern-Specific Gate Impact:
| Pattern | Baseline N | Baseline WR | Baseline PF | Baseline Avg R | Hardened N | Hardened WR | Hardened PF | Hardened Avg R | $\Delta$ PF | Quality Impact |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `ASCENDING_TRIANGLE` | 46 | 41.3% | 0.75 | +-0.14R | 23 | 39.1% | 0.76 | +-0.14R | **+0.01** | 🟢 ACCRETIVE |
| `BULL_FLAG` | 575 | 45.7% | 1.16 | +0.08R | 388 | 47.7% | 1.25 | +0.12R | **+0.09** | 🟢 ACCRETIVE |
| `BULL_PENNANT` | 98 | 31.6% | 0.71 | +-0.18R | 68 | 30.9% | 0.69 | +-0.2R | **-0.02** | 🔴 DILUTIVE |
| `CUP_HANDLE` | 1126 | 46.2% | 1.25 | +0.13R | 696 | 45.7% | 1.21 | +0.11R | **-0.04** | 🔴 DILUTIVE |
| `DOUBLE_BOTTOM` | 461 | 42.7% | 1.01 | +0.01R | 325 | 40.6% | 0.93 | +-0.04R | **-0.08** | 🔴 DILUTIVE |
| `HIGHER_LOW_REVERSAL` | 264 | 47.3% | 1.26 | +0.12R | 174 | 48.3% | 1.32 | +0.15R | **+0.06** | 🟢 ACCRETIVE |
| `MULTI_MONTH_BASE_BREAKOUT` | 724 | 49.0% | 1.33 | +0.15R | 625 | 49.0% | 1.34 | +0.16R | **+0.01** | 🟢 ACCRETIVE |
| `SHAKEOUT_RECLAIM` | 1324 | 39.4% | 0.91 | +-0.05R | 885 | 39.9% | 0.93 | +-0.04R | **+0.02** | 🟢 ACCRETIVE |
| `V_REVERSAL` | 1136 | 46.5% | 1.14 | +0.06R | 734 | 44.4% | 1.06 | +0.03R | **-0.08** | 🔴 DILUTIVE |
| `WYCKOFF_SPRING_TYPE_2` | 1380 | 52.1% | 1.52 | +0.22R | 888 | 52.7% | 1.57 | +0.24R | **+0.05** | 🟢 ACCRETIVE |

---

## 4. 4-Way Controlled Benchmark Matrix

| Benchmark Model | Independent Ledger | N | Win Rate (WR) | Profit Factor (PF) | Expectancy (Avg R) | 95% Confidence Interval |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `Control A: Random Entry` | Random Ledger | 4257 | 43.2% | 1.13 | +0.07R | [41.7%, 44.7%] |
| `Control C: 20D Momentum` | Momentum Ledger | 1423 | 43.9% | 1.17 | +0.09R | [41.3%, 46.4%] |
| `Control D: Baseline Scanner` | Pre-Gate Ledger | 7134 | 45.9% | 1.18 | +0.09R | [44.8%, 47.1%] |
| `Challenger: Hardened Scanner` | Post-Gate Ledger | 4806 | **45.8%** | **1.17** | **+0.09R** | [44.4%, 47.2%] |

---

## 5. Master Pattern Certification Matrix

| Pattern | OOS N | OOS WR | OOS PF | OOS Avg R | OOS WR 95% CI | Hard N | Hard WR | Hard PF | Hard Avg R | Hard WR 95% CI | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `ASCENDING_TRIANGLE` | 16 | **37.5%** | 0.88 | +-0.07R | [18.5%, 61.4%] | 23 | 39.1% | 0.76 | +-0.14R | [22.2%, 59.2%] | `INSUFFICIENT_SAMPLE` |
| `BULL_FLAG` | 302 | **46.7%** | 1.22 | +0.11R | [41.1%, 52.3%] | 388 | 47.7% | 1.25 | +0.12R | [42.8%, 52.6%] | `RESEARCH_ONLY` |
| `BULL_PENNANT` | 51 | **39.2%** | 1.0 | +0.0R | [27.0%, 52.9%] | 68 | 30.9% | 0.69 | +-0.2R | [21.2%, 42.6%] | `RESEARCH_ONLY` |
| `CUP_HANDLE` | 380 | **43.7%** | 1.11 | +0.06R | [38.8%, 48.7%] | 696 | 45.7% | 1.21 | +0.11R | [42.0%, 49.4%] | `RESEARCH_ONLY` |
| `DOUBLE_BOTTOM` | 239 | **37.2%** | 0.86 | +-0.08R | [31.4%, 43.5%] | 325 | 40.6% | 0.93 | +-0.04R | [35.4%, 46.0%] | `RESEARCH_ONLY` |
| `HIGHER_LOW_REVERSAL` | 114 | **50.0%** | 1.44 | +0.2R | [41.0%, 59.0%] | 174 | 48.3% | 1.32 | +0.15R | [41.0%, 55.7%] | `RESEARCH_ONLY` |
| `MULTI_MONTH_BASE_BREAKOUT` | 359 | **47.1%** | 1.19 | +0.1R | [42.0%, 52.2%] | 625 | 49.0% | 1.34 | +0.16R | [45.1%, 52.9%] | `RESEARCH_ONLY` |
| `SHAKEOUT_RECLAIM` | 711 | **39.4%** | 0.92 | +-0.05R | [35.9%, 43.0%] | 885 | 39.9% | 0.93 | +-0.04R | [36.7%, 43.2%] | `RESEARCH_ONLY` |
| `V_REVERSAL` | 486 | **41.6%** | 0.98 | +-0.01R | [37.3%, 46.0%] | 734 | 44.4% | 1.06 | +0.03R | [40.9%, 48.0%] | `RESEARCH_ONLY` |
| `WYCKOFF_SPRING_TYPE_2` | 535 | **52.9%** | 1.63 | +0.27R | [48.7%, 57.1%] | 888 | 52.7% | 1.57 | +0.24R | [49.4%, 56.0%] | `PRODUCTION` |

---

## 6. Complete Trade-Level Forensic Reconciliation (Zero NaN Distortion)

Every aggregate metric below equals the sum of individual trade ledger records:

| Pattern | N | Winners | Losers | Gross Profit R | Gross Loss R | PF | Total R | Avg Win R | Avg Loss R | Avg R |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `ASCENDING_TRIANGLE` | 23 | 9 | 14 | +10.3R | -13.6R | **0.76** | +-3.3R | +1.15R | -0.97R | **+-0.14R** |
| `BULL_FLAG` | 388 | 185 | 203 | +238.0R | -190.7R | **1.25** | +47.3R | +1.29R | -0.94R | **+0.12R** |
| `BULL_PENNANT` | 68 | 21 | 47 | +30.6R | -44.1R | **0.69** | +-13.5R | +1.46R | -0.94R | **+-0.2R** |
| `CUP_HANDLE` | 696 | 318 | 378 | +438.9R | -362.2R | **1.21** | +76.7R | +1.38R | -0.96R | **+0.11R** |
| `DOUBLE_BOTTOM` | 325 | 132 | 193 | +163.4R | -176.0R | **0.93** | +-12.6R | +1.24R | -0.91R | **+-0.04R** |
| `HIGHER_LOW_REVERSAL` | 174 | 84 | 90 | +103.7R | -78.4R | **1.32** | +25.4R | +1.23R | -0.87R | **+0.15R** |
| `MULTI_MONTH_BASE_BREAKOUT` | 625 | 306 | 319 | +391.0R | -291.3R | **1.34** | +99.7R | +1.28R | -0.91R | **+0.16R** |
| `SHAKEOUT_RECLAIM` | 885 | 353 | 532 | +473.2R | -508.9R | **0.93** | +-35.6R | +1.34R | -0.96R | **+-0.04R** |
| `V_REVERSAL` | 734 | 326 | 408 | +378.2R | -358.4R | **1.06** | +19.8R | +1.16R | -0.88R | **+0.03R** |
| `WYCKOFF_SPRING_TYPE_2` | 888 | 468 | 420 | +592.0R | -376.7R | **1.57** | +215.3R | +1.26R | -0.9R | **+0.24R** |

---

## 7. Bear Market Regime Validation vs. Bear Controls

| Strategy / Pattern | Bear Trades (N) | Bear Win Rate (%) | Bear Gross Profit R | Bear Gross Loss R | Bear Profit Factor (PF) | Bear Avg R |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `Control A (Random in Bear)` | 1051 | 42.2% | +631.5R | -591.4R | 1.07 | 0.04R |
| `Control C (Momentum in Bear)` | 0 | 0.0% | +0.0R | -0.0R | 0.0 | 0.0R |
| `Hardened Universe in Bear` | 676 | 50.4% | +467.3R | -321.6R | 1.45 | 0.22R |
| `WYCKOFF_SPRING_TYPE_2 (Bear)` | **202** | **69.3%** | +189.7R | -59.4R | **3.19** | **+0.64R** |

> **Bear Regime Takeaway**: WYCKOFF_SPRING_TYPE_2 showed strong observed performance during the defined bear regimes: **69.3% WR, PF 3.19 and +0.64R across 202 trades**, versus 42.2% WR and PF 1.07 for the random bear control. The momentum bear control had zero observations and is therefore not comparable.

---

## 8. Automated Invariant & Data Integrity Verification

| # | Invariant Rule | Validation Status |
| :--- | :--- | :--- |
| 1 | **Point-in-Time Causality** | **`PASS`** (Zero future bar lookahead during bar replay) |
| 2 | **Intrabar Ambiguity** | **`PASS`** (Same-bar SL and T1 touch strictly penalized as LOSS) |
| 3 | **Trading Calendar** | **`PASS`** (Monday–Friday only, zero weekend bars) |
| 4 | **Risk Integrity** | **`PASS`** (Entry > Stop Loss, Target 1 > Entry, Risk points > 0) |
| 5 | **Holding Horizon** | **`PASS`** (Maximum holding period bounded at 20 daily bars) |
| 6 | **Ledger Reconciliation** | **`PASS`** (SUM(Win R) / SUM(Loss R) == PF across all rows) |
| 7 | **Zero NaN Contamination** | **`PASS`** (All metrics validated against clean floating-point numbers) |
| 8 | **Dual Baseline/Hardened Separation** | **`PASS`** (Pre-gate vs post-gate independent ledger validation) |
