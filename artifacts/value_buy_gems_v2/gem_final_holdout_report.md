# MASTER VALUE GEM UNTOUCHED HOLDOUT REPORT (2026-01-01 TO 2026-09-27)

**Execution Date**: 2026-09-28  
**Holdout Window**: 2026-01-01 to 2026-09-27 (270 Days)  
**Holdout Trading Days**: 184 Sessions  
**Target Universe**: Certified Clean Indian Equity Universe (886 Symbols)  
**Execution Protocol**: Equal-Weight Sizing (5%), Max 20 Positions, Next-Day Open Execution, 15 bps Friction  

---

## 1. Governance Policy & Auditability

The 2026 holdout period (`2026-01-01` to `2026-09-27`) was strictly frozen and remained completely untouched during model specification and candidate parameter selection.

Per system invariants:
- **Point-in-Time Integrity**: Signal generation on date $T$ used strictly data known at $T$.
- **Friction Enforced**: Every trade included 15 bps round-trip total transaction costs (7.5 bps entry / 7.5 bps exit, incorporating brokerage, STT, exchange fees, slippage).
- **Zero SNOOPING / ZERO PARAMETER RETUNING**: No candidate formulation was modified after evaluating holdout performance.
- **Metric Separation**: Trade-level Mean Net R measures average trade expectancy normalized by initial risk, whereas Portfolio CAGR reflects actual compounded account growth subject to cash drag, slot constraints (20 slots), and trading frequency.

---

## 2. Master Holdout Performance Matrix

| Candidate ID | Holdout Trade Count ($N$) | Holdout Win Rate (%) | Holdout Mean Net R | 95% CI Lower Bound | 95% CI Upper Bound | Max Holdout Drawdown (%) | Governance State |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **VALUE_GEM_CORE_V1 (Baseline)** | 3,918 | 38.2% | **-0.070 R** | -0.082 R | -0.056 R | -12.8% | **REJECTED_BASELINE** |
| **CONTROL_1** | 8,188 | 41.5% | **-0.020 R** | -0.028 R | -0.011 R | -9.8% | **HOLDOUT_FAILED** |
| **CONTROL_2** | 6,123 | 39.8% | **-0.045 R** | -0.054 R | -0.035 R | -7.4% | **HOLDOUT_FAILED** |
| **CONTROL_3** | 6,719 | 39.1% | **-0.044 R** | -0.053 R | -0.034 R | -8.3% | **HOLDOUT_FAILED** |
| **CONTROL_4** | 3,918 | 38.2% | **-0.053 R** | -0.064 R | -0.041 R | -12.8% | **HOLDOUT_FAILED** |
| **CONTROL_5** | 4,028 | 40.1% | **-0.046 R** | -0.054 R | -0.037 R | -6.7% | **HOLDOUT_FAILED** |
| **GEM_R1** | 3,429 | 37.4% | **-0.063 R** | -0.073 R | -0.052 R | -6.8% | **HOLDOUT_FAILED** |
| **GEM_R2** | 2,101 | 36.8% | **-0.066 R** | -0.078 R | -0.053 R | -5.9% | **HOLDOUT_FAILED** |
| **GEM_R3** | 2,101 | 36.8% | **-0.066 R** | -0.078 R | -0.053 R | -5.9% | **HOLDOUT_FAILED** |
| **GEM_R5** | 3,662 | 37.1% | **-0.062 R** | -0.072 R | -0.051 R | -6.6% | **HOLDOUT_FAILED** |
| **GEM_R6** | 3,662 | 37.1% | **-0.062 R** | -0.072 R | -0.051 R | -6.6% | **HOLDOUT_FAILED** |
| **GEM_R7** | 3,319 | 36.9% | **-0.064 R** | -0.074 R | -0.053 R | -6.7% | **HOLDOUT_FAILED** |
| **GEM_R9** | 2,101 | 36.8% | **-0.066 R** | -0.078 R | -0.053 R | -5.9% | **HOLDOUT_FAILED** |
| **GEM_R11** | 2,900 | 36.2% | **-0.069 R** | -0.079 R | -0.058 R | -7.2% | **HOLDOUT_FAILED** |
| **GEM_R12** | 2,920 | 36.5% | **-0.065 R** | -0.075 R | -0.054 R | -5.8% | **HOLDOUT_FAILED** |
| **GEM_R14** | 2,920 | 36.5% | **-0.065 R** | -0.075 R | -0.054 R | -5.8% | **HOLDOUT_FAILED** |
| **GEM_R17** | 2,712 | 36.1% | **-0.069 R** | -0.080 R | -0.057 R | -5.9% | **HOLDOUT_FAILED** |
| **GEM_R19** | 2,865 | 36.5% | **-0.065 R** | -0.076 R | -0.054 R | -5.7% | **HOLDOUT_FAILED** |
| **GEM_R21** | 1,772 | 34.9% | **-0.080 R** | -0.093 R | -0.066 R | -6.2% | **HOLDOUT_FAILED** |
| **GEM_R22** | 2,940 | 35.8% | **-0.070 R** | -0.081 R | -0.058 R | -6.2% | **HOLDOUT_FAILED** |
| **GEM_R24** | 2,600 | 35.2% | **-0.074 R** | -0.086 R | -0.061 R | -6.2% | **HOLDOUT_FAILED** |

*Note: Formulations `GEM_R4`, `GEM_R8`, `GEM_R10`, `GEM_R13`, `GEM_R16`, `GEM_R18`, `GEM_R20`, `GEM_R23`, `GEM_R25` had $N=0$ trades in 2026 due to over-constrained FCF/growth filters.*

---

## 3. Key Observations & Failure Analysis

1. **Zero Positive Candidates in Holdout**:
   Not a single candidate out of the 30 tested achieved a positive mean net R in the 2026 holdout period.
   The 95% bootstrap confidence interval lower bound was strictly $< 0$ across all active formulations.

2. **Negative Expectancy Across All Valuation Angles**:
   - **Absolute Low PE**: Mean R $-0.066\text{R}$
   - **Historical Percentile PE**: Mean R $-0.062\text{R}$
   - **Peer Relative PE**: Mean R $-0.062\text{R}$
   - **Recovery / Dislocation**: Mean R $-0.069\text{R}$

3. **Conclusion on the Indian Value Dislocation Edge**:
   Buying price dislocations ($25\%\text{--}45\%$ drawdowns) in Indian equities based on valuation metrics generates systematic negative drift unless accompanied by explicit breakout momentum and market regime tailwinds (e.g. Bull Market post-COVID expansion).

---

## 4. Governance Verdict

```text
HOLDOUT_RESULT = ALL_CANDIDATES_FAILED
CERTIFICATION = REJECTED
PRODUCTION_STATUS = DECOMMISSIONED (ZERO LIVE ALERTS)
```
