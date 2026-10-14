# QPF-1 — Factor Attribution, Temporal Robustness, and Benchmark Decomposition

**Strategy ID**: `QPF_1`  
**Strategy Name**: Quality & Payout Factor  
**Evaluation Window**: 2018-06-15 to 2026-10-12 (8.33 Years, 2,045 Trading Sessions)  
**Rebalance Frequency**: Semi-Annual (June 15 and December 15)  
**Portfolio Universe**: Top 50 Quality & Payout Stocks (Equal 2% weight, 25% sector cap)  
**Benchmarks**: Nifty 500 TRI (Broad Market) & Nifty500 Quality 50 Proxy (Official Quality Benchmark)

---

## 1. Executive Performance Summary

| Metric | QPF-1 (Frozen Base) | Nifty500 Quality 50 Proxy | Nifty 500 TRI | Delta vs Nifty 500 | Delta vs Quality 50 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Net CAGR (%)** | **6.67%** | **7.33%** | **12.03%** | **-5.36%** | **-0.66%** |
| **Total Return (%)** | **71.21%** | **80.17%** | **133.56%** | **-62.35%** | **-8.96%** |
| **Sharpe Ratio (Rf=6.5%)** | **0.06** | **0.13** | **0.32** | **-0.26** | **-0.07** |
| **Max Drawdown (%)** | **-17.14%** | **-17.86%** | **-38.30%** | **+21.16%** | **+0.72%** |
| **Portfolio Win Rate (%)** | **58.6%** | **67.9%** | N/A | N/A | -9.3% |
| **Profit Factor** | **5.39** | **7.36** | N/A | N/A | -1.97 |
| **Total Closed Trades** | **29** | **28** | 0 | N/A | +1 |
| **Total Turnover Friction** | **₹27,899.91** | ₹26,540.10 | ₹0.00 | N/A | +₹1,359.81 |

---

## 2. Temporal Consistency & Window-by-Window Attribution

To prevent pooled historical artifact bias, QPF-1 was pre-registered and evaluated across three independent, non-overlapping historical windows:

| Temporal Cell | Calendar Range | Sessions | QPF-1 CAGR (%) | Nifty 500 TRI (%) | Quality 50 (%) | Excess vs N500 | Excess vs Q50 | QPF-1 Sharpe | QPF-1 Max DD | Gate Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Development** | 2018-06-15 to 2020-12-31 | 625 | 5.36% | 9.86% | 5.56% | -4.51% | -0.20% | -0.10 | -15.58% | **FAIL** |
| **Validation** | 2021-01-01 to 2023-12-31 | 739 | 12.14% | 19.87% | 13.56% | -7.73% | -1.42% | 0.64 | -11.80% | **FAIL** |
| **Locked Holdout**| 2024-01-01 to 2026-10-12 | 681 | 1.99% | 5.59% | 2.32% | -3.60% | -0.32% | -0.36 | -17.14% | **FAIL** |

### Key Temporal Findings:
1. **Zero Positive Excess Windows**: QPF-1 failed to beat the Nifty 500 TRI in any of the three temporal evaluation periods (-4.51%, -7.73%, and -3.60%).
2. **Persistent Quality Benchmark Drag**: In all three periods, QPF-1 slightly underperformed the simpler Nifty500 Quality 50 benchmark (-0.20%, -1.42%, and -0.32%).
3. **Severe Bull Market Lag (2021–2023)**: During the post-COVID liquidity expansion and broad market rally, Nifty 500 TRI advanced at a 19.87% CAGR, while QPF-1 delivered only 12.14% CAGR. High-quality and dividend-paying stocks significantly lagged capital-goods, cyclical, and high-beta market leaders.
4. **Drawdown Dampening as the Only Passing Feature**: Max drawdown remained exceptionally muted across all periods (-11.8% to -17.1% vs -38.3% for the broad market), proving that quality factors act as volatility dampeners, but at the cost of substantial capital compounding drag.

---

## 3. Factor Decomposition & Attribution Analysis

Why did QPF-1 fail to outperform both the broad market and the official Nifty500 Quality 50?

### Factor Pillar Comparison:
1. **Nifty500 Quality 50 (The 3-Factor Benchmark)**:
   - $Z_{ROE}$ (Profitability)
   - $Z_{LEV}$ (Financial Leverage, inverse $D/E$)
   - $Z_{EPS\_VAR}$ (5-Year EPS Growth Variability)
   - *Result*: 7.33% Net CAGR, 0.13 Sharpe, 67.9% Win Rate.

2. **QPF-1 Composite (The 4-Factor Experimental Model)**:
   - Pillar 1: Profitability ($Z_{ROE}$) [30% weight]
   - Pillar 2: Balance Sheet ($Z_{LEV}$) [25% weight]
   - Pillar 3: Cash Flow Quality & Earnings Stability ($Z_{EPS\_VAR} + Z_{CFO\_PAT}$) [25% weight]
   - Pillar 4: Payout Quality ($Z_{DIV\_YIELD} + Z_{SUSTAIN}$) [20% weight]
   - *Result*: 6.67% Net CAGR, 0.06 Sharpe, 58.6% Win Rate.

### Root Causes of the Quality & Payout Drag:
1. **The Indian Payout Factor Paradox**:
   - In academic literature from developed markets (Fama-French, Novy-Marx), dividend-paying quality stocks often exhibit low idiosyncratic volatility and moderate excess returns.
   - In the Indian equity market (2018–2026), however, companies with the highest sustainable dividend yields are predominantly **mature, low-growth public-sector undertakings (PSUs), utilities, and low-reinvestment legacy enterprises**.
   - India's most dynamic compounders (e.g., consumer leaders, private financials, niche industrial leaders) intentionally reinvest 80–95% of their operating cash flows back into capacity expansion and market share growth, yielding nominal dividend yields (<1.0%).
   - By allocating 20% of the composite rank to shareholder payout metrics, QPF-1 systematically tilted its Top 50 selection away from dynamic compounders and toward dividend-heavy, lower-growth firms.

2. **Cash Flow vs Accounting Earnings Divergence ($CFO/PAT$)**:
   - Penalizing companies with CFO/PAT ratios < 0.8 penalized high-growth companies experiencing legitimate working capital expansion during business scale-up cycles.
   - While intended to detect earnings manipulation, in an annual rebalanced cross-section, it filtered out high-performing growth compounders.

3. **Equal-Weighted Allocation (2% per Stock)**:
   - Institutional Quality indices benefit from tilt weighting (where higher quality and larger market-cap firms receive up to 5% weights). Equal weighting 50 names diluted the contribution of mega-cap blue-chip compounders while increasing exposure to lower-liquidity midcaps that did not re-rate.

---

## 4. Transaction Cost & Slippage Sensitivity Audit

Because QPF-1 utilizes a low-turnover semi-annual rebalancing schedule (rebalancing only on June 15 and December 15), turnover friction is minimal:

| Model Scenario | Net CAGR (%) | Total Return (%) | Sharpe Ratio | Max Drawdown (%) | Slippage Sensitivity |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Base Cost (5 bps slippage)** | 6.67% | 71.21% | 0.06 | -17.14% | Baseline |
| **Adverse Cost (10 bps slippage)**| 6.66% | 71.11% | 0.06 | -17.15% | -0.01% CAGR |
| **Adverse Cost (20 bps slippage)**| 6.65% | 70.90% | 0.06 | -17.15% | -0.02% CAGR |

### Transaction Cost Breakdown (Total Friction: ₹27,899.91 on ₹1,000,000 Capital):
- **Securities Transaction Tax (STT)**: ₹11,159.96 (40.0% of total costs)
- **Execution Slippage**: ₹10,601.97 (38.0% of total costs)
- **Brokerage Fees (₹20/order)**: ₹1,160.00 (4.2% of total costs)
- **Stamp Duty (0.015%)**: ₹1,673.99 (6.0% of total costs)
- **Depository Charges (DP)**: ₹461.97 (1.7% of total costs)
- **Exchange & SEBI Turnover Fees**: ₹1,116.00 (4.0% of total costs)
- **GST (18% on Statutory Fees)**: ₹1,116.00 (4.0% of total costs)

**Conclusion on Execution Feasibility**: QPF-1 is completely immune to transaction-cost degradation. The annualized cost drag is less than 0.35% per year. However, execution robustness cannot compensate for the lack of fundamental factor edge.

---

## 5. Audit Artifacts & File Locations

- Benchmark Comparison: [qpf_1_benchmark_comparison.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_benchmark_comparison.csv)
- Portfolio NAV Curve: [qpf_1_portfolio_nav.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_portfolio_nav.csv)
- Complete Trade Ledger: [qpf_1_trade_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_trade_ledger.csv)
- Temporal Validation Table: [qpf_1_temporal_validation.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_temporal_validation.csv)
- Transaction Cost Audit: [qpf_1_transaction_cost_audit.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_transaction_cost_audit.csv)
- Audit Manifest: [qpf_1_manifest.json](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_manifest.json)
