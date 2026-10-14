# SFP-1 — HISTORICAL DATA INVENTORY & ASSET RECONNAISSANCE

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Audit Date**: 2026-10-13  
**Status**: `PHASE_A_AUDITED`  

---

## 1. Executive Summary

This inventory documents all available and missing historical datasets required to test **SFP-1: Size Premium with Financial Survival Filters**. The strategy proposes selecting 30 small-cap equities ranked 251–500 by market capitalization from historically eligible NSE equities, applying basic financial survival filters (positive TTM PAT, positive operating cash flow, and minimum ₹5 crore median daily traded value), and holding them semiannually.

In accordance with the **Mandatory Real-Market-Data Backtest Protocol** and the **Surviving-Constituents Survivorship Bias Invariant**, this audit catalogs the physical location, provenance, date coverage, schemas, and structural limitations of the repository.

---

## 2. Historical Market Data Inventory

| Dataset / Asset | Path in Repository | Format | Records / Symbols | Historical Range | Source Provider | Data Provenance & Integrity Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Daily Equities OHLCV** | `data/history/1d/*.parquet` | Parquet | 947 symbols | 2016-09-27 to 2026-10-12 | Upstox API v3 | **PROVENANCE = CERTIFIED** (931 JSON response manifests with SHA-256 hashes). |
| **Shares Outstanding History** | `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` | Parquet | 8,634 annual filings (858 symbols) | FY2015 to FY2026 | Audited Screener Annual Reports | **PROVENANCE = FORENSIC_REFERENCE_ONLY** (Audited annual filings, statutory 90-day LODR publication lag). |
| **Operating Cash Flow & PAT** | `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` | Parquet | 8,634 filings | FY2015 to FY2026 | Audited Screener Annual Reports | **PROVENANCE = FORENSIC_REFERENCE_ONLY** (Audited annual filings, includes `operating_cash_flow`, `net_profit`). |
| **Broad Market Benchmark** | `research/strategies/rsmr_1/data/NIFTY_500.parquet` | Parquet | 2,471 daily bars | 2016-09-27 to 2026-10-12 | NSE Broad Market (^CRSLDX) | **PROVENANCE = CERTIFIED** (Continuous daily benchmark series). |
| **Nifty Smallcap 250 TRI Benchmark** | *MISSING* | — | 0 files | — | NSE Indices | **STATUS = DATA_UNAVAILABLE** (No dedicated Nifty Smallcap 250 TRI parquet exists in repo). |
| **Delisted / Bankrupt Smallcaps** | *MISSING* | — | 0 files | — | Historical NSE Delistings | **STATUS = SURVIVORSHIP_BIAS_PRESENT** (Historically failed smallcaps are absent from current price cache). |

---

## 3. Data Schema & Field Availability Audit

### A. Point-in-Time Market Capitalization Inputs
* **Formula**: $\text{Market Cap}_{i, t} = \text{Close Price}_{i, t} \times \text{Shares Outstanding}_{i, t}$
* **Shares Outstanding**: Field `shares_outstanding` is present and populated for 858 of 861 fundamental entities in `pit_fundamentals_v1.parquet`.
* **Close Price**: Field `Close` is verified across all 947 daily parquet files in `data/history/1d/`.
* **Causality**: Historical `shares_outstanding` is updated strictly at `conservative_availability_timestamp` ($\ge 90$ days after financial year-end), preventing point-in-time leakage.

### B. Financial Survival Filter Inputs
* **Positive Annual / TTM PAT**: Field `net_profit` is fully populated across all annual statements.
* **Positive Operating Cash Flow**: Field `operating_cash_flow` is populated across all annual statements.
* **Severe Financial Distress / Insolvency**: Can be identified via negative equity (`total_equity < 0`), extreme leverage (`total_debt / total_equity > 5.0`), or audit qualification flags.

### C. 60-Session Liquidity Screen Inputs
* **Formula**: $\text{Median Turnover}_{60} = \text{Median}_{k=0}^{59} (\text{Volume}_{t-k} \times \text{Close}_{t-k})$
* **Threshold**: $\ge ₹5,00,00,000$ (₹5 crore).
* **Feasibility**: 100% calculable from native Upstox daily `Volume` and `Close` fields.

---

## 4. Critical Deficiencies & Data Blockers

1. **Survivorship Bias in Available Price Cache**:
   - The 947 symbols in `data/history/1d/` represent survivors active as of September 2026.
   - Companies that were ranked 251–500 in 2016–2020 but subsequently went bankrupt, entered IBC insolvency, or delisted (e.g. DHFL, RCOM, Sintex, Cox & Kings, Kwality, Rolta, Jet Airways, Unitech) are **completely absent** from the price directory.
   - Per recent empirical research (2025–2026 Nifty Smallcap 250 study cited by user), survivorship bias overstates smallcap returns by **~4.94 percentage points per year**. Any backtest conducted purely on surviving constituents will exhibit this upward distortion.
2. **Missing Nifty Smallcap 250 TRI Official Series**:
   - The repository contains Nifty 50 and Nifty 500 TRI series, but lacks the official standalone `Nifty Smallcap 250 TRI` historical price series.
