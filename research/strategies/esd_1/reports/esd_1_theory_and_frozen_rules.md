# ESD-1: POINT-IN-TIME EARNINGS SURPRISE DRIFT (PEAD)
## STRATEGY THEORY AND FROZEN GOVERNANCE RULES

**Strategy Identifier**: `ESD_1`  
**Strategy Class**: Event-Driven Post-Earnings-Announcement Drift (PEAD)  
**Research Phase**: Exploratory Historical Backtest & Hypothesis Falsification  
**Governance Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  

---

## 1. Economic Hypothesis & Theoretical Motivation

### 1.1 The Core Phenomenon: Post-Earnings-Announcement Drift (PEAD)
In efficient financial markets, new information disclosed in corporate earnings releases should be reflected immediately in asset prices. However, extensive empirical literature in emerging and developed markets (Ball & Brown 1968; Bernard & Thomas 1989; Sehgal & Bijoy 2009; NSE/BSE empirical studies 2026) demonstrates that stock prices exhibit **systematic post-announcement drift** following unexpected earnings beats.

In the Indian equity market, PEAD is hypothesized to arise from:
1. **Analyst Anchoring & Institutional Inertia**: Institutional consensus revisions occur gradually as sell-side analysts update forward estimates over multiple trading sessions.
2. **Retail Disposition Effect**: Investors anchor on prior reference prices and prematurely sell winners, creating temporary supply that slows initial price adjustment.
3. **Execution Delay in Cash Equities**: Large institutional funds accumulate illiquid mid-and-small cap names over multi-week volume-weighted average price (VWAP) execution windows rather than aggressive single-session market orders.

### 1.2 The ESD-1 Research Question
> **Does buying companies that report unexpected quarterly EPS improvements ($SUE \ge 1.0$), confirmed by a positive market-relative price reaction on the first complete trading session following publication, generate statistically significant and economically meaningful excess returns over a fixed 63-session (quarterly) holding period after all Indian statutory taxes and transaction costs?**

---

## 2. Frozen Strategy Specification & Implementation Invariants

Every parameter and trading convention is frozen prior to backtest evaluation to prevent retrospective parameter optimization:

| Parameter | Frozen Rule | Rationale & Invariant |
| :--- | :--- | :--- |
| **Strategy ID** | `ESD_1` | Immutable namespace. |
| **Primary Signal** | Standardized Unexpected Earnings (SUE) | Time-series seasonal random walk proxy. |
| **SUE Formula** | $$SUE_q = \frac{\Delta E_q}{\operatorname{SD}(\Delta E_{q-1}, \dots, \Delta E_{q-8})}$$ | Year-over-year EPS improvement standardized by historical volatility of preceding 8 YoY changes. |
| **Quarterly YoY Change** | $$\Delta E_q = E_q - E_{q-4}$$ | Seasonally adjusted EPS change vs identical quarter prior year. |
| **Denominator Lookback** | Exactly 8 prior YoY changes ($\Delta E_{q-1}, \dots, \Delta E_{q-8}$) | Requires at least 12 prior quarterly observations. The current quarter is strictly excluded from its own denominator. |
| **Signal Threshold** | $SUE_q \ge 1.0$ | Requires earnings beat of at least one historical standard deviation. Frozen before run. |
| **Publication Timestamp** | Conservative statutory availability ($T_{\text{avail}}$) | Filings released after 15:30 IST or AMC are marked for next session open. |
| **Response Window ($T_1$)** | First complete trading session post-publication | Initial market reaction window. |
| **Price Response Filter** | $R_{\text{stock}, T_1} - R_{\text{Nifty 500}, T_1} > 0.0\%$ | Stock must demonstrate strictly positive market-adjusted return on day $T_1$. |
| **Decision Cutoff** | Session $T_1$ Close | Zero intraday look-ahead. Signal committed strictly at Close of $T_1$. |
| **Execution Timing** | Session $T_2$ Open | Entry executed at Open of next trading session. |
| **Holding Period** | Exactly 63 complete trading sessions | Approximate one calendar quarter. |
| **Exit Execution** | Session $T_{65}$ Open (session 64 after entry) | Position exited at Open of the 64th session following entry. |
| **Discretionary Overlays** | **NONE** | No trailing stops, no profit targets, no technical moving average filters, no discretionary exits. |
| **Portfolio Architecture** | Max 20 simultaneous positions | Long-only, unleveraged, 100% gross capacity limit. |
| **Target Slot Sizing** | 5.0% target weight per slot | Whole-share execution based on cash and portfolio equity. |
| **Sector Concentration Cap** | Max 25.0% per sector | Prevents macro sector clustering (e.g. IT/Pharma rally concentration). |
| **Cash Management** | Unallocated capital held as cash | Realistic cash drag modeled (0% yield or risk-free rate). |
| **Tie-Breaker Rule** | 1. SUE descending; 2. 20-day median turnover | Deterministic, non-future-looking priority ranking. |
| **Primary Benchmark** | Nifty 500 Total Returns Index (TRI) | Broad-market index including official 1.30% p.a. dividend yield. |

---

## 3. Statutory Transaction Cost Model

All simulated trades must incorporate actual Indian equity delivery charges:
- **Brokerage**: Flat ₹20 per executed order.
- **Securities Transaction Tax (STT)**: 0.10% on sell turnover.
- **Stamp Duty**: 0.015% on buy turnover.
- **Depository Participant (DP) Charges**: ₹15.93 per sell order (CDSL/NSDL).
- **Exchange Turnover Charges**: 0.00345% on turnover (NSE).
- **Goods & Services Tax (GST)**: 18.0% on (Brokerage + Exchange + SEBI).
- **SEBI Turnover Charges**: 0.0001% on turnover.
- **Execution Slippage**: 5 basis points (0.05%) per side (Base scenario); 10 bps and 20 bps (Adverse scenarios).

---

## 4. Pre-Registered Out-of-Sample Temporal Splits

1. **Development Window**: 2016-01-01 to 2020-12-31 (Implementation sanity check).
2. **Validation Window**: 2021-01-01 to 2023-12-31 (Diagnostic consistency check).
3. **Locked Holdout Window**: 2024-01-01 to 2026-10-13 (Untouched evaluation; zero parameter modifications permitted).

---

## 5. Mandatory Acceptance Gates (A through H)

| Gate ID | Standard / Requirement | Acceptance Threshold |
| :--- | :--- | :--- |
| **Gate A: Data Integrity** | Point-in-time filing lineage; zero restatement leakage; zero forward lookahead. | 100% causal timestamps; verified provenance. |
| **Gate B: Economic Edge** | Annualized net return excess over benchmark. | Net CAGR $\ge \text{Nifty 500 TRI} + 3.0\%$ p.a. |
| **Gate C: Risk Profile** | Risk-adjusted return & maximum peak-to-trough drop. | Sharpe > Benchmark Sharpe; Max DD $\le \text{Benchmark Max DD} + 5.0\%$. |
| **Gate D: Temporal Consistency** | Multi-window stability. | Positive excess returns in $\ge 3 \text{ of } 4$ independent temporal cells. |
| **Gate E: Event-Level Drift** | 63-session market-adjusted return after response session. | Statistically positive mean excess return ($p < 0.05$). |
| **Gate F: Holdout Integrity** | Performance in untouched 2024–2026 window. | Net alpha $> 0$ in locked holdout. |
| **Gate G: Cost Robustness** | Sensitivity to adverse market frictions. | Positive net alpha under 10 bps and 20 bps adverse slippage. |
| **Gate H: Reproducibility** | Full deterministic reproducibility. | 100% reproducible from immutable data manifests and code version. |
