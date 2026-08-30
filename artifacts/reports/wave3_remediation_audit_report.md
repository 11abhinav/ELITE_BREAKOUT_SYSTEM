# Wave 3B — Replay Engine & Accounting Reconciliation Audit

**Audit Timestamp:** 2026-08-30 17:32:47 IST  
**Dataset Hash:** `86e71bacec2fe08a53615e0083b007b1c1e376e72a797da47d1161c2934713d0`  
**Independence Unit:** `symbol_x_trading_date`  

## 1. Multi-TF MFE Anomaly Root-Cause Audit
A critical P0 investigation into the $1123.60\text{R}$ Multi-TF MFE anomaly identified a **Price Scale Mismatch** caused by mock telemetry values evaluated against real equity price series:

| Evaluation ID | Symbol | Timestamp | Raw Entry | Raw SL | Underlying Close | Scale Ratio | Evaluated MFE | Audit Status | Root Cause Detail |
|---|---|---|---|---|---|---|---|---|---|
| `eval_006525_RELIANCE_run_1787118935` | **RELIANCE** | 2026-08-19 11:25:35 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 794.73R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |
| `eval_006527_TCS_run_1787118940` | **TCS** | 2026-08-19 11:25:40 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 1452.47R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |
| `eval_007524_RELIANCE_run_1787119269` | **RELIANCE** | 2026-08-19 11:31:09 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 794.73R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |
| `eval_007526_TCS_run_1787119271` | **TCS** | 2026-08-19 11:31:11 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 1452.47R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |
| `eval_008526_RELIANCE_run_1787119804` | **RELIANCE** | 2026-08-19 11:40:04 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 794.73R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |
| `eval_008528_TCS_run_1787119808` | **TCS** | 2026-08-19 11:40:08 IST | ₹129.50 | ₹128.00 | ₹129.50 | 1.0 | 1452.47R | `REPLAY_INVALID_ZERO_TARGET_DISTANCE` | Normal |

### Key Replay Engine Finding
- **Scale Mismatch:** Telemetry recorded hardcoded entry/SL levels (`₹129.5 / ₹128.0`), while underlying equities (`RELIANCE ~₹1320`, `TCS ~₹2300`) were evaluated on actual price data.
- **Target Distance Collapse:** `entry_price == target_price == 129.5` resulted in $0.0\text{R}$ target distance with instant bar-0 exit, while candle highs produced artificial MFE explosion.
- **Remediation Action:** All 14 Multi-TF records are classified as **`REPLAY_INVALID_RISK_SCALE_MISMATCH`** and excluded from valid trade statistics rather than artificially clamped.

---

## 2. Zero-Delta Telemetry Accounting Reconciliation
- **Total Telemetry Evaluations:** 20,766
- **Reconciliation Status:** `EXACT_ZERO_DELTA_VERIFIED` (Zero unexplained records)

### Terminal Decisions Reconciliation
| Terminal Decision | Record Count ($n$) | Share of Total | Reconciliation Delta |
|---|---|---|---|
| `REJECTED` | 19,626 | 94.51% | 0 |
| `SELECTED` | 1,140 | 5.49% | 0 |

### Rejection Reason Complete Breakdown ($n=19,626$)
| Rejection Category | Record Count ($n$) | Share of Total Rejections | Category Group |
|---|---|---|---|
| `WEAK_SIGNALS_FAIL` | 5,062 | 25.79% | Top Gate (n >= 80) |
| `NO_UPTREND_FAIL` | 4,107 | 20.93% | Top Gate (n >= 80) |
| `STALE_DATA_FAIL` | 3,902 | 19.88% | Top Gate (n >= 80) |
| `PULLBACK_INVALID_FAIL` | 3,840 | 19.57% | Top Gate (n >= 80) |
| `NO_TRIGGER_FAIL` | 828 | 4.22% | Top Gate (n >= 80) |
| `STALE_DATA: Stale trade date: unknown` | 700 | 3.57% | Top Gate (n >= 80) |
| `FAILED_BUCKET_GATES_FAIL` | 494 | 2.52% | Top Gate (n >= 80) |
| `INSUFFICIENT_BARS_FAIL` | 141 | 0.72% | Top Gate (n >= 80) |
| `RISK_REJECTED_FAIL` | 91 | 0.46% | Top Gate (n >= 80) |
| `RANKED_OUT_FAIL` | 89 | 0.45% | Top Gate (n >= 80) |
| *Long-Tail Residual Rejections (81 distinct reasons)* | 372 | 1.9% | Long-Tail & Test Mocks |

**Exact Zero-Delta Check:** $\text{Total Rejections} - (\text{Top Categories} + \text{Long-Tail Residual}) = 0$

---

## 3. Rejection Gate Semantic Reclassification
All rejected candidates without production-equivalent entry, stop loss, and target prices cannot be replayed counterfactually.
Under the amended governance standard, their status is reclassified from `PROTECTIVE (Keep)` to **`UNTESTABLE WITH CURRENT DATA`**.