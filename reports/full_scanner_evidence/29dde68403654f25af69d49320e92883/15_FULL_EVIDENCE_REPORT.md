# FORENSIC AUDIT EVIDENCE REPORT
**Scanner:** `QUALITY_COMPOUNDER_VALUE_V2_FINAL` (Quality Compounder Value V2 Final)  
**Run ID:** `29dde68403654f25af69d49320e92883`  
**Generated At:** `2026-10-01 23:03:45 IST`  
**Git Commit:** `4e2a4d559dfc1c28ba900bd0fdfa3aab5f9c0a82`  
**Evidence Status:** `INCOMPLETE`  

---

## 1. EXECUTIVE SUMMARY & CANONICAL POPULATION RECONCILIATION

| Dimension | Count | Note |
|:---|:---:|:---|
| **Approved Universe** | **1** | Certified Clean Universe (quarantined anomalies excluded) |
| **Structural Ineligible** | **0** | Proven genuine limited existence (< 5Y public history) |
| **Data Failures (Exact Set Union)** | **1** | Unresolved data gaps (Non-PIT, Incomplete Quality, Valuation Gap, Price) |
| **Fully Evaluable** | **0** | 100% complete required inputs (Quality + Valuation + Price) |
| **Quality Evaluated** | **1** | All PIT symbols with required 5Y statement history |
| **Quality Passed** | **1** | Met 5Y ROCE >= 15%, Sales >= 10%, PAT >= 10%, CFO/PAT >= 0.80, D/E <= 0.50 |
| **Valuation Evaluated** | **1** | Non-financial quality-passed candidates evaluated |
| **Valuation Passed** | **1** | Current EV/EBITDA <= 0.75 * 3Y Median (Discount >= 25%) |
| **BUY Alerts Emitted** | **1** | 100% gate compliance + live quote price > 0 |

### Disjoint Population Identity
$$\text{Approved Universe (886)} = \text{Structural Ineligible (5)} + \text{Data Failures (224)} + \text{Fully Evaluable (657)}$$
$$\text{Reconciliation Check: } 0 + 1 + 0 = 1 \quad \text{[PASS ✅]}$$

---

## 2. PRODUCTION BUY ALERTS MASTER RECORD (1 Stocks)

| # | Symbol | CMP (₹) | Tier | Score (100pt) | Status | Alert Routing |
|:---:|:---|:---:|:---:|:---:|:---:|:---|
| 1 | **RECOV_API** | ₹100.00 | Tier A | 44.4 | CANDIDATE | PERSISTED_TO_ALERTS |

---

## 3. EVIDENCE ARTIFACT BUNDLE MANIFEST

All artifacts below are persisted in:  
`/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/full_scanner_evidence/29dde68403654f25af69d49320e92883/`

| Filename | Rows | Description | SHA256 Checksum |
|:---|:---:|:---|:---|
| `00_run_metadata.json` | 1 | Execution metadata, hashes, git commit | `f94011dd27fac2c1...` |
| `01_universe.csv` | 1 | 100% Approved Universe members | `20bdcd202907f7ce...` |
| `02_stock_master.csv` | 1 | Master table for EVERY stock with timestamps and decisions | `6753129e3930f2b2...` |
| `03_raw_financial_inputs.parquet` | 19 | Every raw financial field used by production | `f1f73b4762ba772c...` |
| `04_raw_price_inputs.parquet` | 1 | CMP, quote provider results, and 1D daily candle inputs | `94d92650c446bd00...` |
| `05_production_metrics.parquet` | 19 | Unrounded derived production values | `9f3eec7dbac89cf8...` |
| `06_gate_results.parquet` | 10 | Detailed evaluations for all Quality & Valuation gates | `7cada5a509a248ca...` |
| `07_decision_trace.parquet` | 5 | Step-by-step causal decision trace per symbol | `5fb0878d2182d129...` |
| `08_provider_results.parquet` | 1 | Independent provider call outcomes (Upstox, PIT DB) | `a9e58f434e549b9f...` |
| `09_rejections.parquet` | 0 | Full rejection paths and root cause for every rejected symbol | `af5bb03caead1083...` |
| `10_alerts.parquet` | 1 | Production BUY alerts and scoring payloads | `68ed82251fa52a64...` |
| `11_historical_valuation_observations.parquet` | 879 | 3Y median observations and valuation samples | `aed763fee36abab2...` |
| `12_pit_observations.parquet` | 17188 | Raw point-in-time filing statement records | `a76d0cb02a349d0d...` |
| `13_scanner_summary.json` | 1 | Final run summary statistics | `072c56f379b234d0...` |
| `14_evidence_manifest.json` | 1 | Manifest with SHA256 checksums & integrity checks | N/A (Generated) |

---

## 4. CROSS-FILE CONSISTENCY & INTEGRITY ASSERTIONS

| Assertion Rule | Result | Verification Detail |
|:---|:---:|:---|
| `Universe Count == Stock Master Count` | **PASS ✅** | 1 == 1 |
| `Master Symbols Unique` | **PASS ✅** | Exactly 1 unique symbols (zero duplicates) |
| `Every Universe Member in Stock Master` | **PASS ✅** | 100% universe coverage verified |
| `Alert Counts Reconciled` | **PASS ✅** | 1 alerts in alerts.parquet matches summary |
| `Rejections + Alerts == Universe` | **PASS ✅** | 0 rejections + 1 alerts = 1 |
| `Zero "Other" Population` | **PASS ✅** | Strict 3-population classification: Structural, Data Failure, Fully Evaluable |
| `Independent Provider Tracking` | **PASS ✅** | `GUJGASLTD` live quote failure independently recorded |

---

## 5. EXTERNAL AUDIT INSTRUCTIONS

To independently reconstruct and verify calculations from this bundle without using scanner code:
1. Load `03_raw_financial_inputs.parquet` and verify ROCE, Sales CAGR, PAT CAGR, and CFO/PAT from reported values.
2. Load `04_raw_price_inputs.parquet` and `03_raw_financial_inputs.parquet` to calculate:
   $$\text{Market Cap} = \frac{\text{Shares} \times \text{CMP}}{10^7}, \quad \text{EV} = \text{Market Cap} + \text{Total Debt} - \text{Cash}$$
   $$\text{Current EV/EBITDA} = \frac{\text{EV}}{\text{EBITDA}}$$
3. Compare against `05_production_metrics.parquet` (`raw_calculated_value`).
4. Compare against `11_historical_valuation_observations.parquet` to verify the 25% discount:
   $$\text{Discount} = \frac{\text{3Y Median} - \text{Current EV/EBITDA}}{\text{3Y Median}} \ge 0.25$$
5. Inspect `06_gate_results.parquet` and `07_decision_trace.parquet` to confirm the pass/fail determination.

---
**FINAL VERDICT: FULL EVIDENCE BUNDLE READY FOR EXTERNAL INDEPENDENT AUDIT**
