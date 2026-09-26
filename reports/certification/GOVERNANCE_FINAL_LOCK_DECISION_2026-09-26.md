# FORMAL PRODUCTION GOVERNANCE & LOCK DECISION MEMORANDUM

**Document ID:** `GOV-MEMO-2026-09-26`  
**Date:** 2026-09-26 20:45:00 IST  
**Evidence Source:** `TEMPORAL_REPLICATION_2026-09-26` (80,288 Real Upstox Historical Trades across 10 Years, 2016–2026)  
**Governance Scope:** Candidate Scanners (`TECHNICAL`, `ACCUMULATION`, `PULLBACK`, `EOD`) + Decommissioned Families  
**Mandate:** Zero Strategy Optimization · Zero Retrospective Fitting · Strict Anti-Pooled-Bias Enforcement  

---

## 1. Scanner-by-Scanner Authoritative Governance Record

| Scanner | Evidence-Supported Regime | Multi-Year Replications | Quarterly Robustness | Verified Status | Final Governance Verdict | Production Active State | Live Alert Eligibility |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`TECHNICAL`** | **`BULL`** | 4 / 4 Positive | 4 / 4 Positive | ✅ Replicated (All CIs > 0) | **ELIGIBLE FOR FINAL LOCK** | **ACTIVE ONLY IN BULL** (Suppressed in SIDEWAYS/BEAR) | **Live Alerts in BULL ONLY** |
| **`ACCUMULATION`** | **`BEAR`** | 4 / 4 Positive | 3 / 4 Positive | ⚠️ Persistent Q1 Drag (-0.060R) | **BLOCKED (Q1 Seasonal Weakness)** | **INACTIVE (FAIL-CLOSED)** | **ZERO Live Alerts** |
| **`PULLBACK`** | **`SIDEWAYS`** | 3 / 4 Positive | 3 / 4 Positive | ⚠️ Modern Decay (CI crosses 0) | **BLOCKED (Edge Compression)** | **INACTIVE (FAIL-CLOSED)** | **ZERO Live Alerts** |
| **`EOD`** | **`NONE`** | Underpowered | Underpowered | 🛑 N = 67/120/64 Holdouts | **BLOCKED (Underpowered)** | **INACTIVE (FAIL-CLOSED)** | **ZERO Live Alerts** |
| **`DECOMMISSIONED`** *(All 6 Families)* | **`NONE`** | N/A | N/A | 🛑 Failed Certification | **PERMANENTLY SILENCED** | **DECOMMISSIONED** | **ZERO Live Alerts** |

---

## 2. In-Depth Analysis: TECHNICAL × BULL Final Lock Decision

### A. Empirical Battery Recap
- **Total Trades:** 11,575 real Upstox trades across 10 years (2016–2026).
- **Temporal Replications:**
  - `Cell 1 (2016–2018)`: $N = 1,652$ | Mean Net R = **+0.2197R** (95% CI: `[+0.1660, +0.2731]`, $p = 0.0050$)
  - `Cell 2 (2019–2021)`: $N = 3,472$ | Mean Net R = **+0.2327R** (95% CI: `[+0.1956, +0.2701]`, $p = 0.0001$)
  - `Cell 3 (2022–2024)`: $N = 5,443$ | Mean Net R = **+0.2206R** (95% CI: `[+0.1896, +0.2468]`, $p = 0.0001$)
  - `Cell 4 (2025–2026)`: $N = 1,008$ | Mean Net R = **+0.1458R** (95% CI: `[+0.0792, +0.2126]`, $p = 0.0001$)
- **Quarterly Robustness:** Q1 (+0.206R), Q2 (+0.296R), Q3 (+0.134R), Q4 (+0.261R).
- **Holdout Statistical Proof:** Holdout Arm B CI_low = **+0.1114R**, Delta CI_low = **+0.0186R**, Paired $p = 0.0037$.
- **Concentration:** Top-cell PnL share = 47.7% ($< 60\%$ concentration gate).

### B. Final Production Lock Policy Assessment
Under Section 13 of the Mandatory Temporal Replication Gate, a strategy qualifies for permanent production lock if:
1. Data integrity passes (Upstox API, native fields, timezone, causality). $\rightarrow$ **PASS**
2. End-to-end performance passes (Arm B CI > 0, Delta CI > 0, paired p < 0.05). $\rightarrow$ **PASS**
3. Multi-year replication passes (all 4 cells positive, all quarters positive, low dispersion). $\rightarrow$ **PASS**

### C. Activation Routing Specification
When formally unlocked:
- `CERTIFIED_PRODUCTION_SCANNERS = {"TECHNICAL"}`
- `REGIME_ROUTING_MATRIX["TECHNICAL"]["BULL"] = "CERTIFIED_FOR_PRODUCTION"`
- `REGIME_ROUTING_MATRIX["TECHNICAL"]["SIDEWAYS"] = "NOT_CERTIFIED"`
- `REGIME_ROUTING_MATRIX["TECHNICAL"]["BEAR"] = "NOT_CERTIFIED"`
- **Live alert routing:** Live alerts are generated **only when the point-in-time macro regime is BULL**.
- **Suppression:** In SIDEWAYS or BEAR, TECHNICAL is strictly suppressed with:
  `"Suppressed: TECHNICAL evidence supports BULL only; current regime is {current_regime}."`

---

## 3. In-Depth Analysis: ACCUMULATION × BEAR Q1 Seasonal Drag

### A. Empirical Findings
- **Total BEAR Trades:** 2,775
- **Q1 Sample:** $N = 770$ (27.7% of all sample trades) | Mean Net R = **-0.0604R**
- **Non-Q1 Sample:** $N = 2,005$ | Mean Net R = **+0.1783R**
- **Seasonal Drag:** **-0.2387R** differential against non-Q1 periods.

### B. Multi-Year Cell Breakdown of Q1
- `Cell 1 Q1 (2016–2018)`: $N = 184$ | Mean Net R = **-0.0812R**
- `Cell 2 Q1 (2019–2021)`: $N = 191$ | Mean Net R = **-0.0450R**
- `Cell 3 Q1 (2022–2024)`: $N = 127$ | Mean Net R = **-0.0120R**
- `Cell 4 Q1 (2025–2026)`: $N = 268$ | Mean Net R = **-0.0784R**

### C. Structural & Economic Diagnosis
1. **Consistency:** The Q1 negative expectancy is **not an isolated historical accident**; it manifests across every single multi-year observation window over the last 10 years.
2. **Economic Driver:** In the Indian markets, January–March (Q1) coincides with Union Budget positioning, advance tax outflows, corporate fiscal year-end NAV squaring, and heavy institutional institutional rebalancing. Delivery accumulation breakouts during bear markets in Q1 experience repeated liquidity traps where early breakout thrusts are met by institutional selling into quarter-end illiquidity.
3. **Portfolio Impact:** While full-year BEAR performance remains positive (+0.112R), deploying ACCUMULATION in Q1 guarantees negative expected return and substantially exacerbates portfolio drawdowns.

### D. Governance Policy & Rule
- **Zero Retuning Adherence:** Under no circumstances should scanner thresholds, ATR multipliers, or stops be retrospectively fitted to mask Q1 weakness.
- **Verdict:** **LOCKED / BLOCKED FROM PRODUCTION**.
- ACCUMULATION × BEAR remains in `UNDER_CERTIFICATION` with mandatory warning:
  `"Evidence-supported regime: BEAR. Warning: Q1 seasonal weakness. Current production authorization: NOT YET UNLOCKED."`

---

## 4. In-Depth Analysis: PULLBACK × SIDEWAYS Temporal Decay

### A. Empirical Progression
- `Cell 1 (2016–2018)`: Mean Net R = **+0.2082R** ($N = 2,086$)
- `Cell 2 (2019–2021)`: Mean Net R = **+0.1384R** ($N = 4,185$)
- `Cell 3 (2022–2024)`: Mean Net R = **+0.1069R** ($N = 6,196$)
- `Cell 4 (2025–2026)`: Mean Net R = **+0.0147R** ($N = 3,329$) | 95% Bootstrap CI: `[-0.0170R, +0.0496R]`

### B. Statistical Significance of Decay
- Prior Period (2016–2024 Pooled): Mean Net R = **+0.1345R** ($N = 12,467$)
- Recent Period (2025–2026): Mean Net R = **+0.0147R** ($N = 3,329$)
- Welch's Two-Sample t-test: $t = 6.42$, $p = 1.48 \times 10^{-10}$ ($p < 0.001$).
- **Statistical Interpretation:** The decline is highly statistically significant. The modern market expectancy is compressed by **`-0.1198R`** relative to historical baseline.

### C. Is the Edge Dead or Merely Compressed?
- **Gross Expectancy:** In 2025–26, gross R is +0.038R.
- **Round-Trip Friction:** Transaction friction (brokerage, STT, turnover tax, GST, stamp duty) averages ~0.023R.
- **Net Expectancy:** +0.0147R with a 95% confidence interval spanning negative territory (`CI_low = -0.0170R`).
- **Diagnosis:** The statistical edge has **compressed to post-cost breakeven**. Increased quantitative participation and algorithmic execution on standard EMA pullback setups in sideways markets have eroded continuation probability.

### D. Governance Policy & Rule
- Under Section 12 of the Anti-Pooled-Bias Protocol:
  > *"If the modern cell confidence interval crosses zero, the strategy suffers from temporal edge decay and must not be promoted."*
- **Zero Retuning Adherence:** No retrospective parameter widening or indicator swapping permitted.
- **Verdict:** **LOCKED / BLOCKED FROM PRODUCTION**.
- Scanner Health displays:
  `"Evidence-supported regime: SIDEWAYS. Warning: recent 2025–26 edge compression. Status: DO NOT PERMANENTLY LOCK / CERTIFICATION PENDING."`

---

## 5. In-Depth Analysis: EOD Independent Evidence Program

### A. Current Power Deficit
- 10-Year Trade Total: **1,768** across all regimes (BULL: 907, SIDEWAYS: 516, BEAR: 339).
- Holdout Sample Sizes: BULL = 67, SIDEWAYS = 120, BEAR = 64.
- Confidence Interval Widths: $> 0.40\text{R}$, crossing zero in all three regimes.
- **Statistical Power Deficit:** At $\Delta = +0.05\text{R}$ and $\sigma = 1.0\text{R}$, achieving statistical power $\beta = 0.80$ ($\alpha = 0.05$) requires $N \approx 3,140$ independent observations.
- EOD is currently underpowered by a factor of $\approx 25\times$.

### B. Mandatory Criteria to Exit `UNDER_CERTIFICATION`
1. Minimum 500 independent historical trades per temporal cell.
2. Holdout Arm B CI_low > 0.0 with 95% bootstrap confidence.
3. Holdout Delta CI_low > 0.0 (dynamic beats fixed control).
4. Paired permutation $p < 0.05$.
5. Accumulation of at least **~2,500 additional causal observations** without parameter modification.
- **Verdict:** **UNDERPOWERED / UNDER CERTIFICATION**. Production active: **None**. Zero live alerts.

---

## 6. Execution Protocol: Production Activation Specification

```python
# Authoritative Production State when TECHNICAL is approved for Live Unlock:
CERTIFIED_PRODUCTION_SCANNERS = {"TECHNICAL"}

REGIME_ROUTING_MATRIX = {
    "TECHNICAL": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",  # Generates live alerts in BULL only
        "SIDEWAYS": "NOT_CERTIFIED",         # Suppressed
        "BEAR": "NOT_CERTIFIED"              # Suppressed
    },
    "PULLBACK": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "UNDER_CERTIFICATION",   # Suppressed (Edge compression)
        "BEAR": "NOT_CERTIFIED"
    },
    "ACCUMULATION": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "UNDER_CERTIFICATION"        # Suppressed (Q1 seasonal weakness)
    },
    "EOD": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "NOT_CERTIFIED"              # Suppressed (Underpowered)
    }
}
```

### Safety & Invariant Verification:
1. `save_alert_if_new()` permits alerts ONLY for `TECHNICAL` when `bayesian_regime == "BULL"`.
2. All alerts for `PULLBACK`, `ACCUMULATION`, `EOD`, and decommissioned scanners remain strictly dropped.
3. If current macro regime is `SIDEWAYS` or `BEAR`, `TECHNICAL` alerts are intercepted and dropped with `REGIME_NOT_CERTIFIED_TECHNICAL_{regime}`.

---

## 7. Git & Deployment Status Verification

- **Local Repository Status:**
  - All modified source files (`governance_registry.py`, `database.py`, `dashboard_server.py`, `admin_dashboard.html`, `test_scanner_health_regime_mapping.py`) have been syntax-verified (`py_compile`), tested, and confirmed clean.
  - Zero unbound or shadow variables.
- **Remote Push Status:**
  - Remote Git repository connection is currently disabled in the local sandboxed environment.
  - In strict compliance with operating integrity rules: **No remote push is claimed**. All changes are staged in local working directory and ready for push upon remote network/credential activation.
