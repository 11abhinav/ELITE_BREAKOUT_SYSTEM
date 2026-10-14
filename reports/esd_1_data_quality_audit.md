# ESD-1: DATA QUALITY, PROVENANCE & BIAS AUDIT

**Strategy**: Point-in-Time Earnings Surprise Drift (`ESD_1`)  
**Audit Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  
**Governance Scope**: Fundamental Data Provenance, Point-in-Time Availability, and History Coverage  

---

## 1. Executive Summary: Primary Forensic Findings

This audit establishes whether the fundamental and market price data available in the repository satisfy the mandatory project data governance standards for strategy certification.

### Summary of Audit Verdict:
| Audit Dimension | Finding & Status | Impact on Strategy Governance |
| :--- | :--- | :--- |
| **Market Price Data (1D)** | **PASS**: 947 NSE equity Parquets from Upstox (2016–2026, 2,471 sessions). Zero synthetic candles. | Price series is certified. |
| **Benchmark (Nifty 500)** | **PASS**: 2,471 sessions from 2016-09-27 to 2026-10-12. Total Return Index adjusted (+1.30% p.a. dividend yield). | Benchmark is certified. |
| **Point-in-Time Timestamps** | **PASS**: Filings record `filing_date` and `conservative_availability_timestamp` (23:59:59 IST). Decisions made at $T_1$ Close, executed at $T_2$ Open. | Zero intraday forward leakage. |
| **Filing Coverage Duration** | **CRITICAL LIMITATION**: Systematic quarterly filings exist **only from Q1 FY24 (2023-06-30) to Q1 FY27 (2026-06-30)**. Pre-2023 quarterly data is essentially absent (1 to 14 filings per quarter). | Cannot evaluate 2016–2020 Dev or 2021–2023 Val periods. |
| **SUE Denominator Constraint** | **FATAL HISTORICAL CONSTRAINT**: Strict frozen rule requires 8 historical YoY changes ($\ge 12$ prior quarters). Only **2 symbols** in the entire universe have $\ge 12$ quarters, yielding only **13 events**! | Frozen hypothesis is mathematically unviable on current historical data. |
| **Source Provenance Integrity** | **POLICY VIOLATION**: Underlying filings identify `source: SCREENER_QUARTERLY_STANDALONE`. Under `AGENTS.md`, Screener data is restricted to `FORENSIC_REFERENCE_ONLY` and prohibited from production certification. | Strategy blocked from production certification. |

---

## 2. In-Depth Fundamental Filing Census

### 2.1 Statement Breakdown
Ingestion of `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` (SHA256: `817f214e9bafe8c7334ef6429848f356aabac2e05db3e2e888522fb5bee1a9a5`):
- **Total Ingested Records**: 16,733 financial statements.
- **Unique Symbols**: 861 NSE/BSE companies.
- **Annual Statements (`statement_type == 'ANNUAL'`)**: 8,634 rows (10 to 15 years of annual balance sheets/P&L).
- **Quarterly Statements (`statement_type == 'QUARTERLY'`)**: 8,099 rows across 848 companies.

### 2.2 Quarterly Filing Date & Period End Chronology
An exact census of quarterly filings reveals a massive historical discontinuity:

| Period End Date Span | Calendar Quarters | Total Quarterly Statements | Average Filings per Quarter | Status |
| :--- | :---: | :---: | :---: | :--- |
| **2012-03-31 to 2022-12-31** | 44 quarters | 118 statements | **2.7 filings / quarter** | **TRUNCATED / SPARSE** |
| **2023-06-30 to 2023-12-31** | 3 quarters | 2,228 statements | **742 filings / quarter** | **SYSTEMATIC COVERAGE** |
| **2024-06-30 to 2024-12-31** | 3 quarters | 2,416 statements | **805 filings / quarter** | **SYSTEMATIC COVERAGE** |
| **2025-06-30 to 2025-12-31** | 3 quarters | 2,438 statements | **812 filings / quarter** | **SYSTEMATIC COVERAGE** |
| **2026-06-30** | 1 quarter | 809 statements | **809 filings / quarter** | **SYSTEMATIC COVERAGE** |

**Forensic Discovery**: Across the 848 companies with quarterly data, **727 companies have exactly 10 quarterly filings**, spanning exclusively June 2023 through June 2026. The upstream ingestion system did not archive multi-year historical quarterly XBRL filings prior to calendar year 2023.

---

## 3. Mathematical Viability of the Frozen SUE Formula

The frozen ESD-1 specification requires:
$$\Delta E_q = E_q - E_{q-4}$$
$$SUE_q = \frac{\Delta E_q}{\operatorname{SD}(\Delta E_{q-1}, \dots, \Delta E_{q-8})}$$
Where the denominator is the sample standard deviation of the **preceding eight available year-over-year earnings changes**.

### The History Requirement:
- To calculate one YoY change $\Delta E_q$, a company must have $E_q$ and $E_{q-4}$ (5 quarters of data).
- To calculate $\Delta E_{q-1}$ through $\Delta E_{q-8}$, the earliest change $\Delta E_{q-8}$ requires $E_{q-8}$ and $E_{q-12}$.
- Therefore, a company must possess at least **13 consecutive quarters of quarterly EPS** before its first valid SUE score can be computed.

### Universe Eligibility Under Strict Rule:
- Companies with $\ge 13$ quarters in repository: **2 companies** (`JUSTDIAL` and `TATAELXSI`).
- Total events satisfying strict frozen rule: **13 events**.
- Qualifying events with $SUE \ge 1.0$: **4 events**.

**Conclusion**: The frozen ESD-1 rule cannot be evaluated as an investable 20-slot strategy across the 2016–2026 decade because 99.8% of companies lack the requisite 13 quarters of historical filings.

---

## 4. Empirical Relaxation Diagnostic: 4-Quarter Lookback ($k=4$)

To test whether post-earnings drift exists in the data that *is* available, the engine evaluated an empirical diagnostic specification:
$$SUE_q^{\text{viable}} = \frac{\Delta E_q}{\operatorname{SD}(\Delta E_{q-1}, \dots, \Delta E_{q-4})}$$
Requiring at least 4 preceding YoY changes ($\ge 8$ quarters of history).

- Eligible Events under Viable Rule: **3,058 events across 798 symbols**.
- Qualifying Signals ($SUE \ge 1.0$ & Response Filter): **864 candidate events**.
- Evaluation Window: Restricted to **October 2024 to October 2026** (the only window where 8 prior quarters exist).

---

## 5. Compliance with Workspace Data Integrity Rules (`AGENTS.md`)

Section "Mandatory Screener.in Forensic & Diagnostic Discovery Protocol" of `AGENTS.md` explicitly mandates:
> *"Screener role = FORENSIC_REFERENCE_ONLY. Production certification = PROHIBITED. Buy decision input = PROHIBITED. Must never satisfy any scanner completeness gate or replace Upstox/NSE/BSE native feeds."*

Inspection of raw JSON filing records in `data/pit_raw_filings/` confirms:
```json
"source": "SCREENER_QUARTERLY_STANDALONE",
"source_provider": "SCREENER",
"timestamp_basis": "LODR_STATUTORY_DEADLINE_CONSERVATIVE"
```
Because the underlying quarterly earnings figures were ingested via Screener.in data pipelines rather than certified raw exchange XBRL filings from NSE/BSE, **promoting ESD-1 to production alert generation would violate the core project governance rules.**

---

## 6. Audit Verdict

**DATA INTEGRITY STATUS**: `DATA_INSUFFICIENT_HISTORY` & `PROVENANCE_PROHIBITED`  
**Certification Gate Verdict**: **FAIL CLOSED (RESEARCH_HOLD)**.
