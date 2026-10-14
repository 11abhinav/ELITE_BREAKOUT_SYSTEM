# QGIO-1 — Point-in-Time Data Readiness Gate & Feasibility Certification

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Audit Date**: 2026-10-13  
**Audit Purpose**: Formal evaluation of the Data Readiness Gate before executing strategy simulations.

---

## 1. Readiness Gate Protocol

Under project operating rules and Section 3 of the research assignment:
> *"Explicitly verify that any financial source used for strategy certification is permitted for that purpose under repository governance. Do not certify results from a source designated forensic-reference-only unless an approved, independently verified alternative exists.*  
> *If essential historical data is absent or cannot be reconstructed point-in-time, stop full certification and report RESEARCH_HOLD. Do not generate synthetic history to fill gaps."*

---

## 2. Gate-by-Gate Data Availability Audit

### Gate 1: Business Quality Data Readiness
- **Annual Quality Metrics**:
  - Net Profit > 0 (5 consecutive years): **AVAILABLE** (FY2015–FY2026 in `pit_fundamentals_v1.parquet`).
  - Sales CAGR $\ge 10\%$ (3 years): **AVAILABLE**.
  - ROCE $\ge 15\%$ (Latest & 3Y average): **AVAILABLE**.
  - CFO / PAT $\ge 0.8$ (3-year cumulative): **AVAILABLE**.
  - Net Debt / EBITDA $\le 1.0$: **AVAILABLE**.
  - Non-Financial Filtering (Exclude BFSI): **AVAILABLE** (via `is_bfsi` mapping).
- **Quarterly Profitability Metrics**:
  - Positive Net Profit in each of last 8 quarters: **CHRONOLOGICALLY INSUFFICIENT**.
  - Repository contains only 10 quarters of statements (starting Q1 FY24 / June 2023).
  - 8 consecutive quarters of history only accumulate by mid-2025.
  - Evaluating this gate point-in-time across 2018–2024 results in a 100% data failure rate (`DATA_UNAVAILABLE`).
- **Gate 1 Status**: **CONDITIONALLY QUALIFIED (ANNUAL METRICS ONLY)**. The 8-quarter requirement cannot be evaluated historically prior to mid-2025.

---

### Gate 2: Verified Ownership Accumulation Data Readiness
- **Disclosed Promoter Purchases / Sales (SEBI PIT Reg 7(2))**:
  - 90-day transaction-level on-market purchases and disposals: **ABSENT (0 records in repository)**.
- **Identifiable FII / DII Bulk and Block Transactions**:
  - Historical bulk and block deals: **ABSENT (Only 1 daily snapshot from 2026-10-03 covering 3 symbols exists in `data/fii_block_deals.json`)**.
  - `block_deals` in `app/data_registry.py` is registered as `EPHEMERAL`, designed for live polling, not historical backtest storage.
  - `block_deals_30d` in `data/elite_universe_v2.parquet` is 0 across 100% of rows.
- **Quarterly Shareholding Patterns**:
  - Historical quarterly filings with publication timestamps: **ABSENT (0 filings in repository)**.
- **Promoter Pledges**:
  - `Promoter_Pledge` column in `data/elite_wealth_system.parquet` is **100% NaN (0 non-null values)**.
- **Transfer & Corporate Action Exclusion**:
  - Registry of inter-se transfers, preferential allotments, and pledge invocations: **ABSENT**.
- **Gate 2 Status**: **FAILED / DATA NON-EXISTENT**.
  - Generating synthetic bulk deals or inferring buying without underlying disclosure records is strictly prohibited by project invariants (`AGENTS.md`).

---

### Gate 3: Price-Trend Confirmation Data Readiness
- **50-Day & 200-Day Daily SMAs**: **CERTIFIED** (Upstox certified 1D parquet cache across 836 symbols).
- **20-Day Slope of 200 SMA**: **CERTIFIED**.
- **Liquidity Screen (Median traded value $\ge$ ₹5 Cr over 60 sessions)**: **CERTIFIED**.
- **Gate 3 Status**: **FULLY PASSED**.

---

## 3. Data Readiness Gate Verdict

| Strategic Track | Strategy Definition | Data Readiness Status | Backtest Feasibility |
| :--- | :--- | :---: | :--- |
| **Track A (Baseline)** | Quality Growth + 50/200-day Trend | **QUALIFIED** | **FEASIBLE** using certified 1D prices and annual fundamentals (+75d lag). |
| **Track B (Ownership)**| Quality Growth + Ownership Accumulation (No Trend) | **DATA_ABSENT** | **INCOMPLETE** (0 historical transaction records; yields 0 trades). |
| **Track C (Full)** | Quality Growth + Ownership Accumulation + Trend | **DATA_ABSENT** | **INCOMPLETE** (0 historical transaction records; yields 0 trades). |

### Formal Verdict: **`RESEARCH_HOLD`**

1. Full certification of the primary candidate (`QGIO-1`, Strategy C) is **BLOCKED ON DATA NON-EXISTENCE** for Gate 2 (Ownership Accumulation).
2. Per mandatory operating rules:
   - We **DO NOT fabricate** synthetic bulk deals.
   - We **DO NOT infer** insider buying from price volume or current shareholding snapshots.
   - We mark Strategy B and Strategy C as `DATA_UNAVAILABLE` (0 confirmed transactions).
3. To fulfill the user's research objective and determine whether the underlying premise has merit, we execute the empirical backtest of **Strategy A (The Quality Growth + 50/200 Trend Baseline)** across 2018–2026. This measures the maximum potential performance of the quality-and-trend engine before any ownership filter is applied.
