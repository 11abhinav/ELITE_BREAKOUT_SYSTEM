# SFP-1 — FINAL GO / NO-GO GOVERNANCE CERTIFICATION REPORT

# VERDICT: REJECT

**Strategy Identifier**: `SFP-1` (Size Premium with Financial Survival Filters)  
**Research Namespace**: `research/strategies/sfp_1/`  
**Execution Date**: 2026-10-13  
**Data Evaluation Period**: 2018-07-02 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalance Cycles)  
**Universe**: Point-in-Time Smallcap 250 (Rank 251–500 by Market Capitalization)  
**Benchmark**: Nifty 500 TRI  

---

## 1. Governance Gate Scorecard

| Gate ID | Mandatory Acceptance Gate | Required Threshold | Measured Result | Gate Verdict | Evidence Reference |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **Gate 1** | **Data Validity & Survivorship** | Dataset includes historically failed/delisted smallcaps; zero lookahead. | **0 of 12** failed smallcaps found. Upward survivorship bias quantified at +4.94% CAGR. | **FAIL** | `reports/sfp_historical_universe_audit.md` |
| **Gate 2** | **Broad-Market Performance** | Net SFP-1 CAGR $\ge$ Nifty 500 TRI + 2.0% per annum. | Raw: **8.37%**, Adj: **3.43%** vs Nifty 500: **11.13%** (Gap: **-7.70% / yr**). | **FAIL** | `reports/sfp_benchmark_comparison.csv` |
| **Gate 3** | **Smallcap Benchmark Comparison** | Outperform simpler Smallcap 250 baseline. | Outperformed unfiltered smallcaps (8.37% vs 6.62%), but failed vs market. | **PASS (Partial)** | `reports/sfp_ablation_analysis.md` |
| **Gate 4** | **Incremental Factor Value** | Measurable value added by survival filter over unfiltered smallcaps. | Survival filters added **+1.75% CAGR** and improved profit factor from 3.72 to 4.89. | **PASS** | `reports/sfp_ablation_analysis.md` |
| **Gate 5** | **Temporal Consistency** | Positive excess return in $\ge 3$ of 4 independent chronological windows. | **0 out of 3** windows beat Nifty 500 (Dev: -4.11%, Val: -1.79%, Holdout: -2.42%). | **FAIL** | `reports/sfp_temporal_validation.csv` |
| **Gate 6** | **Risk-Adjusted Return** | Annualized Sharpe > Nifty 500 TRI (0.37); MaxDD acceptable. | Raw Sharpe: **0.40**; Adj Sharpe: **0.15** vs Nifty 500: **0.37**. | **FAIL** | `reports/sfp_risk_and_regime_analysis.md` |
| **Gate 7** | **Cost & Slippage Robustness** | Strategy retains advantage under 10 bps and 20 bps adverse slippage. | Raw CAGR is 8.27% under 20 bps slippage, but no market alpha exists. | **FAIL** | `reports/sfp_transaction_cost_audit.csv` |
| **Gate 8** | **Locked Holdout Integrity** | Positive net excess return during uncontaminated holdout (2024–2026). | Raw CAGR: **+1.87%**; Adj CAGR: **-3.07%** vs Nifty 500: **+4.29%** (Sharpe: **-0.89**). | **FAIL** | `reports/sfp_temporal_validation.csv` |
| **Gate 9** | **Reproducibility & Code Audit** | Fully reproducible outputs from frozen JSON config and source manifest. | Deterministic Python backtest, SHA-256 verified manifests, zero runtime errors. | **PASS** | `research/strategies/sfp_1/audit/sfp_1_manifest.json` |

*Overall Gate Result: **6 out of 9 Gates Failed**.*

---

## 2. Key Forensic Findings & Economic Root Causes

### 1. Absence of Small-Cap Size Premium in India (2018–2026)
Over the 8.3-year historical evaluation period, the Indian equity market strongly favored large-cap market leaders (Nifty 50 / Nifty 100) due to formalization, post-GST consolidation, banking sector cleanup, and institutional liquidity concentration.
Even on the raw surviving stock sample (which ignores bankruptcies), SFP-1 achieved **8.37% CAGR**, failing to beat the broad **Nifty 500 TRI (11.13% CAGR)**.

### 2. The Survivorship Bias Trap (+4.94% CAGR Inflation)
As documented in the 2025–2026 SSRN research paper on the Nifty Smallcap 250, backtests on surviving small-caps overstate returns by ~4.94% annually due to survivorship bias.
When this empirical correction is applied to account for unobserved small-cap liquidations:
* True estimated CAGR collapses to **3.43% Net CAGR**.
* Total 8.3-year wealth generation is **+32.21%** vs **+139.57%** for the broad market.

### 3. Survival Filters Work, But Cannot Fix a Deficient Factor
The ablation analysis confirmed that financial survival screening (PAT > 0, CFO > 0, Equity > 0) successfully added **+1.75% CAGR** over unfiltered smallcaps (8.37% vs 6.62%) and improved profit factor from 3.72 to 4.89. However, screening out bad small-caps was insufficient to generate market-beating excess returns when the underlying small-cap factor itself lagged the large-cap market.

### 4. Locked Holdout Failure
In the out-of-sample holdout period (January 2024 to October 2026), SFP-1 generated a stagnant raw return of **+1.87% CAGR** (and **-3.07% CAGR** after survivorship adjustment) with a negative Sharpe ratio of **-0.89**, underperforming the Nifty 500 TRI (+4.29% CAGR).

---

## 3. Final Governance Directive

1. **Strategy Status**: **REJECTED FOR PRODUCTION / LIVE ALERT GENERATION**.
2. **Production Code Isolation**: No production scanners, scheduled jobs, alert tables, or broker APIs will be modified or created for SFP-1.
3. **Research Preservation**: All code, configurations, data manifests, CSV ledgers, and forensic audit reports are permanently archived under `research/strategies/sfp_1/` and `reports/` for auditability and future factor research.
