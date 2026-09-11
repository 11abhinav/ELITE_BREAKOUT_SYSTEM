# Gate #1 Production-Readiness Telemetry Package & Audit Report

**Generated At**: 2026-09-11T16:11:34.588250  
**Source Telemetry DB**: `data/shadow_telemetry.db`  
**Total Telemetry Records**: `616` | **Total Resolved Disagreements (All Scanners)**: `34`  
**Daily Builder Specific Disagreements**: `2` (Target: $N \ge 100$, preferred $200–300$)  
**Active Production Version**: `V5.25_PRODUCTION`  
**Active Shadow Challenger**: `V5.28_DB_SHADOW` & `V5.26_SHADOW`  

---

## Executive Gate #1 Verdict: **CONTINUE SHADOW OBSERVATION 🟡 (PROMOTION BLOCKED)**

### Core Findings & Audit Verdict:
1. **Sample Size Insufficiency**: Daily Builder specific resolved disagreements currently stand at **`N = 2`**. While all-scanner temporal disagreements reach $N = 34$, V5.28 is a dedicated Daily Builder quality model and cannot be certified on all-scanner proxy data.
2. **False Avoid Rate Warning**: The current False Avoid Rate is **`29.41%`**, which exceeds the locked **$\le 15.0\%$** promotion ceiling (**FAIL / WARNING 🔴**). The system must prove over a larger live sample that it is not excessively penalizing valid winners.
3. **Net Decision Edge**: Aggregate Net Decision Delta across changed decisions is **`+28.60R`** (and **`+23.60R`** after removing the top 2 outlier winners).
4. **Governing Recommendation**: **Do NOT promote V5.28 to production**. Continue `V5.28_DB_SHADOW` in frozen observation until Daily Builder $N \ge 100$ and False Avoid rate stabilizes $\le 15.0\%$.

---

## 1. Complete Disagreement & Trade Accounting Bridge

```
Total Audited Decisions (616 Candidates)
   │
   ├── 582 Unchanged Status Candidates (579 Filtered, 3 Concurring Selected Trades)
   │
   └── 34 Changed Decisions (Disagreements)
         │
         ├── 17 Suppressed Candidates (V5.25 Selected ──► V5.28 Filtered)
         │     ├── 12 Correct Avoids (+10.35R Capital Preserved from Losers)
         │     └── 5 False Avoids    (-6.60R Opportunity Cost from Winners)
         │
         └── 17 Promoted Candidates   (V5.25 Filtered ──► V5.28 Selected)
               ├── 14 Correct Promotes (+27.10R Alpha Generated from Winners)
               └── 3 Bad Promotes     (-2.25R False Positive Drag from Losers)

Trade Count Reconciliation:
* V5.25 Traded Baseline: 17 Suppressed Trades + 3 Concurring Trades = 20 Trades (+1.75R Total Realized)
* V5.28 Shadow Challenger: 17 Promoted Trades + 3 Concurring Trades = 20 Trades (+30.35R Total Realized)
* Decision Net Lift: (+10.35R + +27.10R) - (6.60R + 2.25R) = +28.60R Net Delta R
```

---

## 2. Three-Way Performance Split

| System | Trades | Win Rate (%) | Realized R | E[R] | PF | Avg MFE | Avg MAE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1. V5.25 Production (Actual Traded Baseline) | 20 | 40.0% | +1.75R | +0.088R | 1.17 | 0.98R | -0.78R |
| 2. V5.28 Shadow Challenger (Hypothetical) | 20 | 85.0% | +30.35R | +1.518R | 14.49 | 2.28R | -0.40R |

* **Decision Net Delta R**: **`+28.60R`** across all 34 changed decisions ($R_{V5.28} - R_{V5.25}$).

---

## 3. Four-Way Outcome Scorecard & Attribution

| Outcome Classification | Count | R Impact | Operational Meaning |
| --- | --- | --- | --- |
| ✅ Correct Avoid (Vetoed Loser) | 12 | +10.35R | Capital Preserved from Stale Climax Drag |
| ❌ False Avoid (Vetoed Winner) | 5 | -6.60R | Opportunity Cost from Filter Selectivity |
| ✅ Correct Promote (Elevated Winner) | 14 | +27.10R | Alpha Generated from Fresh Breakout Bases |
| ❌ Bad Promote (Elevated Loser) | 3 | -2.25R | False Positive Selection Drag |
| ⚪ Concurring Trades (Both Traded) | 3 | +5.50R | Core Unchanged Baseline Profit |

* **Total Avoided Candidates**: `17`
* **False Avoid Rate**: **`29.41%`** (Status: **FAIL / WARNING 🔴** vs $\le 15.0\%$ locked charter threshold).

---

## 4. Outlier Robustness Audit (Leave-One-Out & Leave-Two-Out)

| Outlier Test Scenario | Net Delta R | Survives Positive |
| --- | --- | --- |
| Full Realized Sample (All Resolved Disagreements) | +28.60R | YES |
| Leave-1-Out (Minus Largest Single Winner) | +26.10R | YES |
| Leave-2-Out (Minus Top 2 Largest Winners) | +23.60R | YES |

---

## 5. Scanner-Level Disagreement & Delta R Breakdown

| Scanner Name | Total Sample | Disagreements | Correct Avoids (+R) | False Avoids (-R) | Correct Promotes (+R) | Bad Promotes (-R) | Net Scanner ΔR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MultiTF 1H | 56 | 18 | +0.00R | -6.20R | +25.90R | -1.50R | +18.20R |
| MultiTF 5M | 56 | 11 | +7.65R | -0.40R | +0.00R | -0.00R | +7.25R |
| Short Covering | 56 | 3 | +2.70R | -0.00R | +0.00R | -0.00R | +2.70R |
| Daily Builder | 56 | 2 | +0.00R | -0.00R | +1.20R | -0.75R | +0.45R |
| Reversal | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| Pullback V2 | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| Multibagger | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| EOD Breakout | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| Accumulation VCP | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| Wealth Engine | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |
| Technical Ahat | 56 | 0 | +0.00R | -0.00R | +0.00R | -0.00R | +0.00R |

---

## 6. Daily Builder Specific Scorecard & Opportunity Density

| Daily Builder Metric | Current Live Telemetry Value | Operational Significance |
| :--- | :---: | :--- |
| **Eligible Daily Builder Universe** | **`56` Candidates** | Total EOD candidate flow evaluated |
| **Qualified Candidates (Score $\ge 58$)** | **`29` Candidates** | Setups meeting pristine Structure $	imes$ Timing floor |
| **Alerts Emitted (Max 5 Dynamic Ceiling)** | **`3` Alerts** | Actual alerts produced without quota filling |
| **Opportunity Density** | **`51.79%`** | Scarcity of quality setups in raw candidate stream |
| **Emission Rate** | **`10.34%`** | Percentage of qualified setups emitted |
| **Daily Builder Resolved Disagreements** | **`N = 2`** | Target: $N \ge 100$ (Progress: **`2%`**) |

---

## 7. Governance, Timestamp & Invariant Audit

| Governance Check | Result | Status |
| --- | --- | --- |
| Timestamp Monotonicity (Decision <= Entry) | VERIFIED (0 Violations) | PASS ✅ |
| Weekend Bar Prohibition (Sat/Sun Candle Count) | VERIFIED (0 Violations) | PASS ✅ |
| Immutable Parameter Version Binding | All candidates tagged with config_version_id | PASS ✅ |
| Database Integrity (production_parameters.db) | All versions stored immutably with SHA audit | PASS ✅ |
| Git Repository Synchronization | Committed and pushed to origin/main (6029ce10) | PASS ✅ |

---

## 8. Final Audit of the 6 Promotion Criteria

| # | Promotion Criterion | Gate #1 Standard | Current Live Telemetry Value | Audit Verdict |
| :---: | :--- | :--- | :--- | :---: |
| **1** | **Positive Net Advantage** | Net Delta R > 0.00R | **`+28.60R`** | **PASS ✅** |
| **2** | **Controlled Selectivity Drag** | False Avoid Rate <= 15.0% | **`29.41%`** | **FAIL / WARNING 🔴** |
| **3** | **Outlier-Resistant Alpha** | Positive after removing Top 2 Winners | **`+23.60R` (Leave-2-Out)** | **PASS ✅** |
| **4** | **Multi-Regime Durability** | Persistent across states & regimes | MultiTF 1H (+18.2R), 5M (+7.3R), SC (+2.7R) | **PASS ✅** |
| **5** | **Zero Governance Violations** | 0 timestamp/weekend errors | **0 Violations** | **PASS ✅** |
| **6** | **Directional Consistency** | Aligned with certified holdout | Directionally consistent, high win-rate profile | **PASS ✅** |
| **—** | **Daily Builder Sample Size** | N >= 100 Daily Builder Disagreements | **`N = 2` (Insufficient Sample)** | **BLOCK 🛑** |

---

## Conclusion & Operational Charter

1. **PROMOTION BLOCKED**: V5.28 will **NOT** be promoted to production at this stage.
2. **MAINTAIN FROZEN SHADOW**: `V5.25_PRODUCTION` continues executing real capital; `V5.28_DB_SHADOW` continues parallel telemetry logging.
3. **NEXT GATE AUDIT**: Gate #1 evaluation will reopen when Daily Builder specific resolved disagreements reach **$N \ge 100$**.
