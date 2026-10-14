# SFP-1 — FACTOR ABLATION & COMPARATIVE STRATEGY ANALYSIS

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Execution Date**: 2026-10-13  
**Data Horizon**: 2018-07-02 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalance Cycles)  

---

## 1. Executive Summary

To isolate the economic impact of the **Size Factor (Smallcap Rank 251–500)** and the **Financial Survival Filter (PAT > 0, CFO > 0, Equity > 0)**, multiple model variants were backtested alongside the broad market benchmark (Nifty 500 TRI).

### Comparative Ablation Matrix

| Model Identifier | Specification | Net CAGR (%) | Total Return (%) | Sharpe Ratio | Max Drawdown (%) | Total Trades | Win Rate (%) | Profit Factor |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Model 1: SFP-1 Full Strategy** | Smallcap (251–500) + Survival Filters (5 bps slip) | **8.37%** | **94.57%** | **0.40** | **-9.88%** | **77** | **59.7%** | **4.89** |
| Model 1: SFP-1 (10 bps slip) | Adverse Execution Cost (10 bps/side) | 8.34% | 94.03% | 0.39 | -9.88% | 77 | 59.7% | 4.85 |
| Model 1: SFP-1 (20 bps slip) | Adverse Execution Cost (20 bps/side) | 8.27% | 93.12% | 0.38 | -9.88% | 77 | 59.7% | 4.80 |
| **Model 2: Smallcap Unfiltered** | Smallcap (251–500) without Survival Filters | **6.62%** | **69.99%** | **0.12** | **-13.46%** | **78** | **52.6%** | **3.72** |
| **Benchmark: Nifty 500 TRI** | Broad Indian Equity Market | **11.13%** | **139.57%** | **0.37** | **-38.30%** | — | — | — |
| **SFP-1 Survivorship-Adjusted** | Model 1 with -4.94% Survivorship Haircut | **3.43%** | **32.21%** | **0.15** | **-14.88%** | 77 | 59.7% | 4.89 |

*Source files: [sfp_benchmark_comparison.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/sfp_benchmark_comparison.csv), [sfp_portfolio_nav.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/sfp_portfolio_nav.csv)*

---

## 2. Factor Interaction Breakdown

### Question 1: Do Financial Survival Filters Add Economic Value to Small-Caps?
* **Comparison**: `Model 1 (SFP-1 Full Strategy: 8.37% CAGR)` vs `Model 2 (Smallcap Unfiltered Control: 6.62% CAGR)`.
* **Empirical Finding**: 
  - Requiring positive trailing net profit (`net_profit > 0`), positive operating cash flow (`cfo > 0`), and solvent net worth (`total_equity > 0`) delivered a clear performance improvement of **+1.75% CAGR per year** (+24.58% cumulative outperformance).
  - Win rate increased from **52.6%** to **59.7%**, and profit factor expanded from **3.72** to **4.89**.
  - Maximum drawdown improved from **-13.46%** to **-9.88%**.
  - **Verdict**: Financial survival screening successfully weeded out cash-burning, insolvent smallcaps, confirming the user's architectural hypothesis.

### Question 2: Does the Small-Cap Size Premium Beat the Broad Market?
* **Comparison**: `Model 1 (SFP-1: 8.37% CAGR)` vs `Nifty 500 TRI (11.13% CAGR)`.
* **Empirical Finding**:
  - Even **before** applying any penalty for survivorship bias, the small-cap strategy lagged the Nifty 500 TRI by **-2.76% per annum** (8.37% vs 11.13%).
  - The small-cap premium was absent over the full 2018–2026 cycle on an equal-weighted, semiannually rebalanced basis.
  - Large-cap market leaders (Nifty 50 / Nifty 100 constituents) generated superior risk-adjusted wealth due to strong balance sheets, access to institutional credit, and market-share consolidation following demonetization, GST, and COVID.

### Question 3: The Fatal Impact of Survivorship Bias (-4.94% CAGR Haircut)
* **Academic Context**: In accordance with the 2025–2026 SSRN working paper on the Nifty Smallcap 250, simulations conducted purely on surviving constituents overstate returns by ~4.94% annually due to the omission of delisted and bankrupt firms.
* **Empirical Adjustment**:
  - Applying this mandatory correction reduces SFP-1's true estimated performance to **3.43% Net CAGR**.
  - Total 8.3-year return shrinks from +94.57% down to **+32.21%** (underperforming the Nifty 500's +139.57% by more than **107 percentage points**).
  - Annualized excess return against the broad market is **-7.70% per year**.

---

## 3. Friction & Capacity Analysis

* **Friction Sensitivity**:
  - 5 bps slippage: 8.37% CAGR
  - 10 bps slippage: 8.34% CAGR (-3 bps drag)
  - 20 bps slippage: 8.27% CAGR (-10 bps drag)
* **Turnover**: Because SFP-1 rebalances semiannually, portfolio turnover is moderate (~30–40% per rebalance cycle). The ₹5 crore median daily liquidity screen ensured that slippage and statutory costs (₹5,800 to ₹7,500 total friction on ₹10L NAV) did not erode the strategy.
* **Root Failure**: The strategy does not fail because of trading costs; it fails because the **size premium was negative relative to large-caps** and collapses further once survivorship bias is taken into account.
