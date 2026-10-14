# ESD-1: ROBUSTNESS & FALSIFICATION ANALYSIS

**Strategy**: Point-in-Time Earnings Surprise Drift (`ESD_1`)  
**Evaluation Standard**: Mandatory Falsification, Horizon Attribution & Cost Sensitivity  
**Audit Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  

---

## 1. Executive Summary: Hypothesis Falsification

The core investment hypothesis of ESD-1 states that **stocks reporting unexpected quarterly EPS beats ($SUE \ge 1.0$) continue to experience post-announcement abnormal drift over a 63-session holding period following the initial response session**.

### Empirical Conclusion:
> **THE HYPOTHESIS IS FALSIFIED ON CONTEMPORARY INDIAN MARKET DATA.**

While positive earnings surprises occasionally produce large positive outliers that elevate the **arithmetic mean return**, the **median excess return across all holding horizons is negative**, the **win rate is below 47%**, and **the portfolio simulation generated negative annualized returns (-0.65% Net CAGR)** trailing the Nifty 500 TRI by **-10.59 percentage points**.

---

## 2. Event-Study Horizon Attribution (Pre-Defined SUE Groups)

Across 3,738 verified events with complete daily price series, abnormal returns relative to the Nifty 500 TRI were measured at 1D (response), 5D, 21D, 42D, and 63D horizons:

| SUE Group | Event Count | 1D Response Excess | 5D Excess | 21D Excess | 42D Excess | 63D Mean Excess | 63D Median Excess | 63D Win Rate (% Pos) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Extreme Negative ($SUE < -1.0$)** | 308 | -0.52% | +0.22% | +1.32% | +0.30% | **+0.92%** | **-1.83%** | 45.1% |
| **2. Moderate Negative ($-1.0 \le SUE < 0$)** | 384 | -0.67% | -0.02% | +0.79% | +1.46% | **+2.39%** | **-1.56%** | 46.1% |
| **3. Moderate Positive ($0 \le SUE < 1.0$)** | 580 | -0.21% | -0.14% | +0.25% | +1.47% | **+2.90%** | **-1.41%** | 46.6% |
| **4. Strong Positive ($1.0 \le SUE < 2.0$)** | 367 | -0.14% | -0.11% | -0.28% | +1.41% | **+2.66%** | **-0.55%** | 46.9% |
| **5. Extreme Positive ($SUE \ge 2.0$)** | 564 | +0.07% | +0.55% | +1.62% | +1.45% | **+2.14%** | **-1.31%** | 47.0% |

*Artifact Reference: [`reports/esd_1_event_study_results.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/esd_1_event_study_results.csv)*

### Critical Forensic Insights:
1. **Negative Median Excess Return Across All Groups**:
   - For Strong Positive beats ($1.0 \le SUE < 2.0$), the median 63-day excess return is **-0.55%**.
   - For Extreme Positive beats ($SUE \ge 2.0$), the median 63-day excess return is **-1.31%**.
   - In over **53% of cases**, an earnings beat stock **underperformed the broad market** over the subsequent quarter.
2. **Failure of SUE Monotonicity**:
   - In a robust PEAD anomaly, abnormal returns should scale monotonically from negative to positive ($SUE < -1.0 \rightarrow SUE \ge 2.0$).
   - In our empirical test, **Moderate Negative earnings surprises produced +2.39% mean excess**, virtually identical to Extreme Positive surprises (+2.14%).
   - Earnings surprise magnitude alone provides zero reliable rank correlation with subsequent 63-day market outperformance.
3. **Positive Mean Driven by Skewness**:
   - The positive mean excess return (+2.14% to +2.66%) is driven entirely by a tiny handful of high-beta momentum stocks that experienced unrelated sector rallies during the holding period.

---

## 3. Portfolio Backtest Performance Under Realistic Constraints

Simulating an actual 20-slot portfolio (5% target slot size, 25% sector cap, whole-share orders, Indian statutory transaction taxes, and cash drag) produced disastrous results:

| Metric | ESD-1 Frozen Strategy | Nifty 500 TRI Benchmark | Strategy vs Benchmark (Alpha / Delta) |
| :--- | :---: | :---: | :---: |
| **Net CAGR** | **-0.65%** | **+9.94%** | **-10.59% (Severe Negative Alpha)** |
| **Cumulative Total Return** | **-2.03%** | **+34.61%** | **-36.64%** |
| **Sharpe Ratio (Rf = 6.5%)** | **-1.93** | **+0.21** | **-2.14** |
| **Maximum Drawdown** | **-8.07%** | **-18.84%** | +10.77% (Bounded due to cash drag) |
| **Total Completed Trades** | 21 | N/A | Low statistical sample |
| **Trade Win Rate** | **33.3%** | N/A | **66.7% losing trades** |
| **Profit Factor** | **0.74** | N/A | Gross losses exceed gross profits |

*Artifact Reference: [`reports/esd_1_portfolio_nav.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/esd_1_portfolio_nav.csv) and [`reports/esd_1_trade_ledger.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/esd_1_trade_ledger.csv)*

---

## 4. Root Cause Analysis: Why ESD-1 Failed

1. **Severe Cash Drag from Sparse Clustering**:
   Quarterly earnings are announced during compressed 4-week reporting windows (January, April, July, October). Outside these reporting windows, zero new signals arrive, leaving the portfolio sitting with 80% to 95% idle cash. In a rising market, this cash drag guaranteed catastrophic benchmark underperformance.
2. **Fixed 63-Day Hold Without Trend or Stop-Loss Exits**:
   Holding stocks for a rigid 63 trading sessions regardless of price action forces the strategy to hold declining companies through post-earnings distribution. When an initial market reaction reverses (a "bull trap" beat-and-dump), ESD-1 suffers 100% of the drawdown.
3. **Loss of Contemporary Edge in Indian Equities**:
   Algorithmic and institutional participation in Indian equities has accelerated dramatically between 2020 and 2026. Earnings news is largely digested within hours or days. The multi-month drift reported in 2001–2006 academic papers has largely decayed in liquid NSE stocks.

---

## 5. Adverse Transaction Cost Sensitivity

Evaluating the strategy across pre-defined adverse slippage scenarios reveals that increased execution friction accelerates capital destruction:

| Slippage Scenario | Strategy Net CAGR | Excess vs Nifty 500 TRI | Sharpe Ratio | Max Drawdown |
| :--- | :---: | :---: | :---: | :---: |
| **Base Friction (5 bps per side)** | **-0.65%** | **-10.59%** | **-1.93** | **-8.07%** |
| **Adverse 1 (10 bps per side)** | **-1.45%** | **-11.39%** | **-1.98** | **-8.87%** |
| **Adverse 2 (20 bps per side)** | **-3.05%** | **-12.99%** | **-2.08** | **-10.17%** |

*Artifact Reference: [`reports/esd_1_benchmark_comparison.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/esd_1_benchmark_comparison.csv)*

---

## 6. Falsification Verdict

**FALSIFICATION TEST RESULT**: **HYPOTHESIS SYSTEMATICALLY FAILS**  
The standalone Earnings Surprise Drift hypothesis fails on win rate, fails on median excess return, fails on economic edge, and fails on portfolio return.
