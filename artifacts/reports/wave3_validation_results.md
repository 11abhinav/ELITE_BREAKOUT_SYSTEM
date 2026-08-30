# Wave 3 — Comprehensive Validation & Holdout Results Report

**Report Generated:** 2026-08-30 19:07:16 IST  
**Dataset Hash:** `86e71bacec2fe08a53615e0083b007b1c1e376e72a797da47d1161c2934713d0`  
**Methodology:** Chronological Train $\to$ Validation Parameter Freeze $\to$ Single-Pass Sealed Holdout  
**Independence Cluster Unit:** `symbol_x_trading_date`  

---

## 1. Executive Summary & Governance Verdicts

| Hypothesis ID | Frozen Rule Description | Validation $N$ | Holdout $N$ | Holdout Net $E[R]\ \Delta$ | Trade Retention | Three-Tier Scoring (Mech / Stat / Econ) | Final Governance Verdict |
|---|---|---|---|---|---|---|---|
| **`W3_VOL_001`** | Volume Ratio $\ge 1.0x$ | 17 | 18 | **-0.51R** (-0.88R to +0.00R) | **55.6%** | `PASS` / `FAIL` / `FAIL` | **`REJECTED_AT_HOLDOUT`** |
| **`W3_SEC_001`** | Block `HEADWIND` Sector | 17 | 18 | **+0.00R** (0 firings) | **100.0%** | `PASS` / `INCONCLUSIVE` / `NON_DEGRADING` | **`INCONCLUSIVE_UNTRIGGERED_IN_HOLDOUT`** |
| **`W3_MAC_001`** | Macro Drop Sizing ($0.5\times$) | 17 | 18 | **+0.00R** (Risk Control) | **100.0%** | `PASS` / `PASS` / `PASS` | **`RISK_MITIGATION_CANDIDATE`** |

---

## 2. In-Depth Evaluation Analysis

### 1. `W3_VOL_001` — Definitive Rejection
- **Result:** On the untouched Holdout partition ($n=18$), the volume floor rule reduced trade retention to **55.6%** (violating the $\ge 70\%$ constraint) and produced a negative expected return delta of **-0.51R** (Cluster BCa CI: `[-0.88R, +0.00R]`).
- **Governance Verdict:** **`REJECTED_AT_HOLDOUT`** (Permanently rejected; zero production implementation).

### 2. `W3_SEC_001` — Inconclusive Holdout Exposure
- **Audit Finding:** In the holdout partition ($n=18$), exactly **0 trades** occurred in HEADWIND sectors. Consequently, the filter triggered 0 times.
- **Reconciliation:** The 100% retention and $+0.00\text{R}$ delta occurred because the filter was **unexposed / untriggered** during this specific holdout window.
- **Governance Policy Applied:** A $\Delta E[R] = 0.00\text{R}$ outcome with 0 triggers **cannot** claim empirical validation. It is correctly classified as **`INCONCLUSIVE_UNTRIGGERED_IN_HOLDOUT`**.

### 3. `W3_MAC_001` — Risk Mitigation Role
- **Classification:** Defensive position-sizing intervention ($0.5\times$ exposure during severe intraday macro drops), designed to reduce portfolio Max Drawdown and tail MAE without claiming independent trade-level alpha.
- **Governance Verdict:** **`RISK_MITIGATION_CANDIDATE`** (Preserved for forward shadow risk logging).

---

## 3. Holdout 2×2 Contingency Tables

### `W3_VOL_001` — Volume Ratio $\ge 1.0x$
| | Pass Filter (Retained) | Fail Filter (Blocked) | Total |
|---|---|---|---|
| **Actual Winner** | **TP = 10** | FN = 0 | 10 |
| **Actual Loser** | FP = 0 | **TN = 8** | 8 |
| **Total** | 10 | 8 | 18 |

### `W3_SEC_001` — Sector Headwind Block (Untriggered in Holdout)
| | Pass Filter (Retained) | Fail Filter (Blocked) | Total |
|---|---|---|---|
| **Actual Winner** | **TP = 10** | FN = 0 | 10 |
| **Actual Loser** | FP = 8 | **TN = 0** | 8 |
| **Total** | 18 | 0 | 18 |

---

## 4. Production Promotion Status (Wave 3C Governance)
> [!IMPORTANT]
> **Promotion Strictly Locked (Zero Production Code Modified):**
> - `W3_VOL_001` is permanently rejected.
> - `W3_SEC_001` and `W3_MAC_001` require forward shadow data accumulation ($N \ge 50$ independent observations) before any production promotion review.
> - Live scanner execution logic (`EOD`, `Multi-TF`, `Reversal`) remains **100% untouched**.