# QPF-1: QUALITY & PAYOUT FACTOR
## STRATEGY THEORY AND FROZEN GOVERNANCE RULES

**Strategy Identifier**: `QPF_1`  
**Strategy Class**: Cross-Sectional Factor Portfolio (Quality & Shareholder Payout)  
**Research Phase**: Exploratory Factor Backtest & Institutional Benchmark Falsification  
**Governance Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  

---

## 1. Economic Hypothesis & Academic Foundations

### 1.1 The Quality Factor in Indian Equities (IIM Ahmedabad 2022)
Extensive empirical literature globally (Novy-Marx 2013; Asness, Frazzini & Pedersen 2019) demonstrates that high-quality, profitable firms earn superior risk-adjusted returns compared to junk equities. In the Indian market, this phenomenon was rigorously documented by the **Indian Institute of Management Ahmedabad (IIM-A, 2022)**, which evaluated factor premiums across two decades of NSE/BSE trading.

The IIM-A research established three vital structural pillars:
1. **Profitability Premium**: Companies with persistently high Return on Equity (ROE) and Return on Capital Employed (ROCE) compound economic book value faster than average market firms.
2. **Accrual and Cash-Flow Governance**: In emerging markets, reported net income can be distorted by working capital build-up. Companies demonstrating high cash conversion ($\sum \text{CFO} / \sum \text{PAT} \ge 0.8$) exhibit far lower default and earnings restatement rates.
3. **Sustainable Shareholder Payouts**: Regular, sustainable dividends and distributions serve as a powerful governance signal. Companies returning cash to shareholders while maintaining low debt achieve significantly lower volatility and superior downside resilience.

### 1.2 The Institutional Quality Benchmark: Nifty500 Quality 50
To ensure the custom strategy does not merely invent a subjective scoring metric, QPF-1 is benchmarked against the official **Nifty500 Quality 50 Index (TRI)**. The Nifty500 Quality 50 methodology selects 50 companies based on a composite quality score derived from:
- **Return on Equity (ROE)**: Average of latest 3 to 5 financial years.
- **Financial Leverage (Debt-to-Equity)**: Lower is better (excluding financial services).
- **Earnings Growth Variability**: 5-year standard deviation of YoY EPS growth.

### 1.3 The QPF-1 Research Question
> **Does an all-regime, point-in-time composite factor portfolio of the Top 50 Indian companies—ranked on multi-year profitability, decoupled balance-sheet leverage, cash-flow reliability, and sustainable shareholder payouts—generate statistically significant and economically meaningful excess returns over both the broad market (Nifty 500 TRI) and the official institutional quality factor (Nifty500 Quality 50 TRI) after full Indian execution costs?**

---

## 2. Frozen Strategy Specification & Implementation Invariants

Every parameter and trading convention is frozen prior to backtest evaluation:

| Parameter | Frozen Rule | Rationale & Invariant |
| :--- | :--- | :--- |
| **Strategy ID** | `QPF_1` | Immutable namespace. |
| **Pillar 1: Profitability (35%)** | $$\text{Score}_{\text{profit}} = \overline{\text{ROE}}_{5\text{Y}} - 0.5 \times \sigma(\text{ROE}_{5\text{Y}})$$ | Rewards high, stable Return on Equity over 5 years. Penalizes single-year volatile spikes. |
| **Pillar 2: Balance-Sheet (25%)** | Non-BFSI: Low Debt/Equity ($D/E \le 1.0$).<br>BFSI: Return on Assets ($\text{ROA} \ge 1.0\%$) & Net Margin. | Decouples banks and NBFCs from industrial leverage ratios to prevent indiscriminate exclusion. |
| **Pillar 3: Cash Flow (25%)** | 5-Year Cumulative CFO / PAT Ratio ($\sum \text{CFO} / \sum \text{PAT}$) | Rewards real cash generation. Penalizes aggressive accounting accruals. |
| **Pillar 4: Payout (15%)** | Sustainable dividend distribution yield & payout | Rewards distributions funded by cash flow; penalizes debt-funded payouts. |
| **Universe** | NSE Cash Equities (~800 symbols) | Must have verified Upstox 1D daily price series and $\ge 3$ years of audited annual filings. |
| **Rebalance Cadence** | Semi-Annual (June 15 and December 15) | Low turnover. Aligns with statutory filing publication dates (May 30 deadline for March FY). |
| **Portfolio Architecture** | Fixed Top 50 Holdings | Equal weighting (2.0% target slot size). |
| **Sector Concentration Cap** | Max 25.0% per sector (Max 12 positions) | Prevents macro sector clustering. |
| **Execution Timing** | Rebalance Day Open | Fills modeled at session Open on rebalance date. |
| **Holding Period** | Semi-annual reconstitution (held until dropped) | Rebalanced every 6 months. Unchanged names incur zero turnover. |
| **Transaction Fees** | Full Indian statutory delivery schedule | Flat ₹20 Brokerage, 0.1% STT on sell, 0.015% Stamp Duty on buy, ₹15.93 DP charge, GST, SEBI, 5 bps slippage per side. |
| **Benchmarks** | 1. Nifty 500 TRI<br>2. Nifty500 Quality 50 TRI | Dual benchmark challenge: broad market and official quality factor. |

---

## 3. Pre-Registered Acceptance Gates (A through G)

1. **Gate A: Economic Hurdle over Broad Market**: Net CAGR $\ge \text{Nifty 500 TRI} + 2.0\%$ p.a. over evaluation window.
2. **Gate B: Factor Differentiation over Quality 50**: Net CAGR $\ge \text{Nifty500 Quality 50 TRI}$ with positive information ratio.
3. **Gate C: Risk Profile**: Sharpe ratio $>$ Benchmark Sharpe; Maximum Drawdown $\le \text{Benchmark Max DD} + 5.0\%$.
4. **Gate D: Temporal Consistency**: Positive excess returns over Nifty 500 TRI in $\ge 3 \text{ of } 4$ independent evaluation windows.
5. **Gate E: Untouched Holdout**: Positive net excess return in the locked 2024–2026 holdout.
6. **Gate F: Cost Robustness**: Retains positive net alpha under 10 bps and 20 bps adverse slippage.
7. **Gate G: Data Provenance & Lineage**: 100% point-in-time filing lineage without restatement leakage or prohibited data sources.
