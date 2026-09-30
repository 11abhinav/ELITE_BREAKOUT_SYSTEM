# EARNINGS_SURPRISE_QUALITY_V2 — EVENT RECONSTRUCTION REPORT
Generated: 2026-09-30T14:26:46 IST

### STRATEGY SPECIFICATION (FROZEN)
- Signal: Year-over-Year EPS Surprise (YoY-SUE)
- SUE formula: (EPS_t - EPS_{t-4}) / std(prior 4 YoY surprises)
- Minimum quarterly observations: 9 (indices t-8 through t)
- Strong beat threshold: YoY_SUE >= 1.5
- Quality gate: ROCE≥15.0%, Sales CAGR≥10.0%, D/E≤0.5, CFO/PAT≥0.8
- Execution: T+1 Open, 5.0 bps friction, 60d hold

### DATA PROVENANCE
Provider: Screener.in (via PIT quarterly filings)
Database: pit_fundamentals_v1.db
DB SHA256: 1a107141eb447087ad03f6174b6a392f98449fa300d503a4ced8c2ba4792a7ea
Timestamp basis: LODR_STATUTORY_DEADLINE_CONSERVATIVE
Synthetic data: NONE
Fallback providers: NONE
PROVENANCE_STATUS = CERTIFIED_PIT_SCREENER

### COVERAGE SUMMARY
| Metric | Value |
|--------|-------|
| Symbols with ≥9 quarterly history | 685 |
| Events attempted | 1410 |
| DATA_INSUFFICIENT | 23 |
| CAUSALITY_VIOLATION | 0 |
| Quality data missing | 197 |
| Quality strategy fail | 819 |
| **Events with valid SUE** | **371** |
| → STRONG_BEAT (SUE≥1.5) | 139 |
| → WEAK_BEAT | 75 |
| → MISS | 71 |
| → NEUTRAL | 86 |
| Event window (first signal) | 2023-08-14 |
| Event window (last signal) | 2026-08-14 |

### TEMPORAL COVERAGE NOTE
With a 9-quarter minimum and the current DB ceiling of 13 quarters
(2023-Q2 to 2026-Q2), the first valid event fires when index 8 becomes
available. For symbols starting at 2023-Q3 (majority), first event date
is approximately 2025-Q3 (available ~Nov 2025). The backtest window
is therefore narrow (~Aug 2025 to Aug 2026 — approximately 12 months).

This narrow window is a governance constraint, not a bug. The block-bootstrap
evaluation must respect this temporal concentration.

### STRONG BEAT EVENTS (sample, first 20)
| Symbol | Period | Signal Date | YoY_SUE | Surprise | Sigma |
|--------|--------|-------------|---------|----------|-------|
| 3BBLACKBIO | 2025-12-31 | 2026-02-14 | 3.15 | 9.7100 | 3.0807 |
| ACE | 2026-06-30 | 2026-08-14 | 1.80 | 1.8200 | 1.0137 |
| ACUTAAS | 2026-06-30 | 2026-08-14 | 1.80 | 3.6600 | 2.0316 |
| ADOR | 2026-06-30 | 2026-08-14 | 1.51 | 18.1300 | 11.9936 |
| ADVAIT | 2025-12-31 | 2026-02-14 | 2.41 | 6.1100 | 2.5328 |
| AEROFLEX | 2026-06-30 | 2026-08-14 | 2.39 | 0.8700 | 0.3647 |
| AFFLE | 2025-12-31 | 2026-02-14 | 7.82 | 1.3500 | 0.1725 |
| AFFLE | 2026-06-30 | 2026-08-14 | 9.84 | 1.6200 | 0.1646 |
| AGIIL | 2025-12-31 | 2026-02-14 | 6.40 | 0.5800 | 0.0907 |
| AJANTPHARM | 2025-12-31 | 2026-02-14 | 2.92 | 3.2700 | 1.1207 |
| AJANTPHARM | 2026-06-30 | 2026-08-14 | 5.06 | 6.3100 | 1.2478 |
| ANANDRATHI | 2025-12-31 | 2026-02-14 | 12.03 | 1.3800 | 0.1147 |
| ANANDRATHI | 2026-06-30 | 2026-08-14 | 37.41 | 4.1600 | 0.1112 |
| APARINDS | 2026-06-30 | 2026-08-14 | 4.25 | 50.9100 | 11.9889 |
| ASIANPAINT | 2026-06-30 | 2026-08-14 | 1.68 | 4.5800 | 2.7195 |
| ASKAUTOLTD | 2025-12-31 | 2026-02-14 | 1.93 | 0.7100 | 0.3672 |
| BEL | 2025-12-31 | 2026-02-14 | 2.23 | 0.3700 | 0.1660 |
| BERGEPAINT | 2026-06-30 | 2026-08-14 | 3.64 | 0.7700 | 0.2114 |
| BIKAJI | 2025-12-31 | 2026-02-14 | 2.61 | 1.3400 | 0.5142 |
| BLS | 2025-12-31 | 2026-02-14 | 3.35 | 1.0200 | 0.3046 |

### NEXT STEPS (DO NOT PROMOTE BASED ON EVENT COUNT ALONE)
1. ✓ V2 implementation complete
2. ✓ SUE math unit tests pass
3. ✓ PIT causality leakage tests pass
4. → Fetch Upstox T+1 Open price for each STRONG_BEAT event
5. → Compute MFE / MAE / hold-period return
6. → Run Arm A (IS) + Arm B (OOS holdout)
7. → Block-bootstrap by event-date cluster + symbol
8. → N_eff + permutation p-value
9. → 10-session paper test
10. → Promotion decision

BACKTEST_STATUS = DATA_INSUFFICIENT (price data not yet fetched)
