# QGIO-1 — Final Go / No-Go Governance & Research Certification Report

**Candidate ID**: `QGIO_1`  
**Strategy Name**: Quality Growth + Verified Ownership Accumulation  
**Evaluation Date**: 2026-10-13  
**Historical Evaluation Window**: 2018-06-15 to 2026-10-12 (8.33 Years, 2,045 Trading Sessions)  
**Authors**: Elite Breakout System Quantitative Research & Forensic Audit  
**Final Governance Verdict**: **`RESEARCH_HOLD`**

---

## 1. Definitive Verdict & Core Executive Rationale

### FINAL VERDICT: **`RESEARCH_HOLD`**

1. **Gate 2 Data Absence (Mandatory Disclosed Net Buying)**:
   - Historical point-in-time transaction archives for SEBI insider trading (PIT Reg 7(2)), FII/DII bulk & block transactions, quarterly shareholding pattern filings, and promoter pledge changes **do not exist in the repository**.
   - Under project operating rules (`AGENTS.md`) and Section 3 of the mandate:
     > *"If essential historical data is absent or cannot be reconstructed point-in-time, stop full certification and report RESEARCH_HOLD. Do not generate synthetic history to fill gaps."*
   - Because ownership transaction history is absent, Strategy B (Ownership) and Strategy C (Full Model) generated **0 trades**, making it impossible to validate the ownership overlay.

2. **Gate 1 Chronological Data Gap (8-Quarter Profitability Rule)**:
   - Consolidated quarterly financial statements in `pit_fundamentals_v1.parquet` exist only for 10 quarters (starting June 2023).
   - Requiring 8 consecutive completed quarters of profitability point-in-time fails 100% of the universe prior to mid-2025.

3. **Strategy A (Baseline Engine) Failed the Locked Holdout**:
   - While Strategy A (Quality Growth + 50/200-Day Trend) compounded at **14.55% Net CAGR** over the full 8.33-year cycle (beating Nifty 500 TRI at 12.03% and Nifty Quality 50 at 7.33%), it **collapsed in the 2024–2026 Locked Holdout** to **-0.21% CAGR** (trailing the market by **-5.80%** with a **-36.69% drawdown**).
   - Low win rate (32.3%) and 200 SMA exit lag caused severe whipsaws during choppy, rotational market conditions.

---

## 2. Pre-Registered Acceptance Gates Scorecard

| Gate # | Governance Requirement | Pre-Registered Standard | Empirical Measurement | Gate Status |
| :---: | :--- | :--- | :---: | :---: |
| **Gate 1** | **Data Validity & PIT Lineage** | Certified exchange data; zero look-ahead | Certified price data; Screener annual data; quarterly & ownership data absent | **FAILED (Data Gap)** |
| **Gate 2** | **Broad-Market Advantage** | Net CAGR $\ge +2.0\%$ above Nifty 500 TRI | Strategy A: **+2.52%** (14.55% vs 12.03%); Strategy C: 0.00% (No data) | **PASSED (Arm A)** |
| **Gate 3** | **Quality Benchmark Challenge** | Outperform Nifty500 Quality 50 TRI by $\ge +1.0\%$ | Strategy A: **+7.22%** (14.55% vs 7.33%); Strategy C: 0.00% | **PASSED (Arm A)** |
| **Gate 4** | **Temporal Replication** | Positive excess in $\ge 3$ of 4 windows | **2 of 3 windows positive** (+3.48%, +10.72%, -5.80%) | **FAILED** |
| **Gate 5** | **Risk-Adjusted Efficiency** | Sharpe ratio above broad market (0.32) | Strategy A: **0.49** vs 0.32 benchmark; Max DD: -36.69% vs -38.30% | **PASSED (Arm A)** |
| **Gate 6** | **Ownership Marginal Value** | Strategy C materially improves upon Strategy A | Strategy C: 0 trades; **Zero demonstrable value** | **FAILED (Unprovable)** |
| **Gate 7** | **Cost & Slippage Robustness** | Advantage survives 10 & 20 bps adverse costs | Strategy A Net CAGR survives at **14.47%** (20 bps slippage) | **PASSED (Arm A)** |
| **Gate 8** | **Untouched Holdout Alpha** | Positive excess return in 2024–2026 holdout | Strategy A: **-0.21% CAGR** (Active lag: **-5.80%**) | **FAILED** |
| **Gate 9** | **Reproducibility & Audit Trail** | 100% reproducible scripts, configs, manifests | Fully verified and replicated via `run_qgio_1_backtest.py` | **PASSED** |

**Summary**: The strategy failed **4 out of 9 mandatory gates**, mandating a **`RESEARCH_HOLD`** governance verdict.

---

## 3. Four Core Research Questions Answered

### 1. Can the fundamental quality requirements be certified point-in-time?
- **Partially**. Five-year annual profitability, 3-year sales growth, ROCE $\ge 15\%$, CFO/PAT $\ge 0.8$, and Net Debt/EBITDA $\le 1.0$ can be certified point-in-time using audited annual filings (+75-day publication lag).
- **However**, the requirement for 8 consecutive quarters of profitability cannot be certified prior to mid-2025 due to lack of quarterly filings prior to 2023.

### 2. Can genuine disclosed net buying be separated from transfers and corporate actions?
- **Logically yes, but historically impossible in the repository**. The mathematical filter rules for excluding inter-se transfers, gifts, preferential allotments, and pledge invocations were fully formulated and codified in `qgio_transfer_exclusion_audit.csv`.
- **However**, because the repository contains zero historical transaction-level disclosure tables, no empirical separation could be executed on historical trades.

### 3. Does the ownership signal add value over quality and trend alone?
- **No demonstrable value**. Strategy A (Quality + Trend) produced 14.55% Net CAGR across 235 trades. Adding the ownership filter (Strategy C) produced 0 trades and 0.0% return because historical ownership disclosures are missing. The overlay cannot be credited with any performance advantage.

### 4. Does the strategy survive all temporal, cost, drawdown, and benchmark comparisons?
- **No**. While Strategy A beat the Nifty 500 TRI and Nifty Quality 50 over the full 8.33-year period and survived 20 bps of adverse slippage, it suffered a **-36.69% drawdown** and **failed the 2024–2026 locked holdout** with -0.21% CAGR vs +5.59% for the broad market.

---

## 4. Performance Matrix Across All Tested Candidates

| Strategy Candidate | Investment Hypothesis | Empirical Result | Governance Verdict | Key Limitation / Failure Mode |
| :--- | :--- | :---: | :---: | :--- |
| **RSMR-1** | Relative-strength momentum rotation (12M return deciles) | 31.57% CAGR (Retrospective) | **`RESEARCH_HOLD`** | Failed temporal consistency; retrospective lookback selection; severe crash risk. |
| **ESD-1** | Post-earnings-announcement drift (Quarterly earnings surprises) | -0.35% Event Drift | **`REJECTED`** | Negative drift; insufficient historical quarterly filing coverage. |
| **QPF-1** | Quality & shareholder payout (Semi-annual passive factor) | 6.67% CAGR (vs 12.03% N500) | **`REJECTED`** | Severe growth drag (-5.36% excess CAGR); payout factor acted as a value trap. |
| **QGIO-1** | Quality growth + verified ownership accumulation + 50/200 trend | Strategy A: 14.55% CAGR<br>Strategy C: 0.00% (No data) | **`RESEARCH_HOLD`** | **Ownership data absent**; Gate 1 quarterly data gap; Strategy A failed holdout (-0.21% CAGR). |

---

## 5. Verified Repository Artifacts

All scripts, configs, reports, and generated datasets are permanently archived in the isolated namespace:

- **Data Inventory**: [qgio_data_inventory.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_data_inventory.md)
- **Strategy Deduplication**: [qgio_strategy_deduplication.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_strategy_deduplication.md)
- **Data Readiness Gate**: [qgio_data_readiness_gate.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_data_readiness_gate.md)
- **Frozen Configuration**: [qgio_1_frozen_config.json](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/qgio_1/config/qgio_1_frozen_config.json)
- **Frozen Strategy Rules**: [qgio_frozen_strategy_rules.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_frozen_strategy_rules.md)
- **Fundamental Quality Audit**: [qgio_fundamental_quality_audit.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_fundamental_quality_audit.csv)
- **Ownership Transaction Audit**: [qgio_verified_ownership_transactions.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_verified_ownership_transactions.csv)
- **Transfer Exclusion Audit**: [qgio_transfer_exclusion_audit.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_transfer_exclusion_audit.csv)
- **Benchmark Comparison Data**: [qgio_benchmark_comparison.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_benchmark_comparison.csv)
- **Temporal Validation Data**: [qgio_temporal_validation.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_temporal_validation.csv)
- **Portfolio NAV Series**: [qgio_portfolio_nav.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_portfolio_nav.csv)
- **Complete Trade Ledger**: [qgio_trade_ledger.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_trade_ledger.csv)
- **Execution Cost Audit**: [qgio_execution_cost_audit.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_execution_cost_audit.csv)
- **Signal History**: [qgio_signal_history.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_signal_history.csv)
- **Strategy Ablation Analysis**: [qgio_ablation_analysis.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_ablation_analysis.md)
- **Risk and Regime Analysis**: [qgio_risk_and_regime_analysis.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_risk_and_regime_analysis.md)
- **Audit Manifest**: [qgio_1_manifest.json](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/qgio_1_manifest.json)
- **Simulation Engine Script**: [run_qgio_1_backtest.py](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/qgio_1/src/run_qgio_1_backtest.py)

---

## 6. Actionable Next Steps

1. **Do Not Promote to Production Alerts**: QGIO-1 remains in `RESEARCH_HOLD`. No alert routes, database tables, or scanner daemons have been altered.
2. **Upstream Ingestion Prerequisite**: If the team wishes to revisit ownership-based strategies in the future, the repository must first build an audited ingestion pipeline for:
   - SEBI PIT Regulation 7(2) insider transaction disclosures.
   - Historical NSE/BSE bulk and block deal archives (daily files backfilled from 2018).
   - Historical quarterly shareholding patterns with point-in-time filing dates.
3. **Synthesis of Research Findings**: Across RSMR-1, ESD-1, QPF-1, and QGIO-1:
   - Trend-following on quality growth companies (Strategy A) provides real full-cycle compounding (14.55% Net CAGR, 2.08 Profit Factor), but suffers from low win rate (32.3%) and whipsaws in choppy regimes.
   - Future strategy formulation must address regime adaptability—identifying when trend-following conditions are active versus when range-bound mean reversion dominates.
