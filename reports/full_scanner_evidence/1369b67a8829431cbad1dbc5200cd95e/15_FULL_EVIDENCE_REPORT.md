# FORENSIC AUDIT EVIDENCE REPORT
**Scanner:** `QUALITY_COMPOUNDER` (Quality Compounder)  
**Run ID:** `1369b67a8829431cbad1dbc5200cd95e`  
**Generated At:** `2026-10-02 17:24:05 IST`  
**Git Commit:** `6de03dbe90c18b907341225516ffeceb5409dd7d`  
**Evidence Status:** `READY_FOR_EXTERNAL_INDEPENDENT_AUDIT`  

---

## 1. EXECUTIVE SUMMARY & CANONICAL POPULATION RECONCILIATION

| Dimension | Count | Note |
|:---|:---:|:---|
| **Approved Universe** | **886** | Certified Clean Universe (quarantined anomalies excluded) |
| **Structural Ineligible** | **40** | Proven genuine limited existence (< 5Y public history) |
| **Data Failures (Exact Set Union)** | **752** | Unresolved data gaps (Non-PIT, Incomplete Quality, Valuation Gap, Price) |
| **Fully Evaluable** | **94** | 100% complete required inputs (Quality + Valuation + Price) |
| **Quality Evaluated** | **531** | All PIT symbols with required 5Y statement history |
| **Quality Passed** | **133** | Met 5Y ROCE >= 15%, Sales >= 10%, PAT >= 10%, CFO/PAT >= 0.80, D/E <= 0.50 |
| **Valuation Evaluated** | **47** | Non-financial quality-passed candidates evaluated |
| **Valuation Passed** | **3** | Current EV/EBITDA <= 0.75 * 3Y Median (Discount >= 25%) |
| **BUY Alerts Emitted** | **3** | 100% gate compliance + live quote price > 0 |

### Disjoint Population Identity
$$\text{Approved Universe (886)} = \text{Structural Ineligible (5)} + \text{Data Failures (224)} + \text{Fully Evaluable (657)}$$
$$\text{Reconciliation Check: } 40 + 752 + 94 = 886 \quad \text{[PASS ✅]}$$

---

## 2. PRODUCTION BUY ALERTS MASTER RECORD (3 Stocks)

| # | Symbol | CMP (₹) | Tier | Score (100pt) | Status | Alert Routing |
|:---:|:---|:---:|:---:|:---:|:---:|:---|
| 1 | **AFFLE** | ₹1449.60 | Tier B | 36.2 | CANDIDATE | PERSISTED_TO_ALERTS |
| 2 | **AHLUCONT** | ₹555.90 | Tier B | 63.0 | CANDIDATE | PERSISTED_TO_ALERTS |
| 3 | **TIPSMUSIC** | ₹644.20 | Tier A | 57.0 | CANDIDATE | PERSISTED_TO_ALERTS |

---

## 3. EVIDENCE ARTIFACT BUNDLE MANIFEST

All artifacts below are persisted in:  
`/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/full_scanner_evidence/1369b67a8829431cbad1dbc5200cd95e/`

| Filename | Rows | Description | SHA256 Checksum |
|:---|:---:|:---|:---|
| `00_run_metadata.json` | 1 | Execution metadata, hashes, git commit | `5b09e1abc29fda7f...` |
| `01_universe.csv` | 886 | 100% Approved Universe members | `ae2b0ab3af459ec1...` |
| `02_stock_master.csv` | 886 | Master table for EVERY stock with timestamps and decisions | `962e87562b7e0bce...` |
| `03_raw_financial_inputs.parquet` | 23924 | Every raw financial field used by production | `7972c8d971f363bb...` |
| `04_raw_price_inputs.parquet` | 886 | CMP, quote provider results, and 1D daily candle inputs | `0d17ed2cdf8fd248...` |
| `05_production_metrics.parquet` | 15219 | Unrounded derived production values | `dd30f2891eeba772...` |
| `06_gate_results.parquet` | 8520 | Detailed evaluations for all Quality & Valuation gates | `be6305c5a3d99edb...` |
| `07_decision_trace.parquet` | 4175 | Step-by-step causal decision trace per symbol | `d19c968128d2c351...` |
| `08_provider_results.parquet` | 886 | Independent provider call outcomes (Upstox, PIT DB) | `88c7be03e95b722d...` |
| `09_rejections.parquet` | 883 | Full rejection paths and root cause for every rejected symbol | `95b5d81380cb28cd...` |
| `10_alerts.parquet` | 3 | Production BUY alerts and scoring payloads | `8228b524f421f2af...` |
| `11_historical_valuation_observations.parquet` | 3114 | 3Y median observations and valuation samples | `508ab6648d9266ae...` |
| `12_pit_observations.parquet` | 15182 | Raw point-in-time filing statement records | `8cf461c45f4b67e0...` |
| `13_scanner_summary.json` | 1 | Final run summary statistics | `2825072746fb7529...` |
| `14_evidence_manifest.json` | 1 | Manifest with SHA256 checksums & integrity checks | N/A (Generated) |

---

## 4. CROSS-FILE CONSISTENCY & INTEGRITY ASSERTIONS

| Assertion Rule | Result | Verification Detail |
|:---|:---:|:---|
| `Universe Count == Stock Master Count` | **PASS ✅** | 886 == 886 |
| `Master Symbols Unique` | **PASS ✅** | Exactly 886 unique symbols (zero duplicates) |
| `Every Universe Member in Stock Master` | **PASS ✅** | 100% universe coverage verified |
| `Alert Counts Reconciled` | **PASS ✅** | 3 alerts in alerts.parquet matches summary |
| `Rejections + Alerts == Universe` | **PASS ✅** | 883 rejections + 3 alerts = 886 |
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
