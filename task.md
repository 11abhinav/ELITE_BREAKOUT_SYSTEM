# Master Directive Task List: Optimization & Empirical Validation of All 5 Alpha Scanners

## Phase 3 Master Execution Track
- [x] Invariant Safeguards Verified (Weekend ban, lookahead, closed-candle, same-bar, refactor invariants: 43/43 tests passing)
- [x] Multi-TF V3 Formalization & Regime-Gated Replay (`app/multitf_v3_engine.py`, `tests/simulate_multitf_v3_replay.py`: +0.552R, PF 2.17, 50% +2R runners, N=34 OOS)
- [/] Reversal V2.2+ Composite Challenger Replay (Buffer + No-Bear + Capitulation on Step=2 grid, `task-2275` past 70%)
- [x] EOD Breakout Raw Score Engine Optimization (`EOD_CHALL_I` tested vs Sweet-Spot; `CHALL_G` confirmed Tier 1 Champion +0.325R, PF 1.67, N=102 OOS)
- [x] Pullback Alpha Engine Entry & Stop Confirmation (`PULLBACK_CHALL_C_2ATR_SHELF` confirmed Tier 2 Champion +0.404R, PF 1.82, post-SL recovery 14.3%)
- [x] Accumulation / VCP Engine Right-Tail Runner Optimization (`VCP_CHALL_G_RUNNER_EXPANSION` confirmed Tier 2 Champion +0.316R, PF 4.66, N=35 OOS)
- [x] Multi-Scanner Portfolio-Level Risk Simulation (`tests/simulate_portfolio_allocation.py`: Staged Governance +14.62R OOS, PF 1.99, correlation matrix mapped)
- [x] Master Synthesis & Final Registry Snapshot Update (`scripts/generate_master_5_scanner_certification_report.py`)
- [x] Update Master Certification Walkthrough with Complete Audit Traceability Ledger (`walkthrough.md` Edition 3.0)
