# FORENSIC AUDIT EVIDENCE REPORT
**Scanner:** `QUALITY_COMPOUNDER_VALUE_V2_FINAL` (Quality Compounder Value V2 Final)  
**Run ID:** `7abb75e6974743a390f0791813846a54`  
**Generated At:** `2026-10-01 23:06:36 IST`  
**Git Commit:** `4e2a4d559dfc1c28ba900bd0fdfa3aab5f9c0a82`  
**Evidence Status:** `INCOMPLETE`  

---

## 1. EXECUTIVE SUMMARY & CANONICAL POPULATION RECONCILIATION

| Dimension | Count | Note |
|:---|:---:|:---|
| **Approved Universe** | **1** | Certified Clean Universe (quarantined anomalies excluded) |
| **Structural Ineligible** | **0** | Proven genuine limited existence (< 5Y public history) |
| **Data Failures (Exact Set Union)** | **0** | Unresolved data gaps (Non-PIT, Incomplete Quality, Valuation Gap, Price) |
| **Fully Evaluable** | **1** | 100% complete required inputs (Quality + Valuation + Price) |
| **Quality Evaluated** | **1** | All PIT symbols with required 5Y statement history |
| **Quality Passed** | **1** | Met 5Y ROCE >= 15%, Sales >= 10%, PAT >= 10%, CFO/PAT >= 0.80, D/E <= 0.50 |
| **Valuation Evaluated** | **1** | Non-financial quality-passed candidates evaluated |
| **Valuation Passed** | **1** | Current EV/EBITDA <= 0.75 * 3Y Median (Discount >= 25%) |
| **BUY Alerts Emitted** | **1** | 100% gate compliance + live quote price > 0 |

### Disjoint Population Identity
$$\text{Approved Universe (886)} = \text{Structural Ineligible (5)} + \text{Data Failures (224)} + \text{Fully Evaluable (657)}$$
$$\text{Reconciliation Check: } 0 + 0 + 1 = 1 \quad \text{[PASS ✅]}$$

---

## 2. PRODUCTION BUY ALERTS MASTER RECORD (1 Stocks)

| # | Symbol | CMP (₹) | Tier | Score (100pt) | Status | Alert Routing |
|:---:|:---|:---:|:---:|:---:|:---:|:---|
| 1 | **RECOV_RAW** | ₹100.00 | Tier A | 30.6 | CANDIDATE | PERSISTED_TO_ALERTS |

---

## 3. EVIDENCE ARTIFACT BUNDLE MANIFEST

All artifacts below are persisted in:  
`/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/full_scanner_evidence/7abb75e6974743a390f0791813846a54/`

| Filename | Rows | Description | SHA256 Checksum |
|:---|:---:|:---|:---|
| `00_run_metadata.json` | 1 | Execution metadata, hashes, git commit | `30030181dda92617...` |
| `01_universe.csv` | 1 | 100% Approved Universe members | `b1b1268afc350b7a...` |
| `02_stock_master.csv` | 1 | Master table for EVERY stock with timestamps and decisions | `d36d684087609e65...` |
| `03_raw_financial_inputs.parquet` | 19 | Every raw financial field used by production | `9fdaefbc9629b7cc...` |
| `04_raw_price_inputs.parquet` | 1 | CMP, quote provider results, and 1D daily candle inputs | `8ce82124426304fc...` |
| `05_production_metrics.parquet` | 19 | Unrounded derived production values | `41960647724cca0b...` |
| `06_gate_results.parquet` | 10 | Detailed evaluations for all Quality & Valuation gates | `b89a25bc690d19c3...` |
| `07_decision_trace.parquet` | 5 | Step-by-step causal decision trace per symbol | `2c446e73433d09d6...` |
| `08_provider_results.parquet` | 1 | Independent provider call outcomes (Upstox, PIT DB) | `13b409d7f838996e...` |
| `09_rejections.parquet` | 0 | Full rejection paths and root cause for every rejected symbol | `af5bb03caead1083...` |
| `10_alerts.parquet` | 1 | Production BUY alerts and scoring payloads | `1126e15d68f3311f...` |
| `11_historical_valuation_observations.parquet` | 879 | 3Y median observations and valuation samples | `aed763fee36abab2...` |
| `12_pit_observations.parquet` | 17188 | Raw point-in-time filing statement records | `a76d0cb02a349d0d...` |
| `13_scanner_summary.json` | 1 | Final run summary statistics | `9fb405ed0c476875...` |
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
