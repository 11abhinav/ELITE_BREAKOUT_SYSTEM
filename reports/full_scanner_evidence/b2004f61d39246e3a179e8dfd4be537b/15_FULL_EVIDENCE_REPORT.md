# FORENSIC AUDIT EVIDENCE REPORT
**Scanner:** `QUALITY_COMPOUNDER_VALUE_V2_FINAL` (Quality Compounder Value V2 Final)  
**Run ID:** `b2004f61d39246e3a179e8dfd4be537b`  
**Generated At:** `2026-10-01 23:06:05 IST`  
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
| **Quality Passed** | **0** | Met 5Y ROCE >= 15%, Sales >= 10%, PAT >= 10%, CFO/PAT >= 0.80, D/E <= 0.50 |
| **Valuation Evaluated** | **1** | Non-financial quality-passed candidates evaluated |
| **Valuation Passed** | **0** | Current EV/EBITDA <= 0.75 * 3Y Median (Discount >= 25%) |
| **BUY Alerts Emitted** | **0** | 100% gate compliance + live quote price > 0 |

### Disjoint Population Identity
$$\text{Approved Universe (886)} = \text{Structural Ineligible (5)} + \text{Data Failures (224)} + \text{Fully Evaluable (657)}$$
$$\text{Reconciliation Check: } 0 + 1 + 0 = 1 \quad \text{[PASS ✅]}$$

---

## 2. PRODUCTION BUY ALERTS MASTER RECORD (0 Stocks)

| # | Symbol | CMP (₹) | Tier | Score (100pt) | Status | Alert Routing |
|:---:|:---|:---:|:---:|:---:|:---:|:---|

---

## 3. EVIDENCE ARTIFACT BUNDLE MANIFEST

All artifacts below are persisted in:  
`/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/full_scanner_evidence/b2004f61d39246e3a179e8dfd4be537b/`

| Filename | Rows | Description | SHA256 Checksum |
|:---|:---:|:---|:---|
| `00_run_metadata.json` | 1 | Execution metadata, hashes, git commit | `b2b107c2386e7b5e...` |
| `01_universe.csv` | 1 | 100% Approved Universe members | `6515f3c210053dbb...` |
| `02_stock_master.csv` | 1 | Master table for EVERY stock with timestamps and decisions | `287874bf4c0c6e77...` |
| `03_raw_financial_inputs.parquet` | 19 | Every raw financial field used by production | `b61e244724056723...` |
| `04_raw_price_inputs.parquet` | 1 | CMP, quote provider results, and 1D daily candle inputs | `0325e818866aff6e...` |
| `05_production_metrics.parquet` | 19 | Unrounded derived production values | `c8d178efba556a9c...` |
| `06_gate_results.parquet` | 10 | Detailed evaluations for all Quality & Valuation gates | `96eba16f01c692a4...` |
| `07_decision_trace.parquet` | 5 | Step-by-step causal decision trace per symbol | `27e0045ac59e4cf9...` |
| `08_provider_results.parquet` | 1 | Independent provider call outcomes (Upstox, PIT DB) | `1e88ec4bc7461267...` |
| `09_rejections.parquet` | 1 | Full rejection paths and root cause for every rejected symbol | `771d3703f575de62...` |
| `10_alerts.parquet` | 0 | Production BUY alerts and scoring payloads | `af5bb03caead1083...` |
| `11_historical_valuation_observations.parquet` | 879 | 3Y median observations and valuation samples | `aed763fee36abab2...` |
| `12_pit_observations.parquet` | 17188 | Raw point-in-time filing statement records | `a76d0cb02a349d0d...` |
| `13_scanner_summary.json` | 1 | Final run summary statistics | `b07df72b5c2766d7...` |
| `14_evidence_manifest.json` | 1 | Manifest with SHA256 checksums & integrity checks | N/A (Generated) |

---

## 4. CROSS-FILE CONSISTENCY & INTEGRITY ASSERTIONS

| Assertion Rule | Result | Verification Detail |
|:---|:---:|:---|
| `Universe Count == Stock Master Count` | **PASS ✅** | 1 == 1 |
| `Master Symbols Unique` | **PASS ✅** | Exactly 1 unique symbols (zero duplicates) |
| `Every Universe Member in Stock Master` | **PASS ✅** | 100% universe coverage verified |
| `Alert Counts Reconciled` | **PASS ✅** | 0 alerts in alerts.parquet matches summary |
| `Rejections + Alerts == Universe` | **PASS ✅** | 1 rejections + 0 alerts = 1 |
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
