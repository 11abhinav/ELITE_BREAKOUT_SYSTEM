# PEAD_FUNDAMENTAL_V1 — MASTER ONE-SHOT AUDIT REPORT

**Strategy ID:** `PEAD_FUNDAMENTAL_V1`  
**Governance Specification Version:** `1.2 (FROZEN PRE-REGISTRATION)`  
**Git SHA:** `9414180d9b3bd8354e167418522399ce075f6b6f`  
**PIT Database SHA256:** `3700fc8060666d9ece5b95dc9ce91679d5a8bd1a34469c2b7336106d51c58227`  
**Evaluation Window:** `2016-01-01 to 2026-09-30`  

---

### Executive Summary & Formal Governance Verdict

**FINAL GOVERNANCE VERDICT:** `DATA_INSUFFICIENT`

**Reason for Verdict:**
The frozen pre-registered `PEAD_FUNDAMENTAL_V1` SUE forecasting model (Governance Specification Version 1.2, Section 17.1) requires a minimum of **12 consecutive prior quarterly EPS observations** (`SUE_MIN_QUARTERS = 12`) to construct the 4-period seasonal random walk with drift model and calculate the sample forecast-error standard deviation $\sigma$.

The current audited Point-in-Time (PIT) database (`data/pit_fundamentals_v1/pit_fundamentals_v1.db`) contains quarterly statements scraped from exchange disclosures via Screener, which natively limits quarterly columns to a maximum of **11 quarters per symbol**.

Under the **Mandatory Zero-Synthetic-Fallback & No-Dummy-Watchlist Invariant** and **Governance Rules 7 & 17.1**, the system strictly fails-closed:
- `SUE_STATUS = DATA_INSUFFICIENT_HISTORY`
- **Zero synthetic quarters generated**
- **Zero SUE values imputed or replaced with 0**
- **Zero Arm A events certified**

---

### Pre-Event Fundamental Gating Taxonomy Breakdown

| Pre-Event Fundamental Category | Evaluated Event Count | Percentage |
| :--- | :---: | :---: |
| **FUNDAMENTAL_PASS** | **778** | **10.6%** |
| `FUNDAMENTAL_FAIL_QUALITY` | 3192 | 43.6% |
| `FUNDAMENTAL_FAIL_ACCELERATION` | 2545 | 34.7% |
| `METRIC_NOT_APPLICABLE_FINANCIAL` | 386 | 5.3% |
| `DATA_INSUFFICIENT_FILING` | 257 | 3.5% |
| `INSUFFICIENT_LISTING_HISTORY` | 136 | 1.9% |
| `DATA_MISSING_PIT` | 33 | 0.5% |
| **TOTAL EVALUATED EVENTS** | **7327** | **100.0%** |

---

### Next Steps to Unblock Backtest Certification

1. **Ingest Extended Quarterly History (2012–2026)**: Fetch multi-year quarterly filing archives directly from exchange XBRL files or Upstox API to populate $\ge 12$ prior quarterly quarters.
2. **Re-run One-Shot Certification**: Execute `python3 scripts/run_pead_fundamental_v1_one_shot.py`.

---
*Report generated automatically by `scripts/run_pead_fundamental_v1_one_shot.py` on 2026-09-30 03:08:16 UTC*
