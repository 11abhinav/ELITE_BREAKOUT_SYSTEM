# SFP-1 — FROZEN STRATEGY RULES & IMPLEMENTATION SPECIFICATION

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Frozen Specification Date**: 2026-10-13  
**Status**: `FROZEN_FOR_RESEARCH`  

---

## 1. Strategy Mission and Hypothesis

SFP-1 tests whether the **Size Factor (Small-Cap Premium)** in the Indian equity market can generate persistent risk-adjusted excess returns over the broad market (Nifty 500 TRI) when combined with basic **Financial Survival and Liquidity Filters**.

Unlike previous quality and momentum strategies:
* **Selection Mechanism**: Driven purely by **smaller market capitalization** (smallest eligible companies selected first).
* **Role of Fundamentals**: Fundamentals do not score or rank candidates; they act solely as a **survival screen** to filter out insolvent, debt-distressed, or cash-burning penny stocks.
* **Avoidance of Excessive Cash Drag**: By focusing on basic survival rather than 5 consecutive years of exceptional ROCE/margins, a broad candidate pool remains eligible, allowing the 30 portfolio slots to stay largely filled.

---

## 2. Frozen Parameter Specification

### A. Universe Definition
* **Base Universe**: Historically eligible NSE equities with available daily price and audited annual statement history.
* **Exclusions**: Financial companies (Banks, NBFCs, Insurers) are excluded if industrial cash-flow metrics (`operating_cash_flow / net_profit`) are non-comparable.
* **Size Bucket**: Equities ranked **251 to 500** by full market capitalization at each rebalance date.
  $$\text{Market Cap}_{i, t} = \text{Close Price}_{i, t} \times \text{Shares Outstanding}_{i, t}$$

### B. Liquidity Gate
* **Requirement**: Trailing 60 trading sessions median daily turnover $\ge ₹5,00,00,000$ (₹5 crore).
  $$\text{Daily Turnover}_k = \text{Volume}_k \times \text{Close}_k$$
* Candidates with median turnover $< ₹5\text{ Cr}$ are hard-blocked to avoid untradable micro-caps.

### C. Financial Survival Filters
All three conditions must pass based on filings publicly available at decision date:
1. **Positive Net Profit**: Net profit in the latest completed financial year must be strictly positive (`net_profit > 0`).
2. **Positive Operating Cash Flow**: Operating cash flow in the latest completed financial year must be strictly positive (`operating_cash_flow > 0`).
3. **Positive Net Worth**: Total equity must be strictly positive (`total_equity > 0`), excluding commercially insolvent companies.

### D. Selection and Ranking Logic
* **Primary Signal**: Ascending order of **Market Capitalization** (smallest market cap receives highest priority).
* **Tie-Breaker**: Higher 60-session median daily turnover.
* **No Secondary Factors**: Zero RSI, moving-average crosses, price momentum, or volatility filters are permitted.

### E. Portfolio Construction & Risk Constraints
* **Portfolio Size**: Target 30 positions.
* **Target Weight**: Fixed slot target of $1/30 = 3.333\%$ of portfolio NAV.
* **Single Position Cap**: Maximum 5.0% of portfolio NAV.
* **Sector Cap**: Maximum 25.0% of portfolio NAV in any single sector. Candidates that would breach the sector cap are skipped, and the selector continues down the size ranking.
* **Cash Buffer**: If fewer than 30 stocks qualify, unfilled slots remain in cash.

### F. Rebalancing Cadence & Execution
* **Rebalance Frequency**: Semiannual (final trading session of June and December).
* **Decision Cutoff**: Closing prices and published filings on rebalance date.
* **Execution**: Next eligible trading session at the opening price.
* **Interim Exits**: Delisting or severe governance/insolvency failure triggers exit at the next open.

### G. Transaction Costs & Friction Schedule
* **Brokerage**: ₹20 flat per executed order.
* **Securities Transaction Tax (STT)**: 0.1% on delivery sell orders.
* **Stamp Duty**: 0.015% on delivery buy orders.
* **Exchange & SEBI Turnover Charges**: 0.00345% on buy and sell.
* **GST**: 18% on brokerage and exchange charges.
* **Depository Participant (DP) Charges**: ₹15.93 per sell trade.
* **Slippage**: Base scenario = 5.0 bps per side; Adverse scenarios = 10.0 bps and 20.0 bps per side.

### H. Survivorship Bias Accounting
* In recognition of the ~4.94% survivorship bias identified in Indian small-cap literature:
  $$\text{Survivorship-Adjusted Net CAGR} = \text{Simulated Net CAGR} - 4.94\%$$
