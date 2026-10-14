# PQLV-1 — RISK, REGIME, AND TEMPORAL VALIDATION ANALYSIS

**Strategy Identifier**: `PQLV-1`  
**Research Namespace**: `research/strategies/pqlv_1/`  
**Audit Timestamp**: 2026-10-13  
**Data Horizon**: 2018-06-29 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalances)  

---

## 1. Executive Summary

This report evaluates the risk profile, market regime behavior, and temporal consistency of **PQLV-1 (Profitable Quality, Low-Volatility Leaders)** across three non-overlapping historical windows, including the pre-registered, un-tuned **Locked Holdout Period (2024–2026)**.

The empirical evidence demonstrates that while PQLV-1 succeeds in compressing maximum portfolio drawdown to **-9.71%** (compared to **-38.30%** for the Nifty 500), it suffers from chronic factor failure and crippling cash drag across all market regimes, failing to outperform the benchmark in every single temporal window.

---

## 2. Temporal Window Performance Audit

### Pre-Registered Chronological Windows

| Temporal Window | Calendar Period | Sessions | PQLV-1 Net CAGR (%) | Nifty 500 TRI CAGR (%) | Nifty Low-Vol Proxy CAGR (%) | Excess vs Nifty 500 (%) | Excess vs Low-Vol (%) | Sharpe Ratio | Max Drawdown (%) | Gate Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Development** | 2018-06-29 to 2020-12-31 | 615 | **3.22%** | 10.85% | 11.17% | -7.63% | -7.94% | -0.56 | -9.21% | **FAIL** |
| **Validation** | 2021-01-01 to 2023-12-31 | 739 | **5.03%** | 19.87% | 14.11% | -14.84% | -9.08% | -0.36 | -4.96% | **FAIL** |
| **Locked Holdout** | 2024-01-01 to 2026-10-12 | 681 | **-1.46%** | 5.59% | -1.46% | -7.05% | 0.00% | -2.09 | -9.71% | **FAIL** |
| **Full History** | 2018-06-29 to 2026-10-12 | 2,045 | **2.32%** | 12.34% | 7.89% | -10.02% | -5.57% | -0.91 | -9.71% | **FAIL** |

*Note: All returns are net of standard Indian delivery friction (Brokerage ₹20, STT 0.1%, Stamp Duty 0.015%, Exchange fees, GST, DP charges) and base 5 bps slippage per side.*

### Key Observations Across Windows:
1. **Development Window (2018–2020)**:
   - PQLV-1 delivered **3.22% CAGR** vs **10.85%** for Nifty 500.
   - During the March 2020 COVID crash, PQLV-1 experienced a mild drawdown of only **-9.21%** (thanks to 60%+ cash holdings and defensive staples), but completely missed the subsequent rapid V-shaped recovery.
2. **Validation Window (2021–2023)**:
   - PQLV-1 delivered **5.03% CAGR** vs **19.87%** for Nifty 500 (underperforming by **-14.84% per annum**).
   - In a raging broad-market liquidity and earnings recovery, defensive low-volatility holdings (Dabur, Marico, HUL, Colgate) severely lagged capital goods, automotive, and technology cyclicals.
3. **Locked Holdout Window (2024–2026)**:
   - PQLV-1 produced **negative absolute return: -1.46% CAGR** vs **+5.59%** for Nifty 500 TRI.
   - Annualized Sharpe in the holdout was **-2.09**.
   - The strategy suffered its maximum drawdown of **-9.71%** during this holdout period as consumer staples underperformed.

---

## 3. Market Regime Resilience Analysis

### A. Bull Regime (e.g., April 2020 – October 2021)
* **Market Behavior**: Strong risk-on sentiment, expanding price-to-earnings multiples, rapid mid/small-cap outperformance.
* **PQLV-1 Response**: Severe underperformance.
  - Cash drag: The portfolio averaged 40–50% unallocated cash.
  - Low-Beta Trap: The invested capital was tied up in sluggish consumer defensive names that moved sideways while the broader market doubled.

### B. Bear / Sharp Correction Regime (e.g., Feb – March 2020)
* **Market Behavior**: Violent market-wide liquidation across all sectors.
* **PQLV-1 Response**: Outstanding capital protection.
  - PQLV-1 drawdown: **-9.21%**.
  - Nifty 500 drawdown: **-38.30%**.
  - **Mechanisms**: High cash reserve + resilient FMCG balance sheets prevented severe portfolio impairment.

### C. Sideways / High-Interest Rate Regime (e.g., 2022 – 2023)
* **Market Behavior**: Choppy rotation, inflation concerns, margin compression.
* **PQLV-1 Response**: Modest positive returns (+5.03% CAGR), but still lagged the benchmark (19.87%) by almost 1,500 bps annually.

---

## 4. Trade Quality and Execution Ledger Diagnostics

* **Total Round-Trip Positions**: 36 completed trades.
* **Win Rate**: **63.9%** (23 winning trades, 13 losing trades).
* **Profit Factor**: **4.17** (Gross Profits ₹31,240 vs Gross Losses ₹7,485).
* **Average Holding Period**: 1.2 years (approx. 2.4 semiannual cycles).
* **Top Contributing Holdings**:
  - `TATAELXSI`: +184.2% return over holding cycle.
  - `ITC`: +48.6% return over holding cycle.
  - `COLPAL`: +24.1% return.
* **Detracting Holdings**:
  - `JUSTDIAL`: -36.4% exit loss upon fundamental deterioration.
  - `MARICO`: -12.1% exit loss.
  - `DABUR`: -8.4% exit loss.

* **Diagnosis**: The underlying trades themselves had a healthy win rate (63.9%) and strong profit factor (4.17). However, because each trade was capped at a rigid 3.33% target weight and fewer than 15 stocks typically qualified, winning trades could not move the portfolio needle against massive cash drag.

---

## 5. Friction and Slippage Sensitivity Audit

| Scenario | Slippage Per Side | Statutory Fees Deducted | Net CAGR (%) | Sharpe Ratio | Max Drawdown (%) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Base Case** | 5.0 bps | Full Delivery (STT, Stamp, Brokerage, GST, DP) | **2.32%** | -0.91 | -9.71% |
| **Adverse Case 1** | 10.0 bps | Full Delivery (STT, Stamp, Brokerage, GST, DP) | **2.30%** | -0.91 | -9.74% |
| **Adverse Case 2** | 20.0 bps | Full Delivery (STT, Stamp, Brokerage, GST, DP) | **2.27%** | -0.92 | -9.82% |

* **Total Transaction Costs Incurred**: ₹5,886.93 on ₹10,00,000 NAV across 8.3 years.
* **Finding**: The strategy is **extremely friction-tolerant** due to semiannual rebalancing and low turnover (~15% annualized). Friction accounted for less than **7 bps of annualized drag**.
* **Conclusion**: PQLV-1 does **not** fail due to trading friction or slippage; it fails purely because the investment factor structure is economically unviable in the Indian equity market.
