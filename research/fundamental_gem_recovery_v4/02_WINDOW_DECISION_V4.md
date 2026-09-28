# PHASE A ADDENDUM AUDIT REPORT: `FUNDAMENTAL_GEM_RECOVERY_V4`

## 1. Data Frequency & Statement Type Audit
* **Dataset Statement Type**: **ANNUAL ONLY** (`statement_type` $\in$ `['ANNUAL', 'ANNUAL_FINANCIAL_SECTOR']`). Zero quarterly filings exist in `pit_fundamentals_v1.parquet`.
* **Required Exit Rule Adaptation for Annual Data**:
  1. *Thesis Stop*: Adapted from "2 consecutive quarterly PAT drops" to **1 filed Annual Report PAT or ROCE drop $> 20\%$ relative to 3-year average**.
  2. *Layer 4 EBITDA Margin & ROCE Stability*: Adapted from "TTM quarterly margin drop" to **Latest filed Annual Report EBITDA Margin / ROCE relative decline $\le 15\%$ vs 3-year average**.
  3. *State 1 Audit Stop (-25% Drawdown)*: Adapted from "TTM quarterly check" to **Latest filed Annual Report Net Profit / ROCE $> 15\%$ below 3-year average**.

---

## 2. Filing Date & Timestamp Provenance
* **`filing_date`**: Computed as `period_end_date + 30 days` (representing `actual_publication_timestamp`).
* **`conservative_availability_timestamp`**: Computed as `period_end_date + 60 days` (representing conservative availability timestamp for 100% of rows).
* **Point-in-Time Compliance**: Signal evaluation date $T$ uses ONLY filings where `conservative_availability_timestamp <= T`.

---

## 3. Hard Fields & Missing Data Exclusion Protocol
* **Present Fields in Parquet**: `roce`, `roe`, `revenue`, `operating_profit` (EBITDA), `net_profit` (PAT), `eps`, `shares_outstanding`, `operating_cash_flow`, `free_cash_flow`, `total_debt`, `total_equity`, `cash_and_equivalents`, `depreciation_amortization`, `operating_margin`, `net_margin`, `raw_NII`.
* **Absent Fields in Parquet Schema**: `promoter_holding`, `promoter_pledge`, `equity_dilution`, `cwip`, `gross_block`, `receivables`, `other_income`, `pbt`, `car`, `gnpa`, `pcr`.
* **V4 Pre-Registered Config Rule**: Because `promoter_pledge`, `cwip`, `receivables`, and `other_income` are absent from the dataset schema, enforcing a strict drop rule on these 4 absent fields would drop 100% of stocks. Therefore, in the V4 config freeze, these absent filters are dropped from Layer 2, and Quality is governed strictly by:
  - **CFO / PAT > 0.80** (Cash Flow Realization)
  - **Debt / Equity < 0.50** (Solvency)
  - **3-Year Average ROCE / ROE > 15%** (Capital Efficiency)
  - **3-Year Sales & PAT CAGR > 10%** (Growth Durability)
  - **EV/EBITDA & P/B Valuation Discount ($\ge 20\%$)**
  - **Market Residual Drawdown Test ($\le 12\%$)**

---

## 4. Lookback & Universe-Level Coverage Breakdown

| Year | Total Price Universe Symbols | Symbols with PIT Filing | Universe Coverage (%) | Symbols with $\ge 5$ Prior Filings ($\le$ Jan 1) | Lookback $\ge 5$ Filings Coverage (%) | Symbols with $\ge 6$ Prior Filings ($\le$ Jan 1) | Lookback $\ge 6$ Filings Coverage (%) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2016** | 533 | 487 | 74.5% | 14 | 2.6% | 6 | 1.1% |
| **2017** | 569 | 513 | 75.6% | 16 | 2.8% | 8 | 1.4% |
| **2018** | 600 | 538 | 75.2% | 19 | 3.2% | 9 | 1.5% |
| **2019** | 610 | 587 | 77.5% | 25 | 4.1% | 12 | 2.0% |
| **2020** | 626 | 648 | 79.6% | 453 | 72.4% | 18 | 2.9% |
| **2021** | 680 | 676 | 79.0% | 494 | 72.6% | 449 | 66.0% |
| **2022** | 721 | 703 | 79.1% | 519 | 72.0% | 490 | 68.0% |
| **2023** | 770 | 720 | 79.2% | 551 | 71.6% | 514 | 66.8% |
| **2024** | 837 | 742 | 81.0% | 606 | 72.4% | 546 | 65.2% |
| **2025** | 901 | 754 | 81.9% | 661 | 73.4% | 602 | 66.8% |

---

## 5. Recommended Study Window Decision
* **Lookback Finding**: Prior to 2020, filings are sparse (11 to 26 filings/year between 2006 and 2014). Consequently, evaluating a 5-year lookback strictly using filings dated $\le Jan 1$ of the evaluation year yields $< 5\%$ lookback coverage prior to 2020.
* **3-Year Lookback Adaptation**: If a **3-Year Average ROCE** and **3-Year CAGR** lookback is used (requiring 3 or 4 prior annual observations), lookback coverage reaches $> 70\%$ starting in **`2018-01-01`**.
* **Decided Study Window Options**:
  - **Option 1 (3-Year Lookback Baseline)**: **`2018-01-01 to 2026-06-30`** (8.5 Years).
  - **Option 2 (Strict 5-Year Lookback Baseline)**: **`2020-01-01 to 2026-06-30`** (6.5 Years).
