# 10-TRADING-SESSION PAPER VALIDATION REPORT
**Strategy:** QUALITY_VALUE_RECOVERY_WEALTH_V1  
**Timestamp:** 2026-10-03T17:39:03.623972+05:30  
**Data Provenance:** Upstox Real Market Data (Certified Canonical PIT)  
**Execution Protocol:** Strictly $T+1$ Next Trading Day Open (Friction: 15 bps)  
**Initial Paper Capital:** ₹1.00 Crore  
**Final Portfolio Equity:** ₹0.9892 Crore (Net P&L: ₹-1.08 Lakhs | -1.08%)  
**Overall Validation Verdict:** `CERTIFIED`  

---

## Session-by-Session Replay Telemetry

| Session | Date | Regime | Candidates | Filled | Active Positions | Portfolio Equity | Return % | Latency | Pipeline |
|:-------:|:----:|:------:|:----------:|:------:|:----------------:|:----------------:|:--------:|:-------:|:--------:|
| Session 1 | 2026-09-11 | SIDEWAYS | 47 | 0 | 0 | ₹1.0000 Cr | +0.00% | 4.49s | PASS |
| Session 2 | 2026-09-15 | BEAR | 54 | 20 | 20 | ₹0.9839 Cr | -1.61% | 1.12s | PASS |
| Session 3 | 2026-09-16 | SIDEWAYS | 53 | 0 | 20 | ₹0.9834 Cr | -1.66% | 2.01s | PASS |
| Session 4 | 2026-09-17 | SIDEWAYS | 51 | 0 | 20 | ₹0.9905 Cr | -0.95% | 1.17s | PASS |
| Session 5 | 2026-09-18 | SIDEWAYS | 50 | 0 | 20 | ₹0.9883 Cr | -1.17% | 0.80s | PASS |
| Session 6 | 2026-09-21 | SIDEWAYS | 49 | 0 | 20 | ₹0.9912 Cr | -0.88% | 0.80s | PASS |
| Session 7 | 2026-09-22 | BEAR | 49 | 0 | 20 | ₹0.9952 Cr | -0.48% | 1.06s | PASS |
| Session 8 | 2026-09-23 | SIDEWAYS | 48 | 0 | 20 | ₹1.0010 Cr | +0.10% | 1.17s | PASS |
| Session 9 | 2026-09-24 | BEAR | 49 | 0 | 20 | ₹0.9879 Cr | -1.21% | 0.72s | PASS |
| Session 10 | 2026-09-25 | BEAR | 49 | 0 | 20 | ₹0.9892 Cr | -1.08% | 0.63s | PASS |

---

## Invariant Assertions & Governance Checklist
- [x] **Causal Integrity:** 100% of orders executed strictly at $T+1$ Open price (0 lookahead violations)
- [x] **Zero Synthetic Data:** All inputs sourced directly from certified Upstox daily price bars and PIT filings
- [x] **Regime Gating Compliance:** Zero alerts emitted outside certified regimes
- [x] **Friction Model:** Full 15 bps slippage and transaction costs charged against every simulated fill
- [x] **Runtime Stability:** 0 unhandled exceptions, 0 unbound local variables, 10/10 sessions completed successfully

## Governance Verdict
```
DATA_PIPELINE_STATUS    = CERTIFIED (UNEXPLAINED MISSING = 0)
RESEARCH_BATTERY        = CERTIFIED (N_eff=393.5, 10k Bootstrap p<0.0001)
PAPER_VALIDATION_STATUS = CERTIFIED (10/10 Sessions Passed)
-------------------------------------------------------------------------
PRODUCTION_PROMOTION    = READY_FOR_FINAL_GOVERNANCE_LOCK
```
