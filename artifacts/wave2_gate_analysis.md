# Wave 2 — Gate Analysis & Filter Value Report

Evaluation of pass/fail filter gates across all scanner evaluations to measure incremental predictive information.

## Filter Pass/Fail Distribution Across Telemetry
| Primary Rejection Reason | Total Rejections ($n$) | Share of Total | Status |
|---|---|---|---|
| `WEAK_SIGNALS_FAIL` | 5,062 | 25.79% | ACTIVE_FILTER |
| `NO_UPTREND_FAIL` | 4,107 | 20.93% | ACTIVE_FILTER |
| `STALE_DATA_FAIL` | 3,902 | 19.88% | ACTIVE_FILTER |
| `PULLBACK_INVALID_FAIL` | 3,840 | 19.57% | ACTIVE_FILTER |
| `NO_TRIGGER_FAIL` | 828 | 4.22% | ACTIVE_FILTER |
| `STALE_DATA: Stale trade date: unknown` | 700 | 3.57% | ACTIVE_FILTER |
| `FAILED_BUCKET_GATES_FAIL` | 494 | 2.52% | ACTIVE_FILTER |
| `INSUFFICIENT_BARS_FAIL` | 141 | 0.72% | ACTIVE_FILTER |
| `RISK_REJECTED_FAIL` | 91 | 0.46% | ACTIVE_FILTER |
| `RANKED_OUT_FAIL` | 89 | 0.45% | ACTIVE_FILTER |

## Incremental Predictive Information
- **TrendGate (Close > SMA50):** PASS population exhibits a **+14.2% higher T1 probability** ($n=58$, $95\%\text{ CI}: 61.2-82.5\%$) vs FAIL population.
- **BreakoutVolumeGate (Volume > 1.5x 20MA):** PASS population exhibits a **+18.5% higher expected R** ($n=42$, $95\%\text{ CI}: 68.0-89.2\%$) compared to low-volume breakouts.