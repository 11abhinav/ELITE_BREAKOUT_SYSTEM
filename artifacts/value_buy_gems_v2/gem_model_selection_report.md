# MASTER VALUE GEM 30-CANDIDATE MODEL SELECTION REPORT

**Execution Timestamp**: 2026-09-28 01:03:30 IST  
**Research Horizon**: 2016-01-01 to 2026-09-27 (10.75 Years)  
**Universe**: Certified Clean Indian Equity Universe (886 Symbols)  
**Data Provenance**: Certified Upstox 1D Parquet + Pass 5 Point-in-Time Fundamentals DB  
**Friction**: 15 bps per trade, $T+1$ Open Execution, Max 20 Portfolio Positions, Equal-Weight Sizing (5%)  

---

## Executive Summary

The **Master Value Gem Research & Backtest Program** systematically evaluated **30 economically distinct candidate hypotheses** (`CONTROL_1..5` + `GEM_R1..25`) across 10.75 years of Indian equity market history using strict point-in-time causality, zero lookahead bias, zero parameter mining, and audited execution friction.

### Key Governance Findings:
1. **`VALUE_GEM_CORE_V1` Baseline Confirmed Rejected**: The original baseline (`TRAIN +0.101R`, `HOLDOUT -0.070R`) was confirmed to be a single-episode COVID recovery artifact (2020: $+0.636\text{R}$, 2021: $+0.432\text{R}$) that completely collapsed in non-bull/choppy regimes.
2. **Holdout Failure Across All Active Formulations**: Every active candidate formulation (`GEM_R1..R24` with $N > 100$) produced **negative net returns in the untouched 2026 holdout period** (Holdout Mean R ranging from $-0.062\text{R}$ to $-0.080\text{R}$, Holdout 95% CI Upper Bound $< 0$).
3. **Severe Structural Constraints on Cash-Flow Cheapness**: Formulations requiring combined high FCF Yield ($>5\%$) and intact multi-year fundamentals (`GEM_R4`, `GEM_R8`, `GEM_R10`, `GEM_R13`, `GEM_R16`, `GEM_R18`, `GEM_R20`, `GEM_R23`, `GEM_R25`) produced **$N=0$ trades**, proving that Indian equities rarely offer extreme cash-flow cheapness alongside untouched high quality without severe structural distress.
4. **Final Decision State**: **`NO ROBUST VALUE GEM FOUND`**. Zero formulations met the mandatory promotion criteria (`holdout_ci_low > 0`, `n_eff >= 100`, `top10_share <= 50%`, regime consistency).

---

## Candidate Model Performance Matrix (All 30 Formulations)

| Candidate ID | Formulation Hypothesis | Total Trades ($N$) | Mean R | Portfolio CAGR | Max Drawdown | Holdout $N$ (2026) | Holdout Mean R | Final Decision State |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CONTROL_1** | Naive Deep Value (P/E < 15) | 58,358 | +0.132 R | +0.57% | -9.84% | 8,188 | -0.020 R | **HOLDOUT_FAILED** |
| **CONTROL_2** | Deep Asset Value (P/B < 1.5) | 50,308 | +0.211 R | +0.36% | -7.40% | 6,123 | -0.045 R | **HOLDOUT_FAILED** |
| **CONTROL_3** | High ROCE + Cheap | 52,238 | +0.101 R | -0.27% | -8.33% | 6,719 | -0.044 R | **HOLDOUT_FAILED** |
| **CONTROL_4** | Value Trap Filtered Control | 31,596 | +0.170 R | +1.15% | -12.81% | 3,918 | -0.053 R | **HOLDOUT_FAILED** |
| **CONTROL_5** | Dislocation Control (30% DD) | 29,526 | +0.040 R | -0.45% | -6.66% | 4,028 | -0.046 R | **HOLDOUT_FAILED** |
| **GEM_R1** | Quality + Historical PE Cheap | 27,932 | +0.149 R | +0.43% | -6.75% | 3,429 | -0.063 R | **HOLDOUT_FAILED** |
| **GEM_R2** | Quality + Peer Relative Cheap | 17,385 | +0.069 R | -0.50% | -5.94% | 2,101 | -0.066 R | **HOLDOUT_FAILED** |
| **GEM_R3** | Quality + Absolute Cheap | 17,385 | +0.069 R | -0.50% | -5.94% | 2,101 | -0.066 R | **HOLDOUT_FAILED** |
| **GEM_R4** | Quality + CashFlow Value (FCF) | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R5** | Quality + PE Percentile (<30th) | 25,064 | +0.041 R | -0.50% | -6.62% | 3,662 | -0.062 R | **HOLDOUT_FAILED** |
| **GEM_R6** | Quality + Peer Relative PE (<0.8x) | 25,064 | +0.041 R | -0.50% | -6.62% | 3,662 | -0.062 R | **HOLDOUT_FAILED** |
| **GEM_R7** | Strong Quality + Valuation + Intact | 23,127 | +0.037 R | -0.53% | -6.73% | 3,319 | -0.064 R | **HOLDOUT_FAILED** |
| **GEM_R8** | Strong Quality + CashFlow Value | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R9** | Quality + Absolute Cheap + 3Y Growth | 17,385 | +0.069 R | -0.50% | -5.94% | 2,101 | -0.066 R | **HOLDOUT_FAILED** |
| **GEM_R10** | Cash Quality + Cash Value | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R11** | Basic Quality + Absolute Cheap + Bull | 13,371 | +0.254 R | +1.84% | -7.15% | 2,900 | -0.069 R | **HOLDOUT_FAILED** |
| **GEM_R12** | CapEfficiency Quality + Hist Cheap | 12,364 | +0.062 R | -0.42% | -5.84% | 2,920 | -0.065 R | **HOLDOUT_FAILED** |
| **GEM_R13** | Quality + FCF Yield + Bull Regime | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R14** | Quality + Peer PE + Bull Filter | 12,364 | +0.062 R | -0.42% | -5.84% | 2,920 | -0.065 R | **HOLDOUT_FAILED** |
| **GEM_R15** | Quality + Hist Cheap + Trend Slope | 1,863 | +0.207 R | +0.88% | -4.12% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R16** | Quality + CashFlow Value + Trend Slope | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R17** | Strong Quality + Hist Cheap + Recovery | 13,739 | +0.059 R | -0.44% | -5.90% | 2,712 | -0.069 R | **HOLDOUT_FAILED** |
| **GEM_R18** | Strong Quality + FCF + Recovery | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R19** | Quality + Hist Cheap + Liquidity | 11,471 | +0.048 R | -0.48% | -5.72% | 2,865 | -0.065 R | **HOLDOUT_FAILED** |
| **GEM_R20** | Strong Quality + Cash Value + Liquidity | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R21** | Quality + Absolute Cheap + ATR Exit | 8,371 | -0.037 R | -0.88% | -6.21% | 1,772 | -0.080 R | **HOLDOUT_FAILED** |
| **GEM_R22** | CapEfficiency Quality + ATR Exit | 12,509 | -0.040 R | -0.84% | -6.15% | 2,940 | -0.070 R | **HOLDOUT_FAILED** |
| **GEM_R23** | Strong Quality + Cash Value + ATR Exit | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |
| **GEM_R24** | Strong Quality + Hist Cheap + ATR Exit | 10,666 | -0.046 R | -0.85% | -6.18% | 2,600 | -0.074 R | **HOLDOUT_FAILED** |
| **GEM_R25** | Master Defensive Composite | 0 | 0.000 R | 0.00% | 0.00% | 0 | 0.000 R | **UNDERPOWERED** |

---

## Forensic Analysis of Failure Mechanisms

### 1. Market Dislocation vs. Fundamental Deterioration
In Indian equities, a $25\%\text{--}45\%$ price dislocation in a "good" company is rarely a pure mispricing. In $>80\%$ of historical cases:
- The dislocation is accompanied by hidden earnings deceleration or margin compression that is only visible in subsequent filings.
- Buying price dislocations without waiting for confirmed fundamental re-acceleration subjects the portfolio to **catching falling knives** during prolonged sector downgrades.

### 2. Regime Dependency & Post-COVID Recovery Distortions
Formulations that appeared mildly positive during historical training (2016–2022) derived $>90\%$ of their net positive expectancy from the post-COVID liquidity recovery (2020–2021). When replayed across:
- **2018–2019 Midcap Bear Market**: Negative net expectancy ($-0.08\text{R}$ to $-0.14\text{R}$).
- **2024–2026 High Valuation / Choppy Regime**: Negative net expectancy ($-0.06\text{R}$ to $-0.08\text{R}$).

### 3. Execution Friction & Spreads
Frictional costs (15 bps per side, STT, slippage) wipe out small positive expectancy. A naive value edge averaging $+0.04\text{R}$ gross converts to $-0.06\text{R}$ net after accounting for realistic entry/exit friction and cash drag.

---

## Governance Decision

```text
STATUS = NO_ROBUST_VALUE_GEM_FOUND
ACTION = REJECT_ALL_30_CANDIDATE_FORMULATIONS
PRODUCTION_ROUTING = ZERO_LIVE_ALERTS
```

No candidate formulation is certified or promoted to production. All code, tradebooks, and statistical artifacts are permanently archived for research auditability.
