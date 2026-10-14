# PQLV-1 — Official Nifty100 Low Volatility 30 Methodology & Replication Protocol

**Document Purpose**: Document the official NSE index methodology for Nifty100 Low Volatility 30, define the independent benchmark replication engine, and delineate every architectural difference between the official index and the custom PQLV-1 strategy.

---

## 1. Official Nifty100 Low Volatility 30 Index Methodology

According to the official NSE Indices methodology:

### A. Universe & Eligibility
- **Parent Universe**: Nifty 100 constituents at the time of reconstitution.
- **Trading History**: The stock must have traded for a minimum of 90% of trading days during the trailing 1-year period.
- **Sector Restrictions**: None (Financials, banks, and NBFCs are fully eligible).

### B. Volatility Calculation
- **Period**: Trailing 1 year (252 valid trading days).
- **Metric**: Standard deviation of daily natural log returns:
  $$R_t = \ln\left(\frac{P_t}{P_{t-1}}\right)$$
  $$\sigma_{252} = \sqrt{\frac{1}{251}\sum_{t=1}^{252}(R_t - \bar{R})^2} \times \sqrt{252}$$
- Selection: Top 30 stocks with the lowest annualised standard deviation ($\sigma_{252}$) are selected.

### C. Weighting Scheme
- **Inverse Volatility Weighting**: Constituents are weighted inversely proportional to their realised standard deviation:
  $$W_i = \frac{1 / \sigma_{i, 252}}{\sum_{j=1}^{30} \left(1 / \sigma_{j, 252}\right)}$$
- **Stock Cap**: Individual stock weights are capped at 5.0%. Excess weight is redistributed proportionally among remaining uncapped constituents.
- **Sector Cap**: None.

### D. Rebalancing Frequency
- **Cadence**: Semiannual in June and December.
- **Effective Dates**: Effective from the open of trading on the next trading day following the last trading day of June and December.

---

## 2. Comparison: Official Index vs. Custom PQLV-1 Strategy

| Dimension | Official Nifty100 Low Volatility 30 | Custom PQLV-1 Strategy | Research Purpose of Difference |
| :--- | :--- | :--- | :--- |
| **Parent Universe** | Nifty 100 (Large Cap) | Nifty 500 (Large & Mid Cap) | Tests whether low volatility works in broader universe |
| **Financial Companies (BFSI)**| **Included** (Banks, NBFCs eligible) | **Excluded** (Operating non-financials only) | Prevents leverage distortion in quality filters |
| **Fundamental Quality Gates**| **None** (Pure price volatility) | **5 Strict Gates** (PAT > 0 5Y, Sales, ROCE, CFO) | Tests whether profit quality improves factor returns |
| **Constituent Weighting** | **Inverse Volatility Weighted** | **Equal Slots** (1/30 target per slot = 3.33%) | Eliminates weight distortion & caps single-stock bets |
| **Sector Constraint** | **No Sector Cap** (High FMCG/IT concentration) | **Maximum 25% Cap per Sector** | Prevents structural defensive sector over-concentration |
| **Cash Drag Handling** | Fully invested index | Unallocated slots remain in cash | Realistic portfolio cash flow accounting |
| **Rebalance Cadence** | Semiannual (June & December) | Semiannual (June & December) | Aligned for exact common-date comparability |

---

## 3. Replication Engine Architecture in the Backtest

In the ablation tournament:
- **Model A** executes the faithful replication of the official Nifty100 Low Volatility 30 methodology (Nifty 100 universe, inverse volatility weighting, 5% cap, semiannual rebalance).
- **Model D** executes the custom PQLV-1 strategy (Nifty 500 non-financials, 5 quality gates, equal 1/30 sizing, 25% sector cap).
- **Model B** isolates the Quality-Only effect.
- **Model C** isolates the Low-Volatility-Only effect on the custom universe.

This structural separation ensures that any measured outperformance or underperformance can be attributed cleanly to either (1) the low-volatility factor, (2) the quality gates, or (3) the portfolio weighting constraints.
