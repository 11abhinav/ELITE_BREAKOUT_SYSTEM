# QGIO-1 — Point-in-Time Data Inventory & Lineage Audit

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Audit Timestamp**: 2026-10-13  
**Audit Purpose**: Forensic inventory of historical financial, ownership, and price datasets in the repository to determine point-in-time backtest feasibility.

---

## 1. Inventory Summary Table

| Data Dimension | Target Requirement | Repository Location / Source | Historical Span | Coverage Depth | Provenance Status (`AGENTS.md`) | Usability Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Annual Financials** | 5 consecutive years PAT > 0, Sales CAGR $\ge 10\%$, ROCE $\ge 15\%$, Net Debt/EBITDA $\le 1.0$ | `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` | FY2015 – FY2026 | 8,634 annual statements across 860 symbols | `SCREENER_AUDITED_ANNUAL_CONSOLIDATED` | **QUALIFIED FOR RESEARCH ONLY** (`FORENSIC_REFERENCE_ONLY`; blocked from live alerts) |
| **Quarterly Financials** | 8 consecutive quarters PAT > 0, TTM sales growth $\ge 8\%$ | `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet` | Q1 FY24 – Q1 FY27 (2023-06 to 2026-06) | 8,099 statements across 848 symbols | `SCREENER_AUDITED_QUARTERLY_CONSOLIDATED` | **CHRONOLOGICALLY INSUFFICIENT** (Only 10 quarters total; 8 consecutive quarters exist only from mid-2025 onward; 0 coverage prior to 2023) |
| **Daily Price (OHLCV)** | 50/200-day SMAs, 20-day 200 SMA slope, median traded value | `data/pead_v2_price_cache/*.parquet` & `data/price_cache/*.parquet` | 2017-01-01 to 2026-10-12 | 836 NSE symbols, 2,400+ daily bars per symbol | `UPSTOX_V2_NATIVE_1D` | **CERTIFIED** (Full Upstox provenance, zero forward-looking leakage) |
| **Disclosed Promoter Buying** | 90-day on-market buying minus selling, net $\ge 0.05\%$ free float | None | None | 0 records | `DATA_ABSENT` | **CRITICAL DEFECT: ABSENT** (No SEBI Reg 7(2) / SAST insider disclosure archive exists) |
| **FII / DII Bulk & Block Deals** | Identified institutional buying minus selling, net $\ge 0.05\%$ free float | `data/fii_block_deals.json` | 2026-10-03 (Single date snapshot) | 3 symbols (`ARMEE`, `ELEVATE`, `GKSL`) | `EPHEMERAL_SNAPSHOT` | **CRITICAL DEFECT: ABSENT** (No historical archive from 2018–2026; single ephemeral snapshot only) |
| **Shareholding Patterns** | Quarterly promoter, FII, DII stake changes (corroboration) | None | None | 0 records | `DATA_ABSENT` | **CRITICAL DEFECT: ABSENT** (No historical quarterly shareholding filings exist in repository) |
| **Promoter Pledging** | Promoter pledge $\le 5\%$ of promoter holdings | `data/elite_wealth_system.parquet` (`Promoter_Pledge` col) | Current snapshot | 300 rows, but **0 non-null values (100% NaN)** | `DATA_UNAVAILABLE` | **CRITICAL DEFECT: ABSENT** (Column is 100% unpopulated) |
| **Benchmark Series** | Nifty 500 TRI & Nifty500 Quality 50 TRI | `data/pead_v2_price_cache/NIFTY500.parquet` & composite Quality 50 proxy | 2018-06-15 to 2026-10-12 | 2,045 trading sessions | `UPSTOX_BENCHMARK_PROVENANCE` | **CERTIFIED** |

---

## 2. Forensic Findings by Dimension

### A. Annual Fundamentals (Gate 1 Eligibility)
- **Dataset Path**: `data/pit_fundamentals_v1/pit_fundamentals_v1.parquet`
- **Volume**: 8,634 consolidated annual records across 860 symbols.
- **Completeness**: Net profit (100%), Revenue (100%), ROCE (93.1%), Total Debt (93.3%), Operating Cash Flow (99.2%).
- **Point-in-Time Watermarking**: Enforces a +75-day publication availability lag from FY end (June 15 availability for March 31 filings).
- **Governance Status**: As established in the QPF-1 certification, the underlying data originates from Screener audited annual financials. Under `AGENTS.md`, this data is classified as `FORENSIC_REFERENCE_ONLY`. It is legally barred from triggering live production alerts.

### B. Quarterly Fundamentals (Gate 1 Strict Profitability)
- **Target Rule**: "Consolidated net profit positive in each of the last eight reported quarters."
- **Repository Reality**:
  - Total quarterly statements in `pit_fundamentals_v1.parquet`: 8,099 statements.
  - Distribution across time:
    - 2012–2022: Only 1 to 14 symbols per quarter (effectively zero coverage).
    - 2023-06-30 to 2026-06-30: 732 to 820 symbols per quarter (10 quarters total).
  - **Defect**: Requiring 8 consecutive quarters of history means no company can qualify before Q1 FY26 (June 2025). Evaluating this rule point-in-time across the historical backtest window (2018–2024) would fail 100% of the universe on `DATA_UNAVAILABLE`.

### C. Disclosed Ownership Accumulation (Gate 2 Net Buying)
- **Target Rule**:
  - Reconstruct disclosed on-market transactions by promoters/promoter group, FIIs, and DIIs over the prior 90 calendar days.
  - Calculate `net_buy_shares = buy_shares - sell_shares` and `net_buy_value`.
  - Exclude inter-se transfers, gifts, rights issues, preferential allotments, and pledge invocations.
  - Require net buying value $\ge 0.05\%$ of free-float market capitalisation.
- **Repository Reality**:
  - No SEBI PIT Regulation 7(2) insider trading disclosure table exists in the repository.
  - No historical NSE/BSE bulk or block deal archive exists in the repository. The only block deal file is `data/fii_block_deals.json`, which is an ephemeral 1-day snapshot dated `2026-10-03` covering only 3 symbols.
  - In `app/data_registry.py`, `block_deals` is registered under `StorageTier.EPHEMERAL` (cadence 86400s), designed for live intraday/daily polling, not historical backtest storage.
  - In `data/elite_universe_v2.parquet`, the column `block_deals_30d` is populated with `0` across all 176 rows.
  - In `data/elite_wealth_system.parquet`, the column `Promoter_Pledge` has 0 valid values (`NaN` across 100% of rows).
- **Defect**: Gate 2 cannot be evaluated empirically from historical records. There is zero historical transaction-level ownership data.

### D. Daily Price & Technical Indicators (Gate 3 Trend Filter)
- **Dataset Path**: `data/pead_v2_price_cache/*.parquet`
- **Coverage**: 836 NSE symbols from 2017 to 2026.
- **Data Quality**: 100% Upstox verified native OHLCV. Zero forward-looking leakage.
- **Indicator Computability**: 50-day SMA, 200-day SMA, and 20-day slope of 200 SMA can be calculated with mathematical exactness across all trading sessions.

---

## 3. Data Readiness Conclusion

The repository supports:
1. Long-term annual financial quality screening (FY2015–FY2026).
2. Daily price trend analysis and 50/200 SMA filtering (2018–2026).
3. Benchmark tracking against Nifty 500 TRI and Nifty500 Quality 50.

The repository **DOES NOT support**:
1. Point-in-time 8-quarter profitability screening prior to mid-2025.
2. Historical promoter on-market transaction reconstruction (SEBI PIT Reg 7(2)).
3. Historical FII/DII bulk and block deal accumulation (2018–2026).
4. Historical quarterly shareholding pattern filing verification.
5. Historical promoter pledge tracking.

Under the user's explicit operating rules:
> *"If essential historical data is absent or cannot be reconstructed point-in-time, stop full certification and report RESEARCH_HOLD. Do not generate synthetic history to fill gaps."*

Gate 2 is **BLOCKED ON DATA NON-EXISTENCE**.
