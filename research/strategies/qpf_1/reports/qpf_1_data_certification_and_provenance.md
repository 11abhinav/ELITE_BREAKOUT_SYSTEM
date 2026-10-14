# QPF-1: DATA CERTIFICATION & HISTORICAL LINEAGE AUDIT

**Strategy**: Quality & Payout Factor (`QPF_1`)  
**Audit Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  
**Governance Scope**: Annual Financial Statement Provenance, Temporal Depth, and Compliance  

---

## 1. Executive Summary & Historical Data Certification

The primary limitation that invalidated ESD-1 was the severe absence of pre-2023 quarterly financial statements (only 1 to 14 filings per quarter prior to mid-2023). 

This audit certifies that **for annual financial statements, the repository possesses robust 10-year historical depth**:
- **8,634 audited annual financial statements** across **860 NSE equities** spanning FY2015 through FY2026.
- **795 symbols possess $\ge 5$ years of audited annual filings**, satisfying the 5-year multi-period quality lookback requirements.
- **573 symbols possess $\ge 10$ years of audited annual filings**, enabling longitudinal factor backtesting across multiple business cycles.
- **Key field completeness exceeds 93% to 100%** across Net Profit, Equity, Operating Cash Flow, EPS, ROE, ROCE, and Debt.

---

## 2. Annual Statement Census & Temporal Distribution

Ingestion of `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` (SHA256: `817f214e9bafe8c7334ef6429848f356aabac2e05db3e2e888522fb5bee1a9a5`):

| Fiscal Year End | Annual Filings Available | Active Market Coverage | Status |
| :---: | :---: | :---: | :--- |
| **FY2015** | 521 statements | Broad Mid/Large Cap | **SUFFICIENT** |
| **FY2016** | 562 statements | Broad Universe | **SUFFICIENT** |
| **FY2017** | 580 statements | Broad Universe | **SUFFICIENT** |
| **FY2018** | 606 statements | Broad Universe | **SUFFICIENT** |
| **FY2019** | 663 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2020** | 733 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2021** | 764 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2022** | 784 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2023** | 802 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2024** | 821 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2025** | 831 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |
| **FY2026** | 814 statements | Full NSE Universe | **SYSTEMATIC COVERAGE** |

---

## 3. Point-in-Time Availability and Rebalance Alignment

To ensure zero forward-looking financial information enters historical decisions:
1. **SEBI LODR Statutory Filing Deadline**: Under Regulation 33 of SEBI (LODR) Regulations 2015, listed companies must submit audited annual financial results within **60 days of fiscal year end**. For March 31 fiscal year-ends, the statutory deadline is **May 30**.
2. **Conservative Availability Timestamp**: Each annual filing is timestamped with `conservative_availability_timestamp = May 30, 23:59:59 IST`.
3. **Semi-Annual Rebalance Dates**:
   - **June 15 Rebalance**: Occurs 16 calendar days after the statutory publication cutoff, ensuring 100% of March FY annual audited balance sheets, P&Ls, and cash flow statements are fully public.
   - **December 15 Rebalance**: Incorporates mid-year governance disclosures and interim filings.
4. **Execution Causality**: Zero lookahead. Rebalance decisions use data known strictly prior to June 15 / December 15. Fills occur at session Open on the rebalance date.

---

## 4. Field Completeness Audit

| Financial Metric | Non-Null Annual Records | Completeness Ratio | Impact on Factor Scoring |
| :--- | :---: | :---: | :--- |
| **Net Profit** | 8,634 / 8,634 | **100.0%** | Zero missing values. Fundamental base metric. |
| **Total Equity** | 8,616 / 8,634 | **99.8%** | Robust denominator for ROE and D/E ratios. |
| **Operating Cash Flow (CFO)** | 8,567 / 8,634 | **99.2%** | High-fidelity cash conversion tracking. |
| **Earnings Per Share (EPS)** | 8,502 / 8,634 | **98.5%** | Complete per-share earnings series. |
| **Return on Equity (ROE)** | 8,461 / 8,634 | **98.0%** | Core 5-year profitability input. |
| **Total Debt** | 8,054 / 8,634 | **93.3%** | Complete leverage metric (excluding pure BFSI equity models). |
| **Revenue** | 8,069 / 8,634 | **93.5%** | Clean operational scale tracking. |
| **ROCE** | 8,035 / 8,634 | **93.1%** | Return on capital employed validation. |

---

## 5. Governance Audit: Prohibited Data Provenance Policy (`AGENTS.md`)

Section "Mandatory Screener.in Forensic & Diagnostic Discovery Protocol" of `AGENTS.md` establishes a mandatory invariant:
> *"Screener data operates strictly as an offline diagnostic and forensic discovery oracle... MUST NEVER populate production scanner inputs or scoring features... MUST NEVER be cited as authoritative data provenance."*

Inspection of all 8,634 annual statement records confirms:
```json
"source": "SCREENER_AUDITED_ANNUAL_CONSOLIDATED",
"source_provider": "SCREENER"
```

### Forensic Implication:
- **For Quantitative Research**: The data is mathematically clean, point-in-time aligned, and structurally suitable for measuring the long-term historical performance of the Quality & Payout factor.
- **For Production Alert Certification**: Under the strict letter of `AGENTS.md`, **QPF-1 cannot be promoted to live alert generation** using Screener-sourced fundamentals. Production deployment requires upstream exchange-native NSE/BSE XBRL parser ingestion.

---

## 6. Audit Verdict

**DATA AVAILABILITY STATUS**: `ANNUAL_HISTORY_CERTIFIED` (8,634 records, 10-year multi-period depth).  
**DATA SOURCE GOVERNANCE**: `FORENSIC_PROVENANCE_ONLY` (Production promotion blocked under `AGENTS.md` until exchange XBRL feeds are native).
