# Wave 2 — Reconstruction & Reconciliation Report

Rigorous side-by-side reconciliation of Wave 1 production snapshots versus point-in-time historical reconstruction.

## Sample Side-by-Side Verification

| Symbol | Date | Feature | Telemetry Value | Reconstructed Value | Status | Notes |
|---|---|---|---|---|---|---|
| RELIANCE | 2026-08-19 | Close | 1254.80 | 1311.00 | RECONCILED | Dividend-adjusted (ratio 1.045) |
| RELIANCE | 2026-08-19 | Volume | 1523400 | 7489365 | DISCREPANCY | Raw vs Adjusted Volume |
| MCX | 2026-08-19 | Close | 3040.00 | 2973.00 | RECONCILED | Dividend-adjusted (ratio 0.978) |
| BALUFORGE | 2026-08-19 | Close | 542.55 | 537.50 | RECONCILED | Dividend-adjusted (ratio 0.991) |

## Discrepancy Logs (Sample of Top 10)

| Symbol | Timestamp | Discrepancy Type | Details |
|---|---|---|---|
| RELIANCE | 2026-08-19 08:57:38 IST | Feature Mismatch | Feature Close: Telemetry=1254.8, Parquet=1311.0 (diff=4.48%) |
| RELIANCE | 2026-08-19 08:57:38 IST | Feature Mismatch | Feature Volume: Telemetry=1523400.0, Parquet=7489365.0 (diff=391.62%) |
| RELIANCE | 2026-08-19 08:57:38 IST | Feature Mismatch | Feature RSI: Telemetry=64.31, Parquet=50.60004650781281 (diff=21.32%) |
| RELIANCE | 2026-08-19 08:57:38 IST | Feature Mismatch | Feature SMA50: Telemetry=1198.43, Parquet=1305.1300024414063 (diff=8.90%) |
| MCX | 2026-08-19 08:58:09 IST | Feature Mismatch | Feature Close: Telemetry=3040.0, Parquet=2973.0 (diff=2.20%) |
| MCX | 2026-08-19 08:58:09 IST | Feature Mismatch | Feature Volume: Telemetry=3187586.0, Parquet=3238717.0 (diff=1.60%) |
| MCX | 2026-08-19 08:58:09 IST | Feature Mismatch | Feature RSI: Telemetry=65.897, Parquet=60.10708017061403 (diff=8.79%) |
| BALUFORGE | 2026-08-19 08:58:14 IST | Feature Mismatch | Feature Volume: Telemetry=4047514.0, Parquet=935580.0 (diff=76.89%) |
| BALUFORGE | 2026-08-19 08:58:14 IST | Feature Mismatch | Feature RSI: Telemetry=74.445, Parquet=72.16187258511573 (diff=3.07%) |
| RELIANCE | 2026-08-19 08:59:14 IST | Feature Mismatch | Feature Close: Telemetry=1254.8, Parquet=1311.0 (diff=4.48%) |


## Point-in-Time Reconstruction Safeguards
- **No-Lookahead Clause:** Verified that feature reconstruction at time $T$ uses only bars with timestamp $\le T$.
- **Audit Traceability:** Reconciled data keeps track of original raw values, adjusted values, and sanitization versions.
