# Wave 3B — Candidate Rule 2×2 Confusion Matrix Report

Comprehensive classification and economic payoff metrics for candidate hypothesis filters on verified baseline signals.

## 1. Sector Headwind Filter (`W3_SEC_001`: Block `sector_status == 'HEADWIND'`)

### 2×2 Contingency Matrix
| | Pass Filter (Retained) | Fail Filter (Filtered) | Total |
|---|---|---|---|
| **Actual Winner** | **TP = 44** | FN = 0 | 44 |
| **Actual Loser** | FP = 26 | **TN = 0** | 26 |
| **Total** | 70 | 0 | 70 |

### Diagnostic Classification Metrics
- **Winner Retention Rate:** **100.0%** (44/44)
- **Loser Elimination Recall:** **0.0%** (0/26)
- **Specificity:** 0.0%
- **Precision:** 62.86%
- **Balanced Accuracy:** **50.0%**
- **Trade Opportunity Retention:** **100.0%**
- **Expected R (Before Filter):** +0.43R
- **Expected R (After Filter):** **+0.43R** ($\Delta = +0.00\text{R}$)

---

## 2. High Volume Breakout Filter (`W3_VOL_001`)

### 2×2 Contingency Matrix
| | Pass Filter (Retained) | Fail Filter (Filtered) | Total |
|---|---|---|---|
| **Actual Winner** | **TP = 44** | FN = 0 | 44 |
| **Actual Loser** | FP = 0 | **TN = 26** | 26 |
| **Total** | 44 | 26 | 70 |

### Diagnostic Classification Metrics
- **Winner Retention Rate:** **100.0%**
- **Loser Elimination Recall:** **100.0%**
- **Trade Opportunity Retention:** **62.86%**
- **Expected R Delta:** **+-0.43R**

---

## Wave 3C Frozen Promotion Governance Status
> [!IMPORTANT]
> **Promotion Gate Frozen:** Candidate rules remain in read-only validation status. Live production execution logic is **100% untouched**.