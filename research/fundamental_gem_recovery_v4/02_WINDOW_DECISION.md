# STUDY WINDOW DECISION DOCUMENT: `FUNDAMENTAL_GEM_RECOVERY_V4`

## 1. Executive Decision
* **Decided Study Window**: **`2016-01-01 to 2026-06-30`** (10.5 Years)
* **Development Window**: **2016-01-01 to 2024-12-31** (9 Years)
* **Holdout Window**: **2025-01-01 to 2026-06-30** (1.5 Years - Report Only)
* **Earliest 80%+ Coverage Year**: **2006**

---

## 2. Coverage Audit Summary
* **Total Filings Audited**: 7707 filings across 795 unique symbols.
* **Price Data Coverage**: 947 active and historical symbols present in Upstox 1D cache.
* **Core Fundamental Metrics Coverage (2016–2026)**:
  * `roce` / `roe`: >90% non-null coverage
  * `revenue` / `net_profit`: >95% non-null coverage
  * `operating_cash_flow`: >85% non-null coverage
  * `total_debt` / `total_equity`: >90% non-null coverage

---

## 3. Lookback & Ratio Fallback Protocol
* **10-Year vs 5-Year ROCE**: Because fundamental filings prior to 2010 have ~70-75% coverage, a **5-Year Average ROCE** (min 15%) and **5-Year Median Valuation** baseline are applied consistently across all evaluation dates starting from 2016-01-01.
* **Survival Bias Control**: Delisted and historical suspended symbols with certified price history are fully retained in the universe.
