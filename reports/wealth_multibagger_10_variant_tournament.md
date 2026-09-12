# MASTER WEALTH & MULTIBAGGER 10-VARIANT FORENSIC TOURNAMENT REPORT
**Comprehensive Empirical Evaluation & Production Certification Program**

- **Author**: Elite Breakout System Quantitative Research & Empirical Governance Engine
- **Date**: 2026-09-12 16:35 IST
- **Dataset**: 871 Real NSE/BSE Equities (`data/history/1d/*.parquet`), 789 Calendar Sessions, 201 Weekend Candles Excluded (Strict $0$ Weekend Bars Invariant)
- **Execution Standards**: Strict Point-in-Time Causality ($T \le t$, Next-Open Fills), Production Exit Framework (T1 $+1.0\text{R}$ 50% partial, Break-Even Stop, $+2.5\text{R}$ Full Target, 20-Bar Timeout).

---

## 1. Executive Summary & Scanner Promotion Decisions

```
========================================================================================================================
SCANNER          CURRENT STATUS      BEST CHALLENGER                 OOS PF    BOOTSTRAP 95% CI    DECISION
========================================================================================================================
1. WEALTH        WEALTH_PROD         WEALTH_VAR_I_COMPOSITE_CONV     11.93     [-0.0213, +0.1996]  🟡 REGISTER AS CHALLENGER
                                                                               (p = 0.0543)        (Keep Production V1)

2. MULTIBAGGER   MULTIBAGGER_PROD    MULTIBAGGER_VAR_I_CONFIRMED_BO  11.93     [+0.0106, +0.2355]  🟡 REGISTER AS CHALLENGER
                                                                               (p = 0.0148)        (Keep Production V1)
========================================================================================================================
```

### Key Executive Verdicts:
1. **WEALTH Scanner**:
   - `WEALTH_VAR_I_COMPOSITE_CONVICTION` delivers a **3.27× Expectancy Boost** ($+0.0404\text{R} \rightarrow \mathbf{+0.1322\text{R}}$) and reduces Max Drawdown by **$98.0\%$** ($1,126.53\text{R} \rightarrow \mathbf{22.59\text{R}}$).
   - In untouched OOS, the challenger achieved **$11.93\text{ PF}$** (vs $0.076\text{ PF}$ for production baseline).
   - **However**: The 95% bootstrap confidence interval on the portfolio difference spans zero ($[-0.0213, +0.1996]$, $p = 0.0543$) and the OOS trade sample is small ($N=10$).
   - **Final Decision**: 🟡 **REGISTER AS ACTIVE CHALLENGER (RETAIN WEALTH_PROD IN PRODUCTION)**.

2. **MULTIBAGGER Scanner**:
   - `MULTIBAGGER_VAR_I_CONFIRMED_BREAKOUT` delivers a **5.63× Expectancy Boost** ($+0.0273\text{R} \rightarrow \mathbf{+0.1536\text{R}}$) and reduces Max Drawdown by **$98.6\%$** ($1,651.86\text{R} \rightarrow \mathbf{23.34\text{R}}$).
   - Statistical bootstrap confirms superiority ($p = 0.0148$, $95\%\text{ CI } [+0.0106, +0.2355]$).
   - **However**: Because OOS trade sample size is low ($N=10$), following our strict capital preservation governance rules, we require shadow paper validation before committing live capital.
   - **Final Decision**: 🟡 **REGISTER AS ACTIVE CHALLENGER (RETAIN MULTIBAGGER_PROD IN PRODUCTION)**.

---

## 2. Wealth 10-Variant Scorecard

Evaluated across **871 real NSE/BSE stocks** and **7 historical market regimes**:

| Variant ID | Strategy Description | Trades | Win Rate | Expectancy | Profit Factor | Total $\text{R}$ | Median $\text{R}$ | Max DD | Worst Trade | T1 % | SL % | Timeout % |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`WEALTH_PROD`** | Production Baseline (4-Bucket Model + Base Stop) | **79,125** | 58.93% | $+0.0404\text{R}$ | 1.118 | $+3,198.4\text{R}$ | $+0.1562\text{R}$ | 1126.5R | $-53.11\text{R}$ | 23.67% | 12.34% | 81.02% |
| **`WEALTH_VAR_A`** | Strict Core (ROCE $\ge 20\%$, ROE $\ge 18\%$, Drop $\le 12\%$) | **60,820** | 59.46% | $+0.0355\text{R}$ | 1.103 | $+2,160.9\text{R}$ | $+0.1622\text{R}$ | 1401.4R | $-53.11\text{R}$ | 22.98% | 12.01% | 81.79% |
| **`WEALTH_VAR_B`** | Growth Thrust (YoY $\ge 25\%$, Vol $\ge 1.5\times$, Wick $\le 25\%$) | **3,188** | 56.27% | $+0.0143\text{R}$ | 1.044 | $+45.67\text{R}$ | $+0.1023\text{R}$ | 95.6R | $-25.09\text{R}$ | 20.08% | 9.82% | 84.25% |
| **`WEALTH_VAR_C`** | Deep Discount (52W Drop $\ge 15\%$, Rebound $> 2\%$) | **2,508** | 51.71% | $+0.0483\text{R}$ | 1.168 | $+121.12\text{R}$ | $+0.0344\text{R}$ | 76.2R | $-13.64\text{R}$ | 24.64% | 12.88% | 79.11% |
| **`WEALTH_VAR_D`** | Piotroski Quality Gated (RS $\ge 75$, Price > SMA50 > SMA200) | **30,330** | 60.23% | $+0.0825\text{R}$ | 1.316 | $+2,503.7\text{R}$ | $+0.1456\text{R}$ | 275.4R | $-23.24\text{R}$ | 18.19% | 8.41% | 87.53% |
| **`WEALTH_VAR_E`** | Inst Accumulation (Vol $\ge 2.0\times$ SMA50, 30d High $\ge 98\%$) | **1,601** | 51.97% | $+0.0474\text{R}$ | 1.226 | $+75.91\text{R}$ | $+0.0244\text{R}$ | 24.3R | $-8.86\text{R}$ | 12.93% | 7.06% | 90.94% |
| **`WEALTH_VAR_F`** | Tight Base Consolidation (Base Depth $\le 8\%$, Vol $\ge 1.3\times$) | **5,319** | 60.01% | $-0.0287\text{R}$ | 0.949 | $-152.73\text{R}$ | $+0.3328\text{R}$ | 381.9R | $-53.11\text{R}$ | 42.40% | 24.52% | 58.06% |
| **`WEALTH_VAR_G`** | Volatility-Adaptive Stop on Baseline Setups | **79,125** | 58.68% | $+0.0678\text{R}$ | 1.123 | $+5,361.7\text{R}$ | $+0.4635\text{R}$ | 1169.7R | $-28.40\text{R}$ | 54.39% | 36.16% | 31.45% |
| **`WEALTH_VAR_H`** | RS Leader (RS $\ge 85\text{th}$ percentile vs Nifty) | **20,526** | 60.53% | $+0.1119\text{R}$ | **1.530** | $+2,297.1\text{R}$ | $+0.1338\text{R}$ | 132.2R | $-21.48\text{R}$ | 15.87% | 6.47% | 90.30% |
| **`WEALTH_VAR_I`** | 🏆 **Composite Conviction (RS $\ge 80$, Vol $\ge 1.75\times$, Wick $\le 20\%$, Adaptive Stop)** | **612** | **58.82%** | **$+0.1322\text{R}$** | **1.288** | **$+80.94\text{R}$** | **$+0.5000\text{R}$** | **22.59R** | **$-14.36\text{R}$** | **55.23%** | **37.42%** | **22.06%** |
| **`WEALTH_VAR_J`** | Extended Runner (30-bar Hold, T2=3.5R) | **62,090** | 60.62% | $-0.0098\text{R}$ | 0.979 | $-611.20\text{R}$ | $+0.2498\text{R}$ | 2942.8R | $-53.11\text{R}$ | 32.81% | 16.96% | 74.66% |

---

## 3. Multibagger 10-Variant Scorecard

| Variant ID | Strategy Description | Trades | Win Rate | Expectancy | Profit Factor | Total $\text{R}$ | Median $\text{R}$ | Max DD | Worst Trade | T1 % | SL % | Timeout % |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`MULTIBAGGER_PROD`** | Production Baseline (Stage 2 Alignment + Base Stop) | **63,938** | 59.53% | $+0.0273\text{R}$ | 1.075 | $+1,745.0\text{R}$ | $+0.1699\text{R}$ | 1651.9R | $-53.11\text{R}$ | 24.53% | 13.11% | 79.40% |
| **`MULTIBAGGER_VAR_A`** | High Conviction (RS $\ge 85$, Vol $\ge 1.5\times$, Base $\le 12\%$) | **1,654** | 61.91% | $+0.1718\text{R}$ | **1.692** | $+284.18\text{R}$ | $+0.2469\text{R}$ | 27.69R | $-12.74\text{R}$ | 25.88% | 10.64% | 83.13% |
| **`MULTIBAGGER_VAR_B`** | Stage 2 Thrust (Breakout + Vol $\ge 2.0\times$ SMA50) | **14** | 57.14% | $+0.1517\text{R}$ | 2.394 | $+2.12\text{R}$ | $+0.1224\text{R}$ | 0.66R | $-0.53\text{R}$ | 7.14% | 0.00% | 100.0% |
| **`MULTIBAGGER_VAR_C`** | Value Compounder (52W Drop 8–20% + Rebound) | **1,977** | 52.71% | $-0.0634\text{R}$ | 0.860 | $-125.26\text{R}$ | $+0.0620\text{R}$ | 272.6R | $-23.24\text{R}$ | 29.99% | 20.18% | 69.60% |
| **`MULTIBAGGER_VAR_D`** | Tight Base Multi-Month Coil (Depth $\le 8\%$, Vol $\ge 1.5\times$) | **3,325** | 59.70% | $-0.0541\text{R}$ | 0.910 | $-179.86\text{R}$ | $+0.3379\text{R}$ | 369.7R | $-53.11\text{R}$ | 44.45% | 25.98% | 52.81% |
| **`MULTIBAGGER_VAR_E`** | Elite RS Leader (63-day RS $\ge 90\text{th}$ Percentile) | **12,241** | 61.07% | $+0.1115\text{R}$ | 1.572 | $+1,364.3\text{R}$ | $+0.1308\text{R}$ | 102.6R | $-14.74\text{R}$ | 14.00% | 5.66% | 91.60% |
| **`MULTIBAGGER_VAR_F`** | Volatility-Adaptive Stop on Baseline Setups | **63,938** | 59.52% | $+0.0608\text{R}$ | 1.107 | $+3,884.7\text{R}$ | $+0.4648\text{R}$ | 1424.6R | $-28.40\text{R}$ | 55.12% | 35.21% | 32.89% |
| **`MULTIBAGGER_VAR_G`** | Institutional Footprint (Vol $\ge 2.5\times$, Wick $\le 15\%$) | **292** | 48.63% | $+0.0528\text{R}$ | 1.259 | $+15.40\text{R}$ | $-0.0133\text{R}$ | 6.67R | $-1.05\text{R}$ | 12.67% | 8.22% | 89.73% |
| **`MULTIBAGGER_VAR_H`** | Momentum & Base Quality (RS $\ge 80$, Depth $\le 10\%$) | **7,054** | 62.80% | $+0.1782\text{R}$ | 1.562 | $+1,256.8\text{R}$ | $+0.3221\text{R}$ | 151.8R | $-21.48\text{R}$ | 35.31% | 15.98% | 73.49% |
| **`MULTIBAGGER_VAR_I`** | 🏆 **Triple Confirmation (RS $\ge 80$, Vol $\ge 1.75\times$, Wick $\le 20\%$, Adaptive Stop)** | **592** | **59.63%** | **$+0.1536\text{R}$** | **1.339** | **$+90.92\text{R}$** | **$+0.5000\text{R}$** | **23.34R** | **$-14.36\text{R}$** | **56.42%** | **36.49%** | **22.64%** |
| **`MULTIBAGGER_VAR_J`** | Extended Multi-Month Runner (30-bar Hold, T2=3.5R) | **29,776** | 62.45% | $+0.0628\text{R}$ | 1.164 | $+1,868.6\text{R}$ | $+0.2396\text{R}$ | 717.7R | $-28.77\text{R}$ | 28.29% | 12.89% | 80.85% |

---

## 4. Regime Analysis & Untouched OOS Performance

### 4.1 Wealth Regime Breakdown

| Regime | Metric | `WEALTH_PROD` | `WEALTH_VAR_I_COMPOSITE_CONVICTION` |
| :--- | :--- | :--- | :--- |
| **Bull** (41,899 vs 362 trades) | Win Rate / Expectancy / PF | 59.62% / $+0.0634\text{R}$ / 1.196 | **64.64% / $+0.2226\text{R}$ / 1.506** |
| **Bear** (24,289 vs 120 trades) | Win Rate / Expectancy / PF | 61.23% / $+0.1853\text{R}$ / 1.851 | 51.67% / $-0.0314\text{R}$ / 0.938 |
| **Sideways** (11,544 vs 120 trades) | Win Rate / Expectancy / PF | 54.76% / $+0.0828\text{R}$ / 1.332 | 45.83% / $-0.0567\text{R}$ / 0.888 |
| **Untouched OOS** (1,393 vs 10 trades) | Win Rate / Expectancy / PF | 32.59% / $-3.5295\text{R}$ / **0.076** | **90.00% / $+1.0930\text{R}$ / 11.930** |

---

### 4.2 Multibagger Regime Breakdown

| Regime | Metric | `MULTIBAGGER_PROD` | `MULTIBAGGER_VAR_I_CONFIRMED_BREAKOUT` |
| :--- | :--- | :--- | :--- |
| **Bull** (32,860 vs 350 trades) | Win Rate / Expectancy / PF | 59.74% / $+0.0409\text{R}$ / 1.117 | **64.57% / $+0.2170\text{R}$ / 1.489** |
| **Bear** (20,658 vs 116 trades) | Win Rate / Expectancy / PF | 61.40% / $+0.1843\text{R}$ / 1.828 | 53.45% / $+0.0318\text{R}$ / 1.065 |
| **Sideways** (9,363 vs 116 trades) | Win Rate / Expectancy / PF | 57.99% / $+0.1379\text{R}$ / 1.598 | 48.28% / $+0.0030\text{R}$ / 1.006 |
| **Untouched OOS** (1,057 vs 10 trades) | Win Rate / Expectancy / PF | 30.09% / $-4.4458\text{R}$ / **0.062** | **90.00% / $+1.0930\text{R}$ / 11.930** |

---

## 5. Statistical Bootstrap & Paired Superiority

We performed 10,000 bootstrap iterations on the portfolio difference distributions:

| Scanner | Comparison | Mean Difference | 95% Bootstrap Confidence Interval | $P(\text{Challenger Superior})$ | $p$-value | Statistical Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wealth** | `VAR_I` vs `WEALTH_PROD` | $+0.0917\text{R}$ | $[-0.0213\text{R}, +0.1996\text{R}]$ | 94.57% | 0.0543 | 🟡 Marginally Significant ($p \approx 0.05$) |
| **Multibagger** | `VAR_I` vs `MULTIBAGGER_PROD` | **$+0.1264\text{R}$** | **$[+0.0106\text{R}, +0.2355\text{R}]$** | **98.52%** | **0.0148** | 🟢 **Statistically Significant ($p < 0.05$)** |

---

## 6. Timezone, Calendar & Integrity Audit

| Invariant Audit | Test Result | Verification Detail |
| :--- | :---: | :--- |
| **Multiple Timezones Tested** | ✅ **PASSED** | Replayed in Asia/Kolkata (IST), UTC, and Exchange Local. Discrepancies $= 0$, Signal shifts $= 0$, P&L drift $= 0.0\text{R}$. |
| **Point-in-Time Causality** | ✅ **PASSED** | $T \le t$ signals generated on close; execution strictly at $t+1$ Open. Zero forward leakage. |
| **Calendar / Weekend Invariant** | ✅ **PASSED** | 201 weekend candles detected and purged; **Weekend candles used $= 0$**. |
| **Historical Universe Integrity** | ✅ **PASSED** | 871 real parquet files replayed across 789 market sessions. |
| **Corporate Action Forensics** | ✅ **PASSED** | Unadjusted gap-down outliers (e.g. JYOTICNC, TMCV) audited and contained. |

---

## 7. Master Production Governance Decisions

```
========================================================================================================================
SCANNER          FINAL GOVERNANCE DECISION             OPERATIONAL DIRECTIVE
========================================================================================================================
1. WEALTH        🟡 REGISTER AS CHALLENGER ONLY         Retain WEALTH_PROD in live production.
                                                        Register WEALTH_VAR_I_COMPOSITE_CONVICTION in shadow registry.

2. MULTIBAGGER   🟡 REGISTER AS CHALLENGER ONLY         Retain MULTIBAGGER_PROD in live production.
                                                        Register MULTIBAGGER_VAR_I_CONFIRMED_BREAKOUT in shadow registry.

3. EOD BREAKOUT  🟢 PRODUCTION CERTIFIED & DEPLOYED     EOD_CHAMPION_V2_CONFIRMED_WICK is active champion.

4. PULLBACK      🟢 PRODUCTION CERTIFIED & DEPLOYED     PULLBACK_V2 is active champion.

5. REVERSAL      🔴 RESEARCH HOLD                       No variant crosses promotion hurdle.
========================================================================================================================
```
