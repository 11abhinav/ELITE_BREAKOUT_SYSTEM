# MANDATORY TEMPORAL CERTIFICATION RECONCILIATION REPORT

**Evaluation Scope:** 80,288 Real Upstox Historical Trades (2016–2026)  
**Governance Release:** `TEMPORAL_REPLICATION_2026-09-26`  
**Current Governance Status:** **FAIL-CLOSED (0 LIVE PRODUCTION ALERTS)**  
**Audit Date:** 2026-09-26 20:22:19 IST  

---

### DATA PROVENANCE
Provider: Upstox
API: Upstox Historical V2 API
Exchange: NSE (National Stock Exchange of India)
Universe: Nifty 500 / MidSmallCap Dynamic Watchlist
Instrument resolution: Cash Equities (EQ)
Timeframe: Daily & 15m / Bhavcopy Stride=1
Date range: 2016-01-01 to 2026-09-26
Timezone: Asia/Kolkata (IST)
Rows: 80,288
Native fields: timestamp, open, high, low, close, volume, open_interest
Missing rows: 0
Duplicates: 0
Synthetic data: 0 (Strictly Prohibited)
Fallback providers: None
Dataset hash: 4a952ca20c529f6178535fdd55ec9aaa...
Provenance status: PROVENANCE_STATUS = CERTIFIED

---

## 1. Authoritative Reconciliation Matrix (Reported vs. Recomputed)

| Scanner | Regime | Reported N | Recomputed N | Reconciliation | Provenance | Leakage | Config Match | Replication Status | Governance Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TECHNICAL** | **BULL** | 11,575 | 11,575 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **TECHNICAL** | **SIDEWAYS** | 5,864 | 5,864 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **TECHNICAL** | **BEAR** | 2,927 | 2,927 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **PULLBACK** | **BULL** | 16,504 | 16,504 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **PULLBACK** | **SIDEWAYS** | 15,796 | 15,796 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **PULLBACK** | **BEAR** | 11,555 | 11,555 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **ACCUMULATION** | **BULL** | 6,800 | 6,800 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **ACCUMULATION** | **SIDEWAYS** | 4,075 | 4,075 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **ACCUMULATION** | **BEAR** | 2,775 | 2,775 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **EOD** | **BULL** | 907 | 907 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **EOD** | **SIDEWAYS** | 516 | 516 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |
| **EOD** | **BEAR** | 339 | 339 | **MATCH** | CERTIFIED | PASSED | MATCH | `PENDING` | `UNDER_CERTIFICATION` |

> **RECONCILIATION RESULT:** **100% MATCH** across all 12 Scanner × Regime pairs and all 48 temporal cells. Zero discrepancies between reported and recomputed metrics.

---

## 2. Red-Team Verification: TECHNICAL × BULL

- **Dataset Provenance & Schema:** Upstox NSE Cash EQ, SHA256 verified, timezone Asia/Kolkata.
- **Trade Counts & Multi-Year Cells:**
  - `Cell 1 (2016–2018)`: $N = 1,652$ | Mean Net R = **+0.2197R** (95% CI: `[+0.1660, +0.2731]`, Permutation $p = 0.0050$)
  - `Cell 2 (2019–2021)`: $N = 3,472$ | Mean Net R = **+0.2327R** (95% CI: `[+0.1956, +0.2701]`, Permutation $p = 0.0001$)
  - `Cell 3 (2022–2024)`: $N = 5,443$ | Mean Net R = **+0.2206R** (95% CI: `[+0.1896, +0.2468]`, Permutation $p = 0.0001$)
  - `Cell 4 (2025–2026)`: $N = 1,008$ | Mean Net R = **+0.1458R** (95% CI: `[+0.0792, +0.2126]`, Permutation $p = 0.0001$)
- **Quarterly Robustness:**
  - `Q1`: $N = 2,210$ | Mean Net R = **+0.2056R**
  - `Q2`: $N = 3,119$ | Mean Net R = **+0.2959R**
  - `Q3`: $N = 3,835$ | Mean Net R = **+0.1337R**
  - `Q4`: $N = 2,411$ | Mean Net R = **+0.2605R**
- **Dispersion & Concentration:** Top cell PnL share = **47.7%** ($< 60\%$ threshold). No temporal concentration flag.
- **Causality & Execution Integrity:** 
  - Duplicate trades: **0**
  - Lookahead/leakage: **0**
  - T+1 causal entry: **Confirmed**
  - Entry/exit friction applied: **Confirmed**
- **Configuration Match:** Config hash `4a11187c816bf6c9...` is 100% identical between research engine and production code.
- **Verdict:** **EVIDENCE CONFIRMED IN BULL**. Retained in `UNDER_CERTIFICATION` pending administrative sign-off. Production active: **None until final lock verification**.

---

## 3. Red-Team Verification: ACCUMULATION × BEAR & Q1 Seasonal Drag

- **Multi-Year Cells:**
  - `Cell 1 (2016–2018)`: $N = 663$ | Mean Net R = **+0.0402R**
  - `Cell 2 (2019–2021)`: $N = 673$ | Mean Net R = **+0.1356R**
  - `Cell 3 (2022–2024)`: $N = 458$ | Mean Net R = **+0.2065R**
  - `Cell 4 (2025–2026)`: $N = 981$ | Mean Net R = **+0.0975R**
  - Top-cell PnL share = **31.0%** (very low dispersion).
- **Investigation of Q1 Negative Result:**
  - Across all 10 years, Q1 produced $N = 770$ trades with Mean Net R = **-0.0604R**.
  - Non-Q1 trades ($N = 2,005$) generated Mean Net R = **+0.1783R**.
  - Net Q1 Drag: **-0.2387R** differential against non-Q1 periods.
  - **Q1 Breakdown by Multi-Year Cell:**
    - `Cell 1 Q1`: $N = 184$ | Mean Net R = **-0.0812R**
    - `Cell 2 Q1`: $N = 191$ | Mean Net R = **-0.0450R**
    - `Cell 3 Q1`: $N = 127$ | Mean Net R = **-0.0120R**
    - `Cell 4 Q1`: $N = 268$ | Mean Net R = **-0.0784R**
  - **Stability:** Q1 seasonal weakness is structurally persistent across all four temporal cells (every single cell exhibits Q1 drag).
- **Rule Adherence:** Scanner logic was **NOT modified** or fitted to suppress Q1.
- **Verdict:** **LOCK BLOCKED**. Scanner Health must display:
  `Evidence-supported regime: BEAR. Warning: Q1 seasonal weakness. Current production authorization: NOT YET UNLOCKED.`

---

## 4. Red-Team Verification: PULLBACK × SIDEWAYS Temporal Decay Analysis

- **Multi-Year Progression:**
  - `Cell 1 (2016–2018)`: Mean Net R = **+0.2082R** ($N = 2,086$)
  - `Cell 2 (2019–2021)`: Mean Net R = **+0.1384R** ($N = 4,185$)
  - `Cell 3 (2022–2024)`: Mean Net R = **+0.1069R** ($N = 6,196$)
  - `Cell 4 (2025–2026)`: Mean Net R = **+0.0147R** ($N = 3,329$) | 95% CI: `[-0.0170, +0.0496]`
- **Decay Quantification & Hypothesis Testing:**
  - Prior Pooled (2016–2024): Mean Net R = **+0.1345R** ($N = 12,467$)
  - Recent Period (2025–2026): Mean Net R = **+0.0147R** ($N = 3,329$)
  - Net Compression: **-0.1198R** drop in expectancy.
  - Welch's t-test: $t = 6.42$, $p = 1.48 	imes 10^-10$ ($p < 0.001$).
  - **Finding:** The 2025–26 decay is **statistically distinguishable** from prior periods. While positive in the aggregate point estimate, the 95% bootstrap confidence interval crosses zero (`CI_low = -0.0170R`), indicating that the statistical edge has compressed to near-zero in the modern market environment.
- **Rule Adherence:** Zero retuning.
- **Verdict:** **LOCK BLOCKED**. Scanner Health must display:
  `Evidence-supported regime: SIDEWAYS. Warning: recent 2025–26 edge compression. Status: DO NOT PERMANENTLY LOCK / CERTIFICATION PENDING.`

---

## 5. Red-Team Verification: EOD Power & Sample-Size Analysis

- **Historical Observations:**
  - 10-year trade total = **1,768** across all regimes (BULL: 907, SIDEWAYS: 516, BEAR: 339).
  - Annual volume ranges from 110 to 240 trades/year across the entire universe.
  - Quarterly volume: Q1 (420), Q2 (445), Q3 (478), Q4 (425).
- **Holdout Power Analysis:**
  - BULL holdout: $N = 67$ (95% CI width = 0.44R, crosses zero)
  - SIDEWAYS holdout: $N = 120$ (95% CI width = 0.38R, crosses zero)
  - BEAR holdout: $N = 64$ (95% CI width = 0.42R, crosses zero)
- **Observations Required:**
  - To achieve statistical power (beta = 0.80, alpha = 0.05) to certify a +0.05R delta over fixed control, EOD requires at least **~2,500 additional independent causal trades**.
- **Exit Conditions from `UNDER_CERTIFICATION`:**
  1. Minimum 500 independent trades per temporal cell.
  2. Holdout Arm B CI_low > 0.0.
  3. Holdout Delta CI_low > 0.0.
  4. Paired permutation $p < 0.05$.
- **Verdict:** **UNDERPOWERED / UNDER CERTIFICATION**. Production active: **None**. Zero live alerts.

---

## 6. Authoritative Production Governance State

```text
CERTIFIED_PRODUCTION_SCANNERS: EMPTY set()
UNDER_CERTIFICATION_SCANNERS: {"TECHNICAL", "PULLBACK", "ACCUMULATION", "EOD"}
DECOMMISSIONED_SCANNERS:      {"SHORT_COVERING", "5M_BREAKOUT", "MOMENTUM_IGNITION", 
                               "MOMENTUM_THRUST_REVERSAL", "MULTI_TF", "MULTI_TF_5M", 
                               "TECHNICAL_INTRADAY", "REVERSAL"}
LIVE_PRODUCTION_ALERTS:        0 (ZERO)
FAIL_CLOSED_GATE:              ACTIVE
```
