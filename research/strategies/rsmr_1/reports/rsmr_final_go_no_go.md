# RSMR-1 FINAL GO / NO-GO GOVERNANCE VERDICT

## VERDICT: RESEARCH_HOLD

**Strategy**: Relative-Strength Momentum Rotation (RSMR-1)  
**Governance Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  
**Determination**: **DO NOT PROMOTE RSMR-1 TO THE PRODUCTION ALERT SYSTEM.**  

---

## 1. Executive Summary & Verdict Rationale

Following a rigorous, independent forensic audit and full reproduction of the RSMR-1 historical backtest across 947 verified NSE equities and 2,490 Upstox daily sessions, the empirical determination is **RESEARCH_HOLD**.

While the strategy demonstrates massive historical compounding during favorable macroeconomic liquidity regimes (generating **38.55% Net CAGR** for 12-Month Momentum and **35.91% Net CAGR** for 6-Month Semi-Annual rebalancing over the 2018–2026 common evaluation window), **it categorically fails the project's pre-defined Temporal Replication and Out-of-Sample Consistency Gate**.

Specifically:
1. **Temporal Inconsistency & Recent Momentum Decay**:
   - The 6-Month baseline generated **negative excess returns in 2 of the 4 temporal cells**: Cell 1 (2018: -8.58% CAGR vs -1.67% benchmark) and Cell 4 (2025–2026: -5.16% CAGR vs -1.43% benchmark).
   - This directly violates the mandatory project acceptance rule requiring positive excess returns in **at least 3 of 4 independent temporal cells**.
   - In Cell 4, the baseline suffered a deep **-30.62% maximum drawdown** while the benchmark experienced only -14.82%.
2. **Selection Bias & Data Snooping Risk**:
   - The top-performing **12-Month Momentum Quarterly variant (38.55% Net CAGR)** was selected retrospectively after examining the entire 2016–2026 historical period.
   - Its rules have not yet been evaluated across an untouched, pre-registered prospective out-of-sample forward evaluation window.
3. **Historical Survivorship Bias**:
   - The 946-stock universe consists of surviving NSE equities with 10-year Upstox price histories. Historically delisted, merged, or bankrupt companies (e.g., DHFL, RCom, Jet Airways) are absent. Academic research indicates an inherent **1.5% to 2.5% annual CAGR bias** in unadjusted momentum simulations.
4. **Bear-Market Vulnerability**:
   - In Bear regimes, the strategy experienced **-21.27% annualized return**, offering **zero capital protection** relative to the benchmark (-20.34%).

---

## 2. Formal Governance Acceptance Gate Evaluation

The pre-defined governance criteria were applied strictly without modification or ex-post relaxation:

| Governance Gate | Requirement / Threshold | Actual Audited Result | Evidence / Artifact | Status | Failure Reason |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Gate 1: Data Integrity & Causality** | Real Upstox market data; zero forward leakage; 100% causal signals. | 947 verified NSE equities; 2,490 daily sessions; signal formed strictly at Close $T$, executed at $T+1$. | `rsmr_session_reconciliation.md` | **PASS** | Met all real data and point-in-time requirements. |
| **Gate 2: Session & Date Reconciliation** | 100% mathematical reconciliation of all trading sessions, warm-up, and regimes. | Reconciled: 2,490 total = 326 warm-up + 18 calendar gap + 2,146 active regime/cell sessions. | `session_reconciliation.json` | **PASS** | Discrepancy fully resolved. |
| **Gate 3: Benchmark TRI Outperformance** | Net CAGR $\ge 15.0\%$ AND Excess vs Nifty 500 TRI $\ge +5.0\%$ p.a. | 12M: 38.55% Net CAGR (+25.93% vs TRI 12.62%).<br>6M Semi: 35.91% Net CAGR (+23.28% vs TRI). | `rsmr_common_window_comparison.csv` | **PASS** | Large net hurdle clearance on common window. |
| **Gate 4: Maximum Drawdown Relative to Market** | Strategy Max Drawdown $\le 1.25\times$ Benchmark Max Drawdown. | Strategy Max DD: **-36.34%** (12M) / **-36.53%** (6M).<br>Benchmark Max DD: **-38.30%**. | `rsmr_common_window_comparison.csv` | **PASS** | Peak-to-trough drop bounded inside COVID shock. |
| **Gate 5: Temporal Replication Consistency** | Positive excess CAGR in $\ge 3 \text{ of } 4$ independent temporal cells. | 6M Baseline passed in **only 2 of 4 cells** (Cell 1: -6.92% excess, Cell 4: -3.73% excess).<br>12M lookback passed 3 of 4, but Cell 1 had -9.13% excess and Cell 4 absolute CAGR was only 6.37%. | `rsmr_temporal_cells_6m_vs_12m.csv` | **FAIL** | **CRITICAL FAILURE**: Strategy failed the 3 of 4 temporal cell rule; recent momentum decayed. |
| **Gate 6: Execution Cost & Statutory Ledger** | Audited deduction of all Indian statutory charges, taxes, and slippage. | Deducted ₹2,80,135.77 across STT, GST, Stamp Duty, DP charges, Brokerage, and 5 bps slippage per side. | `rsmr_execution_cost_audit.csv` | **PASS** | Every cost line verified and deducted exactly once. |
| **Gate 7: Out-of-Sample Selection Integrity** | Winning parameter configuration must not be selected post-hoc on full test set. | 12-Month lookback was chosen after full-period retrospective backtest. Untouched forward holdout missing. | `rsmr_forensic_validation.md` | **FAIL** | High data snooping / overfitting risk. |
| **Gate 8: Capital Preservation in Bear Regimes** | Meaningful downside protection during declared Bear regimes. | Strategy CAGR in Bear regimes was **-21.27%** vs benchmark **-20.34%**. Drawdown in Cell 4 reached **-30.62%** vs benchmark -14.82%. | `rsmr_temporal_and_regime_revalidation.csv` | **FAIL** | Long-only momentum suffers severe drawdowns in prolonged corrections. |

**Summary**: 5 Gates Passed, 3 Gates Failed. **Automatic verdict: RESEARCH_HOLD.**

---

## 3. Metric Reconciliation Summary: Reported vs Audited

| Metric | Reported Value | Independently Audited Value | Status | Forensic Note |
| :--- | :---: | :---: | :---: | :--- |
| **Source Market Sessions** | 2,490 | 2,490 | **VERIFIED** | Matched across 947 NSE Upstox Parquet files. |
| **Active Evaluated Sessions** | 2,146 | 2,146 | **RECONCILED** | 326 warm-up sessions (2016-09-27 to 2018-01-16) + 18 calendar offsets fully accounted for. |
| **Benchmark CAGR (6M Baseline)** | 10.11% | 10.11% (PR) / 11.41% (TRI) | **RECONCILED** | Reported used Price Return starting 2018-01-17. |
| **Benchmark CAGR (12M Lookback)** | 11.32% | 11.32% (PR) / 12.62% (TRI) | **RECONCILED** | Started 2018-07-20 due to 252d warm-up. Standardized on 2,020 common sessions. |
| **RSMR-1 6M Quarterly Net CAGR** | 28.46% | 28.46% (full) / 30.14% (common) | **VERIFIED** | Exact reproduction from daily portfolio NAV. |
| **RSMR-1 6M Semi-Annual Net CAGR** | 31.57% | 31.57% (full) / 35.91% (common) | **VERIFIED** | Exact reproduction from daily portfolio NAV. |
| **RSMR-1 12M Quarterly Net CAGR** | 38.55% | 38.55% | **VERIFIED** | Exact reproduction over common evaluation window. |
| **Maximum Drawdown (12M Lookback)** | -36.34% | -36.34% | **VERIFIED** | Occurred during March 2020 COVID shock. |
| **Sharpe Ratio (12M Lookback)** | 1.34 | 1.34 | **VERIFIED** | Annualized daily returns with 6.50% risk-free rate. |
| **Total Friction Deducted** | ~₹2.80L | ₹2,80,135.77 | **VERIFIED** | Fully accounted for in transaction ledger. |

---

## 4. Key Unresolved Limitations

1. **Severe Regime Sensitivity & Lack of Capital Protection**:
   RSMR-1 is a pure long-only relative strength rotation system. In trending bull markets (such as 2020–2021 and 2023–2024), it produces extraordinary returns (+162% and +189% episode returns). However, when market leadership breaks down (as in 2018 and late 2024–2026), it stays fully invested in deteriorating momentum names, incurring deeper drawdowns than the index.
2. **Post-2024 Performance Degradation (Temporal Cell 4)**:
   Between January 2025 and October 2026, the 6-Month baseline produced -5.16% CAGR and a -30.62% drawdown. While the 12-Month variant fared better (+6.37% CAGR), its Sharpe ratio collapsed to 0.12. Momentum rotation has not maintained its historical edge in recent market regimes.
3. **Absence of Point-in-Time Delisted Equities**:
   Without an authoritative, point-in-time delisted stock database for 2016–2024, the simulation carries a known ~1.5% to 2.5% survivorship bias that cannot be eliminated solely through local data.

---

## 5. Required Actions Prior to Re-Evaluation

Before RSMR-1 can be considered for promotion to production alerts:
1. **Implement Dynamic Cash / Hedging Rules in Bear Regimes**:
   Evaluate an objective cash-protection overlay (e.g. holding 100% liquid cash / T-Bills whenever Nifty 500 drops below its 200-day SMA) to prevent the -21.27% Bear-regime CAGR drain.
2. **Freeze the 12-Month Momentum Model for Prospective Forward Tracking**:
   Lock the rules of the 12-Month Quarterly and 6-Month Semi-Annual variants into a frozen research registry.
3. **Resolve Cell 4 Momentum Breakdown**:
   Establish whether the recent 2025–2026 underperformance is a temporary cyclical consolidation or structural momentum decay due to market microstructure shifts.

**Final Directive**: The system will maintain RSMR-1 under **`RESEARCH_HOLD`**. No live production alerts, scanner routing, or broker execution models will be enabled for RSMR-1 at this time.
