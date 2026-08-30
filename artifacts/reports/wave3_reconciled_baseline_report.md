# Wave 3B — Reconciled Scanner Baseline Performance Report

Baseline evaluation using the standardized **Wilson Score 95% CI** and **Dependence-Aware Cluster Bootstrap (BCa)**.

## Scanner Performance Summary
| Scanner | Sample ($n$) | Sample Status | T1 Win Rate (Wilson 95% CI) | Expected R (Cluster BCa 95% CI) | MFE (BCa 95% CI) | MAE (BCa 95% CI) |
|---|---|---|---|---|---|---|
| **EOD** | 70 | `ELIGIBLE` ($n \ge 50$) | **62.86%** (51.15%–73.23%) | **+0.43R** (0.00R to 0.81R) | **0.72R** (0.15R to 1.64R) | **0.27R** (0.00R to 0.48R) |
| **MULTI_TF** | 14 | `REPLAY_INVALID_RISK` | *Invalidated* (Scale Mismatch) | *N/A (Excluded)* | *N/A (Excluded)* | *N/A (Excluded)* |
| **REVERSAL** | 1 | `INSUFFICIENT_SAMPLE` | 0.0% (0.00%–97.50%)* | -1.00R (Descriptive) | 0.00R | 16.46R |

### Governance Notes
- **Bootstrap Methodology:** `BCa` (Seed: `42`, Resamples: `100`, Clusters: `3`).
- **Degradation Status:** `ci_degraded = False`.
- **Subgroup Semantics:** Subgroups with $n < 50$ (such as REVERSAL $n=1$) are explicitly classified as `INSUFFICIENT_SAMPLE` and do not constitute statistical evidence against scanner viability.