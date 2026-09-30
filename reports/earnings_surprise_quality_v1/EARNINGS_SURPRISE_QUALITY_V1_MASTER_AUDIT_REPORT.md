# EARNINGS_SURPRISE_QUALITY_V1 — MASTER ONE-SHOT AUDIT REPORT

**Strategy ID:** `EARNINGS_SURPRISE_QUALITY_V1`  
**Governance Version:** `1.0 (FROZEN PRE-REGISTRATION)`  
**Evaluation Window:** `2016-01-01 to 2026-09-30`  
**PIT DB SHA256:** `3700fc8060666d9ece5b95dc9ce91679d5a8bd1a34469c2b7336106d51c58227`  

---

### Executive Summary & Final Governance Verdict

#### Accounting Funnel:
- **1. Reconstructed Earnings Events:** `4285`  
- **2. Quality Baseline Valid Events:** `0`  
- **3. SUE Valid Events (>= 12 Prior Quarters):** `0`  
- **4. ARM A Executable Trades Replayed:** `0`  

- **Exit Architecture:** `Confirmed Structural Weakness (Open-Ended Hold)`  
- **Overall Master Verdict:** `DATA_INSUFFICIENT` (Screener DB max 11 quarters history vs 12 quarters required for certified SUE forecast)  
- **Production Status:** `BLOCKED` (Zero live alerts authorized)  

---

### Forensic Audit Note
This run reached the event-replay stage, but ARM A produced 0 executable trades because the active PIT dataset contains a maximum of 11 consecutive quarters per symbol (2023-Q3 to 2026-Q1), whereas the frozen SUE forecast model strictly requires 12 prior quarters. Under fail-closed governance rules, the 12-quarter requirement was preserved untouched. No trades were executed, and the hypothesis remains un-tested until historical quarterly depth is restored to 2012-2013+.

---

### Data Provenance & Invariants Audit
- **Price Source:** Upstox Historical Candle API V3 (Certified)
- **Fundamentals Source:** Screener PIT Database (`data/pit_fundamentals_v1/pit_fundamentals_v1.db`)
- **Timestamp Basis:** `LODR_STATUTORY_DEADLINE_CONSERVATIVE`
- **Friction Model:** `2.5 bps entry + 2.5 bps exit` (`5.0 bps round-trip total`)
- **Execution Endpoint:** `T+1 Open` entry, `Confirmed Structural Weakness T+1 Open` exit.
