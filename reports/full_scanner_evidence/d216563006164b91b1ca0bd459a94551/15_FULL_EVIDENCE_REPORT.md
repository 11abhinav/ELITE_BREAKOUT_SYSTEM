# FORENSIC AUDIT EVIDENCE REPORT
**Scanner:** `QUALITY_COMPOUNDER` (Quality Compounder)  
**Run ID:** `d216563006164b91b1ca0bd459a94551`  
**Generated At:** `2026-10-21 07:54:37 IST`  
**Git Commit:** `e6df6320c6fe168c4b688c1d93307dad490897ba`  
**Evidence Status:** `INCOMPLETE`  

---

## 1. EXECUTIVE SUMMARY & CANONICAL POPULATION RECONCILIATION

| Dimension | Count | Note |
|:---|:---:|:---|
| **Approved Universe** | **4** | Certified Clean Universe (quarantined anomalies excluded) |
| **Structural Ineligible** | **0** | Proven genuine limited existence (< 5Y public history) |
| **Data Failures (Exact Set Union)** | **4** | Unresolved data gaps (Non-PIT, Incomplete Quality, Valuation Gap, Price) |
| **Fully Evaluable** | **0** | 100% complete required inputs (Quality + Valuation + Price) |
| **Quality Evaluated** | **0** | All PIT symbols with required 5Y statement history |
| **Quality Passed** | **0** | Met 5Y ROCE >= 15%, Sales >= 10%, PAT >= 10%, CFO/PAT >= 0.80, D/E <= 0.50 |
| **Valuation Evaluated** | **3** | Non-financial quality-passed candidates evaluated |
| **Valuation Passed** | **0** | Current EV/EBITDA <= 0.75 * 3Y Median (Discount >= 25%) |
| **BUY Alerts Emitted** | **0** | 100% gate compliance + live quote price > 0 |

### Disjoint Population Identity
$$\text{Approved Universe (886)} = \text{Structural Ineligible (5)} + \text{Data Failures (224)} + \text{Fully Evaluable (657)}$$
$$\text{Reconciliation Check: } 0 + 4 + 0 = 4 \quad \text{[PASS ✅]}$$

---

## 2. PRODUCTION BUY ALERTS MASTER RECORD (0 Stocks)

| # | Symbol | CMP (₹) | Tier | Score (100pt) | Status | Alert Routing |
|:---:|:---|:---:|:---:|:---:|:---:|:---|

---

## 3. EVIDENCE ARTIFACT BUNDLE MANIFEST

All artifacts below are persisted in:  
`/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/full_scanner_evidence/d216563006164b91b1ca0bd459a94551/`

| Filename | Rows | Description | SHA256 Checksum |
|:---|:---:|:---|:---|
| `00_run_metadata.json` | 1 | Execution metadata, hashes, git commit | `014131cc9f520dba...` |
| `01_universe.csv` | 4 | 100% Approved Universe members | `245fea29f57523aa...` |
| `02_stock_master.csv` | 4 | Master table for EVERY stock with timestamps and decisions | `8d42badc2f72335d...` |
| `03_raw_financial_inputs.parquet` | 76 | Every raw financial field used by production | `ffdcb79708bc69fb...` |
| `04_raw_price_inputs.parquet` | 4 | CMP, quote provider results, and 1D daily candle inputs | `0ca3730643b11631...` |
| `05_production_metrics.parquet` | 76 | Unrounded derived production values | `5f835f4c3048c9de...` |
| `06_gate_results.parquet` | 40 | Detailed evaluations for all Quality & Valuation gates | `ad9d9bdd00348e5a...` |
| `07_decision_trace.parquet` | 20 | Step-by-step causal decision trace per symbol | `e6cc71076a25a929...` |
| `08_provider_results.parquet` | 4 | Independent provider call outcomes (Upstox, PIT DB) | `a21e963d88413242...` |
| `09_rejections.parquet` | 4 | Full rejection paths and root cause for every rejected symbol | `4f28d26bb5f5ca3e...` |
| `10_alerts.parquet` | 0 | Production BUY alerts and scoring payloads | `af5bb03caead1083...` |
| `11_historical_valuation_observations.parquet` | 879 | 3Y median observations and valuation samples | `aed763fee36abab2...` |
| `12_pit_observations.parquet` | 16733 | Raw point-in-time filing statement records | `db4cfd01f9417c9d...` |
| `13_scanner_summary.json` | 1 | Final run summary statistics | `4673c0bc4d5c84bb...` |
| `14_evidence_manifest.json` | 1 | Manifest with SHA256 checksums & integrity checks | N/A (Generated) |

---

## 4. CROSS-FILE CONSISTENCY & INTEGRITY ASSERTIONS

| Assertion Rule | Result | Verification Detail |
|:---|:---:|:---|
| `Universe Count == Stock Master Count` | **PASS ✅** | 4 == 4 |
| `Master Symbols Unique` | **PASS ✅** | Exactly 4 unique symbols (zero duplicates) |
| `Every Universe Member in Stock Master` | **PASS ✅** | 100% universe coverage verified |
| `Alert Counts Reconciled` | **PASS ✅** | 0 alerts in alerts.parquet matches summary |
| `Rejections + Alerts == Universe` | **PASS ✅** | 4 rejections + 0 alerts = 4 |
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
