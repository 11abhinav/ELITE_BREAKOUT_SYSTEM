# SFP-1 — RISK, REGIME, AND TEMPORAL VALIDATION ANALYSIS

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Evaluation Date**: 2026-10-13  
**Data Horizon**: 2018-07-02 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalance Cycles)  

---

## 1. Executive Summary

This report evaluates the temporal consistency, risk characteristics, and macroeconomic regime sensitivity of **SFP-1 (Size Premium with Financial Survival Filters)**.

The strategy was evaluated across three non-overlapping historical windows, including the pre-registered **Locked Holdout Window (2024–2026)**. The empirical evidence demonstrates that SFP-1 failed to generate positive excess returns over the broad market in **every single temporal window**, both before and after adjusting for small-cap survivorship bias.

---

## 2. Temporal Window Audit

### Pre-Registered Chronological Windows

| Temporal Window | Calendar Period | Sessions | Raw SFP-1 CAGR (%) | Survivor-Adj CAGR (%) | Nifty 500 TRI CAGR (%) | Raw Excess vs Market | Adj Excess vs Market | Sharpe Ratio | Max Drawdown (%) | Gate Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Development** | 2018-07-02 to 2020-12-31 | 614 | **5.73%** | **0.79%** | 9.84% | -4.11% | -9.05% | -0.01 | -9.88% | **FAIL** |
| **Validation** | 2021-01-01 to 2023-12-31 | 739 | **16.78%** | **11.84%** | 18.57% | -1.79% | -6.73% | 1.39 | -7.07% | **FAIL** |
| **Locked Holdout** | 2024-01-01 to 2026-10-12 | 681 | **1.87%** | **-3.07%** | 4.29% | -2.42% | -7.36% | -0.89 | -3.12% | **FAIL** |
| **Full History** | 2018-07-02 to 2026-10-12 | 2,045 | **8.37%** | **3.43%** | 11.13% | -2.76% | -7.70% | 0.40 | -9.88% | **FAIL** |

*Note: All returns are net of standard Indian delivery friction (Brokerage ₹20, STT 0.1%, Stamp Duty 0.015%, Exchange fees, GST, DP charges) and 5 bps slippage per side.*

### Key Observations:
1. **Development Window (2018–2020)**:
   - SFP-1 delivered **5.73% raw CAGR** (0.79% survivorship-adjusted) vs **9.84%** for Nifty 500 TRI.
   - The period was marked by the severe 2018–2019 NBFC/IL&FS liquidity crunch, which disproportionately penalized small-cap balance sheets.
2. **Validation Window (2021–2023)**:
   - SFP-1 delivered strong absolute growth (**16.78% raw CAGR**, 11.84% adjusted) with an impressive Sharpe ratio of **1.39**.
   - However, even during this massive post-COVID bull market, the Nifty 500 TRI generated **18.57% CAGR**, resulting in negative excess returns (-1.79% raw, -6.73% adjusted).
3. **Locked Holdout Window (2024–2026)**:
   - SFP-1 delivered a weak raw CAGR of **1.87%** and a **negative survivorship-adjusted CAGR of -3.07%** vs **+4.29%** for Nifty 500 TRI.
   - Holdout Sharpe ratio was **-0.89**.

---

## 3. Market Regime Sensitivity

### A. Small-Cap Bear Regime (2018 – 2019)
* **Market Dynamics**: Credit squeeze, flight to quality, mid/small-caps collapsed while large-caps (Reliance, TCS, HDFC Bank) held up the index.
* **SFP-1 Response**: Survival filters prevented catastrophic liquidation (drawdown contained to -9.88%), but capital failed to compound.

### B. High-Beta Bull Regime (2020 – 2023)
* **Market Dynamics**: Global liquidity expansion, domestic retail inflows into small-caps.
* **SFP-1 Response**: Generated solid returns (+16.78% CAGR), but equal-weighted small-caps with turnover $\ge ₹5\text{ Cr}$ still could not decisively outperform mega-cap industrial leaders.

### C. Late-Cycle Consolidation (2024 – 2026)
* **Market Dynamics**: Regulatory scrutiny on small-cap froth, valuation compression.
* **SFP-1 Response**: Flat performance (+1.87% raw), turning deeply negative (-3.07%) after adjusting for survivorship attrition.

---

## 4. Trade Execution & Quality Diagnostics

* **Total Round-Trip Positions**: 77 completed trades.
* **Win Rate**: **59.7%** (46 wins, 31 losses).
* **Profit Factor**: **4.89** (Gross Profit ₹78,410 vs Gross Loss ₹16,040).
* **Average Holding Duration**: 1.0 years (approx. 2 semiannual cycles).
* **Turnover & Costs**: Total friction across 8.3 years was ₹7,215 on ₹10L NAV (approx. 8 bps annualized drag).

* **Core Insight**: The trade quality was sound and transaction costs were negligible. The failure of the strategy is structural: **the Indian small-cap size factor does not generate structural positive alpha over the Nifty 500 after accounting for survivorship attrition.**
