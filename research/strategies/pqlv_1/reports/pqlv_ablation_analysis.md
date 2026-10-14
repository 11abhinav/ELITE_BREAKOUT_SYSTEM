# PQLV-1 — FACTOR ABLATION & COMPARATIVE STRATEGY ANALYSIS

**Strategy Identifier**: `PQLV-1`  
**Research Namespace**: `research/strategies/pqlv_1/`  
**Audit Timestamp**: 2026-10-13  
**Data Horizon**: 2018-06-29 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalances)  

---

## 1. Executive Summary of Ablation Results

To dissect the true causal economic drivers behind **PQLV-1 (Profitable Quality, Low-Volatility Leaders)**, four distinct models and two official benchmark series were executed simultaneously over identical calendar dates, trading calendars, friction models, and point-in-time constraints.

### Ablation Comparison Matrix

| Model Identifier | Strategy Specification | Net CAGR (%) | Total Return (%) | Annualized Sharpe | Max Drawdown (%) | Round-Trip Trades | Win Rate (%) | Profit Factor |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Model D (Primary)** | **PQLV-1 Full Strategy (Quality + Low-Vol)** | **2.32%** | **20.89%** | **-0.91** | **-9.71%** | **36** | **63.9%** | **4.17** |
| Model D (10 bps) | PQLV-1 Adverse Slippage (10 bps/side) | 2.30% | 20.73% | -0.91 | -9.74% | 36 | 63.9% | 4.17 |
| Model D (20 bps) | PQLV-1 Adverse Slippage (20 bps/side) | 2.27% | 20.42% | -0.92 | -9.82% | 36 | 63.9% | 4.17 |
| **Model A** | Official Nifty100 Low Vol 30 Proxy | 7.89% | 87.67% | 0.16 | -29.49% | 134 | 64.9% | 3.62 |
| **Model B** | Quality-Only Control (No Volatility Filter) | 4.41% | 42.96% | -0.28 | -13.59% | 30 | 50.0% | 4.65 |
| **Model C** | Low-Vol-Only Control (No Quality Gates) | 2.13% | 19.13% | -0.39 | -30.66% | 46 | 58.7% | 2.86 |
| **Benchmark 1** | Nifty 500 TRI (Broad Indian Market) | 12.34% | 138.18% | 0.34 | -38.30% | — | — | — |
| **Benchmark 2** | Nifty500 Quality 50 Proxy (Official Quality) | 7.33% | 80.17% | 0.13 | -17.86% | 28 | 67.9% | 7.36 |

---

## 2. Factor Interaction Breakdown

### Question 1: Does Quality Add Economic Value to Low Volatility?
* **Comparison**: `Model D (PQLV-1: Quality + Low-Vol, 2.32% CAGR)` vs `Model C (Low-Vol-Only: 2.13% CAGR)`.
* **Empirical Finding**: 
  - Applying strict quality gates (5 consecutive profitable years, positive 3Y revenue CAGR, ROCE $\ge 15\%$, CFO/PAT $\ge 0.8$) to low-volatility stocks yielded a negligible CAGR boost of **+0.19% per annum** (+1.76% cumulative return over 8.3 years).
  - However, the quality filter dramatically compressed drawdown risk: Maximum Drawdown dropped from **-30.66%** in Model C down to **-9.71%** in Model D.
  - The quality filter successfully weeded out debt-laden utilities and stagnant legacy businesses that exhibited low historical price volatility but suffered severe balance-sheet drawdowns.
  - **Verdict**: Quality improves capital preservation when attached to low volatility, but fails to generate meaningful growth.

### Question 2: Does Low Volatility Add Economic Value to Quality?
* **Comparison**: `Model D (PQLV-1: Quality + Low-Vol, 2.32% CAGR)` vs `Model B (Quality-Only: 4.41% CAGR)`.
* **Empirical Finding**:
  - Selecting the least volatile stocks from the quality pool **cut the portfolio's net CAGR in half**, reducing annualized returns from **4.41%** down to **2.32%** (a severe drag of **-209 bps per annum**).
  - In the Indian equity market (2018–2026), premier quality companies with high ROCE and high reinvestment rates (such as high-growth technology, specialty chemicals, and discretionary consumer compounders) naturally exhibited moderate price volatility ($\sigma \approx 25\%\text{--}35\%$).
  - Forcing the selector to choose the lowest 252-day realized volatility systematically dumped the highest-performing compounders and concentrated the portfolio into slow-growth, ultra-defensive consumer staples (e.g., Colgate-Palmolive, Marico, Dabur, ITC, Hindustan Unilever).
  - **Verdict**: Filtering for low volatility **harms** quality investing in India. It systematically strips out high-growth compounders in favor of low-beta stalwarts.

### Question 3: The Fatal Flaw of Cash Drag Under Strict Quality Constraints
* **Root Cause of Underperformance**:
  - The PQLV-1 specification mandated a fixed slot allocation of 1/30 (3.33% of NAV) per qualifying stock and strictly barred reallocating vacant slots to existing holdings ("Unallocated capital remains cash").
  - In 2018, only **3 non-financial stocks** in the entire universe passed all 5 strict quality gates (5-year PAT > 0, 3Y rev CAGR > 0, ROCE $\ge 15\%$, CFO/PAT $\ge 0.8$).
  - As a result, **90% of the portfolio sat in 0% yielding cash** during 2018!
  - In 2019, only **10 stocks** qualified, leaving **66.7% of the portfolio in uninvested cash**.
  - During the historic post-March 2020 Indian equity bull run, having 50% to 70% of the fund trapped in uninvested cash fundamentally handcuffed portfolio compounding, resulting in a microscopic 2.32% CAGR while the Nifty 500 surged at 12.34% CAGR.

---

## 3. Comparison with Official Benchmarks

### PQLV-1 vs Nifty 500 TRI (Broad Market)
* **Net CAGR Gap**: PQLV-1 generated **2.32%** vs Nifty 500 TRI's **12.34%** (underperformance of **-10.02% per year**).
* **Cumulative Return**: PQLV-1 generated **+20.89%** vs Nifty 500 TRI's **+138.18%**.
* **Risk Comparison**: PQLV-1 achieved superior drawdown protection (-9.71% vs -38.30%), but at the unacceptable cost of surrendering 85% of total wealth creation.

### PQLV-1 vs Official Nifty100 Low Vol 30 Proxy (Model A)
* **Net CAGR Gap**: PQLV-1 generated **2.32%** vs Official Low Vol's **7.89%** (underperformance of **-5.57% per year**).
* **Why Official Low Vol Won**:
  - The official NSE methodology stays **100% invested across 30 stocks** at all times by drawing from the Nifty 100 without restrictive accounting gates.
  - The official methodology utilizes inverse-volatility weighting across all 30 slots rather than leaving 20+ slots empty as cash.
  - By avoiding cash drag, the official index captured the broader market upside much more effectively.

---

## 4. Key Takeaways from Ablation

1. **Negative Factor Synergy**: In India, combining strict multi-year accounting quality with trailing 1-year low volatility creates a structural mismatch. The quality pool is constrained, and ranking by lowest volatility extracts the least dynamic subset of that pool.
2. **Fatal Portfolio Inflexibility**: Enforcing 30 equal slots without variable slot sizing or cash deployment guarantees catastrophic cash drag whenever macroeconomic cycles reduce the number of qualifying firms.
3. **Severe Factor Underperformance**: Both standalone low volatility (Model C: 2.13%) and combined quality low volatility (Model D: 2.32%) severely trailed the unconstrained market (12.34%), confirming prior academic literature that the low-volatility anomaly in India is either weak or counter-cyclical.
