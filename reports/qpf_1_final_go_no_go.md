# QPF-1 — Final Go / No-Go Governance & Certification Report

**Strategy ID**: `QPF_1`  
**Strategy Name**: Quality & Payout Factor (Point-in-Time Cross-Sectional Semi-Annual Rebalance)  
**Authors / Research Engine**: Elite Breakout System Quantitative Research  
**Date of Evaluation**: 2026-10-13  
**Historical Evaluation Span**: 2018-06-15 to 2026-10-12 (8.33 Years, 2,045 Trading Sessions)  
**Final Governance Verdict**: **REJECT FOR PRODUCTION (RESEARCH ARCHIVED)**

---

## 1. Executive Summary & Final Verdict

Following the observed failures of **RSMR-1** (Momentum Rotation) and **ESD-1** (Earnings Surprise Drift), **QPF-1** was formulated as a structural factor candidate designed to extract equity risk premia from durable profitability, balance-sheet conservatism, earnings stability, and sustainable shareholder distributions.

The empirical backtest has concluded across 8.33 years of point-in-time annual filing history and 2,045 trading sessions.

### Definitive Verdict: **REJECT FOR ALERT STRATEGY & PRODUCTION COMMITMENT**
1. **Underperformed the Broad Market**: QPF-1 delivered a net CAGR of **6.67%**, trailing the **Nifty 500 TRI** (**12.03%**) by **-5.36% annualized** over the full cycle.
2. **Underperformed the Official Quality Benchmark**: QPF-1 underperformed the simpler **Nifty500 Quality 50 Proxy** (**7.33%**) by **-0.66% annualized**, proving that adding shareholder payout and cash-flow accrual screens to the official quality formula created an active drag rather than an advantage.
3. **Failed Every Temporal Validation Window**: In all three independent, non-overlapping evaluation windows (Development 2018–2020, Validation 2021–2023, and Locked Holdout 2024–2026), QPF-1 produced **negative excess returns** against the broad market.
4. **Data Governance Barrier**: Under project rules in `AGENTS.md`, `pit_fundamentals_v1.parquet` is derived from Screener audited annual reports, which carries `FORENSIC_REFERENCE_ONLY` status and is legally barred from live alert generation.

---

## 2. Pre-Registered Acceptance Criteria Audit

Before reviewing results, 7 stringent acceptance gates were pre-registered. Below is the gate-by-gate audit:

| Gate # | Pre-Registered Acceptance Gate | Threshold / Expectation | Measured Result | Verdict |
| :---: | :--- | :--- | :---: | :---: |
| **Gate 1** | **Net Annualized Excess Return** | $\ge +2.00\%$ over Nifty 500 TRI | **-5.36%** (6.67% vs 12.03%) | **FAILED** |
| **Gate 2** | **Temporal Consistency** | Positive excess in $\ge 3$ of 4 windows | **0 of 3 windows** positive (-4.51%, -7.73%, -3.60%) | **FAILED** |
| **Gate 3** | **Risk-Adjusted Efficiency** | Sharpe ratio above broad market (0.32) | **0.06** (Sharpe vs 0.32 benchmark) | **FAILED** |
| **Gate 4** | **Downside Capital Preservation** | Max DD no more than 5% worse than market | **-17.14%** vs -38.30% (Superior cushion) | **PASSED** |
| **Gate 5** | **Untouched Holdout Performance** | Positive net excess in 2024–2026 holdout | **-3.60%** vs N500; **-0.32%** vs Q50 | **FAILED** |
| **Gate 6** | **Benchmark Distinctiveness** | Demonstrable benefit over Nifty500 Quality 50 | **-0.66% CAGR lag**; lower win rate (58.6% vs 67.9%) | **FAILED** |
| **Gate 7** | **Data Integrity & Point-in-Time** | Verified PIT timestamps, zero look-ahead | 75-day filing lag respected; Screener data status | **CONDITIONAL** |

**Summary**: QPF-1 failed **5 out of 6 quantitative gates**, passing only the drawdown dampening test.

---

## 3. Comprehensive Performance Benchmark Matrix

| Dimension | QPF-1 (Frozen Base) | Nifty500 Quality 50 Proxy | Nifty 500 TRI | Delta vs N500 | Delta vs Q50 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Initial Capital** | ₹1,000,000 | ₹1,000,000 | ₹1,000,000 | — | — |
| **Final Portfolio NAV** | ₹1,712,100 | ₹1,801,700 | ₹2,335,600 | -₹623,500 | -₹89,600 |
| **Net CAGR (%)** | **6.67%** | **7.33%** | **12.03%** | **-5.36%** | **-0.66%** |
| **Total Cumulative Return** | **+71.21%** | **+80.17%** | **+133.56%** | **-62.35%** | **-8.96%** |
| **Sharpe Ratio (Rf=6.5%)** | **0.06** | **0.13** | **0.32** | **-0.26** | **-0.07** |
| **Max Drawdown (%)** | **-17.14%** | **-17.86%** | **-38.30%** | **+21.16%** | **+0.72%** |
| **Closed Portfolio Exits** | 29 | 28 | 0 | — | +1 |
| **Win Rate on Positions** | 58.6% | 67.9% | N/A | — | -9.3% |
| **Profit Factor** | 5.39 | 7.36 | N/A | — | -1.97 |
| **Transaction Friction** | ₹27,899.91 | ₹26,540.10 | ₹0.00 | — | +₹1,359.81 |

---

## 4. Why QPF-1 Failed: Forensic Factor Autopsy

The failure of QPF-1 provides valuable empirical findings regarding factor investing in the Indian market:

### 1. The Payout Factor Acts as a Value Trap in India
- In mature economies with limited capital reinvestment needs, high dividend yield signals financial health and capital discipline.
- In India's fast-growing emerging economy (2018–2026), top-tier wealth creators (e.g. high-growth private banks, consumer compounders, industrial innovators) **retain almost all earnings (85–95%)** to compound capital internally at 20%+ ROCE.
- The stocks offering the highest sustainable dividend yields were **state-owned enterprises (PSUs), traditional utilities, commodity miners, and mature cash cows**.
- By incorporating a 20% weight on Payout (Dividend Yield + Debt-funded Payout penalty), QPF-1 systematically loaded into slower-growing, lower-valuation stocks, transforming a pure quality strategy into an unintended high-dividend value portfolio.

### 2. Severe Bull Market Lag During Market Expansions
- Between 2021 and 2023, the Indian broad market experienced an intense rally driven by capex revivals, manufacturing expansion, and high-beta cyclicals (Nifty 500 TRI returned **19.87% CAGR**).
- High-quality, low-debt, dividend-paying stocks returned only **12.14% CAGR**, creating a massive **-7.73% annual performance deficit**.
- While quality provided protection during the COVID crash in early 2020 (limiting maximum drawdown to -17.1% vs -38.3%), the upside sacrificed during the subsequent bull market was far too severe to justify holding the portfolio.

### 3. Dilution vs. The Official Benchmark
- The **Nifty500 Quality 50** formula uses 3 clean factors: ROE, Debt/Equity, and 5-Year EPS Growth Variability. It achieved 7.33% CAGR.
- QPF-1 added CFO/PAT and Dividend Payout, resulting in 6.67% CAGR.
- Every additional constraint added to the 3-factor model degraded return and win rate without improving risk metrics.

---

## 5. Comparative Strategic Summary: RSMR-1 vs. ESD-1 vs. QPF-1

| Candidate | Investment Hypothesis | Test Outcome | Failure / Limitation Reason |
| :--- | :--- | :--- | :--- |
| **RSMR-1** | Price-trend persistence (Relative Momentum) | **RESEARCH HOLD** | Failed temporal consistency; retrospective 12M lookback; no downside protection in market breaks. |
| **ESD-1** | Post-Earnings-Announcement Drift (Quarterly Event) | **REJECTED** | Negative event drift (-0.35%); historical quarterly filing coverage severely insufficient (only 10 quarters). |
| **QPF-1** | Durable Quality & Sustainable Payout (Annual Fundamental) | **REJECTED** | Structurally lagged Nifty 500 TRI by -5.36% CAGR; lagged Nifty Quality 50 benchmark; payout factor acted as growth drag. |

---

## 6. Research Archival & Repository Integrity

In accordance with system invariants:
1. **Zero Modifications to Production Scanners**: No production scanner files (`app/`, `production/`) have been modified or polluted.
2. **Zero Live / Paper Trade Alerts**: No entries were made to `buy_alerts_journal` or production databases.
3. **Permanent Research Record**: All artifacts, configs, scripts, and logs are preserved in:
   - Configuration: `research/strategies/qpf_1/config/qpf_1_frozen_config.json`
   - Simulation Engine: `research/strategies/qpf_1/src/run_qpf_1_backtest.py`
   - Benchmark Comparison: [qpf_1_benchmark_comparison.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_benchmark_comparison.csv)
   - Portfolio NAV: [qpf_1_portfolio_nav.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_portfolio_nav.csv)
   - Trade Ledger: [qpf_1_trade_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_trade_ledger.csv)
   - Temporal Validation: [qpf_1_temporal_validation.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_temporal_validation.csv)
   - Cost Ledger: [qpf_1_transaction_cost_audit.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_transaction_cost_audit.csv)
   - Audit Manifest: [qpf_1_manifest.json](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qpf_1_manifest.json)
