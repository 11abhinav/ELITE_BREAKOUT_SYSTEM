# EARNINGS_SURPRISE_QUALITY_V1 — MASTER ONE-SHOT AUDIT REPORT

**Strategy ID:** `EARNINGS_SURPRISE_QUALITY_V1`  
**Governance Version:** `1.0 (FROZEN PRE-REGISTRATION)`  
**Evaluation Window:** `2016-01-01 to 2026-09-30`  
**PIT DB SHA256:** `3700fc8060666d9ece5b95dc9ce91679d5a8bd1a34469c2b7336106d51c58227`  

---

### Executive Summary & Final Governance Verdict

- **Primary Treatment (ARM A - Quality + Positive Result + SUE >= 1.0):**  
  - Replayed Trades: `0`  
  - Exit Architecture: `Confirmed Structural Weakness (Open-Ended Hold)`  
- **Overall Master Verdict:** `DATA_INSUFFICIENT` (Screener DB max 11 quarters history vs 12 quarters required for certified SUE forecast)  
- **Production Status:** `BLOCKED` (Zero live alerts authorized)  

---

### Data Provenance & Invariants Audit
- **Price Source:** Upstox Historical Candle API V3 (Certified)
- **Fundamentals Source:** Screener PIT Database (`data/pit_fundamentals_v1/pit_fundamentals_v1.db`)
- **Timestamp Basis:** `LODR_STATUTORY_DEADLINE_CONSERVATIVE`
- **Friction Model:** `2.5 bps entry + 2.5 bps exit` (`5.0 bps round-trip total`)
- **Execution Endpoint:** `T+1 Open` entry, `Confirmed Structural Weakness T+1 Open` exit.
