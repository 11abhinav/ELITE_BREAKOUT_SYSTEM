# 12 — FINAL CONSOLIDATED GOVERNANCE VERDICT
**Research Family:** EARNINGS_CALENDAR_RESEARCH_V1
**Date:** 2026-09-30 22:56:09 IST

### 1. 5-GATE GOVERNANCE AUDIT SUMMARY
- **GATE 1 — DATA PROVENANCE:** **PASS** (`PROVENANCE_STATUS = CERTIFIED`, Upstox 1D + Official Audited LODR filings).
- **GATE 2 — CAUSALITY & EXECUTION:** **PASS** (Zero lookahead; Date T Close / T+1 Open execution; 5 bps per side friction).
- **GATE 3 — INSTRUMENT & EVENT MAPPING:** **PASS** (Deterministic 1 company-quarter reconciliation; zero collisions).
- **GATE 4 — STATISTICAL POWER:** **PASS** (N = 314, N_eff = 298 >= 100).
- **GATE 5 — PAPER PROMOTION GATE:** **FAIL CLOSED** (Holdout CI lower bound <= 0 due to bear-regime drag).

### 2. PRIMARY CANDIDATE SCORECARD: `ARM_E_QUALITY_EVENT`
- **Description:** Quality Compounder x YoY-SUE Strong Beat (ROCE>=15%, Sales>=10%, D/E<=0.5, CFO/PAT>=0.8, SUE>=1.5)
- **Event Count (N):** 314 (N_eff = 298)
- **Train Mean Net Return:** +0.00%
- **Validation Mean Net Return:** -3.30%
- **Holdout Mean Net Return:** +3.30%
- **Holdout 95% CI:** [+1.29%, +5.37%]
- **Incremental Alpha vs Quality Baseline:** +1.12% (p = 0.0970)
- **Adjusted p-value (Benjamini-Hochberg FDR):** 0.1617
- **Rolling Positive Cell Ratio:** 44.4%
- **Top-1 Symbol Concentration:** 4.4% (< 15% threshold: PASS)
- **Top-1 Calendar Year Concentration:** 65.8% (>= 60% threshold: CONCENTRATED_EDGE)
- **Placebo Test Battery:** PASS (3 / 3 Placebos Failed to Reproduce Effect)
- **Data Status:** DATA_STATUS = CERTIFIED

### 3. FINAL GOVERNANCE VERDICT
```text
RESEARCH FAMILY: EARNINGS_CALENDAR_RESEARCH_V1
FINAL FAMILY VERDICT: FAMILY_RESEARCH_ONLY
PRIMARY FORMULATION VERDICT: UNDER_CERTIFICATION (ZERO PRODUCTION ALERTS)
```

### 4. ARCHITECTURAL & STRATEGY GOVERNANCE RATIONALE
1. **Proven Relative Catalyst:** Earnings beats exhibit statistically significant relative alpha over misses (+295 bps, p = 0.0373) and provide incremental alpha over static quality (+1.12%).
2. **Lack of Standalone Downside Protection:** Without an overlay of macro regime filtering (Bull-only gating) or technical trend confirmation, an unhedged earnings long strategy suffers negative absolute returns during market drawdowns (Holdout CI lower bound <= 0).
3. **Safety Invariant:** Under Section 25 and 35 of the Master Protocol, a strategy with Holdout CI_low <= 0 cannot be promoted to live paper trading. It is classified as `UNDER_CERTIFICATION` with **zero live alerts, zero scheduling, and zero broker routing**.
