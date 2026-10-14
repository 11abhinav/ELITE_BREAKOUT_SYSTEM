# PQLV-1 — FINAL GO / NO-GO GOVERNANCE CERTIFICATION REPORT

# VERDICT: REJECT

**Strategy Identifier**: `PQLV-1` (Profitable Quality, Low-Volatility Leaders)  
**Research Namespace**: `research/strategies/pqlv_1/`  
**Execution Date**: 2026-10-13  
**Data Evaluation Period**: 2018-06-29 to 2026-10-12 (2,045 Trading Sessions, 17 Semiannual Rebalance Cycles)  
**Universe**: Historical Point-in-Time Non-Financial NSE Equities  
**Benchmark**: Nifty 500 TRI, Nifty100 Low Volatility 30 Proxy, Nifty500 Quality 50 Proxy  

---

## 1. Governance Gate Scorecard

Every pre-registered acceptance gate established in the frozen research protocol was rigorously tested. The strategy failed 7 out of the 9 mandatory gates.

| Gate ID | Mandatory Acceptance Gate | Required Threshold | Measured Result | Gate Verdict | Evidence Reference |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **Gate 1** | **Data Validity & PIT Causality** | Zero future lookahead, audited filing lag ($\ge 90$ days), verified corporate actions. | Upstox 1D cache (872 symbols), 8,634 audited statements, zero lookahead. | **PASS** | `reports/pqlv_data_readiness_gate.md` |
| **Gate 2** | **Broad-Market Performance** | Net PQLV-1 CAGR $\ge$ Nifty 500 TRI CAGR + 2.0% annually over common period. | PQLV-1 CAGR: **2.32%** vs Nifty 500 TRI: **12.34%** (Gap: **-10.02% / yr**). | **FAIL** | `reports/pqlv_benchmark_comparison.csv` |
| **Gate 3** | **Factor Benchmark Comparison** | Beat Low-Vol proxy by $\ge 1\%$ CAGR, or within $1\%$ CAGR with $\ge 5\%$ lower MaxDD. | PQLV-1 CAGR: **2.32%** vs Low-Vol Proxy: **7.89%** (Gap: **-5.57% / yr**). | **FAIL** | `reports/pqlv_benchmark_comparison.csv` |
| **Gate 4** | **Incremental Factor Value** | Measurable benefit over Quality-Only (Model B) and Low-Vol-Only (Model C). | Model B (Quality-Only): **4.41%** vs PQLV-1: **2.32%**. Low-Vol destroyed 209 bps of return. | **FAIL** | `reports/pqlv_ablation_analysis.md` |
| **Gate 5** | **Temporal Consistency** | Positive excess return in $\ge 3$ of 4 independent chronological windows. | 0 out of 3 windows generated positive excess return vs Nifty 500 (Dev: -7.6%, Val: -14.8%, Holdout: -7.1%). | **FAIL** | `reports/pqlv_temporal_validation.csv` |
| **Gate 6** | **Risk-Adjusted Return** | Annualized Sharpe > Nifty 500 TRI (0.34); MaxDD not $> 5\%$ worse. | PQLV-1 Sharpe: **-0.91** vs Nifty 500: **0.34**. (MaxDD: -9.71% passed, but Sharpe failed). | **FAIL** | `reports/pqlv_risk_and_regime_analysis.md` |
| **Gate 7** | **Cost & Slippage Robustness** | Retain economically meaningful advantage under 10 bps and 20 bps slippage. | PQLV-1 20 bps CAGR: **2.27%**. No alpha exists to preserve. | **FAIL** | `reports/pqlv_transaction_cost_audit.csv` |
| **Gate 8** | **Locked Holdout Integrity** | Positive net excess return during uncontaminated holdout (2024–2026). | PQLV-1 Holdout CAGR: **-1.46%** vs Nifty 500: **+5.59%** (Excess: **-7.05%**, Sharpe: **-2.09**). | **FAIL** | `reports/pqlv_temporal_validation.csv` |
| **Gate 9** | **Reproducibility & Code Audit** | Fully reproducible outputs from frozen JSON config and source manifest. | Deterministic Python backtest, SHA-256 verified manifests, zero runtime errors. | **PASS** | `research/strategies/pqlv_1/audit/pqlv_1_manifest.json` |

---

## 2. Key Findings and Forensic Root Causes

### 1. Massive Factor Underperformance in Indian Equities
Over the 8.3-year period (June 2018 to October 2026), the Indian equity market rewarded earnings growth, momentum, and operational expansion. Selecting solely for lowest trailing 252-day price volatility systematically forced capital into the most stagnant, low-beta sectors (primarily slow-growing consumer staples like Marico, Dabur, and Colgate). 
While these stocks offered admirable capital protection during market panics (maximum portfolio drawdown of only **-9.71%** vs **-38.30%** for the Nifty 500), they failed completely to participate in economic expansions, delivering a net annualized return of just **2.32%**, which severely lagged the broad market's **12.34%** CAGR.

### 2. Negative Interaction Between Quality and Low Volatility
Ablation testing revealed that:
* Removing the quality filters and using low volatility alone (Model C) delivered **2.13% CAGR**.
* Removing volatility and selecting the highest-quality companies directly (Model B) delivered **4.41% CAGR**.
* When low volatility was combined with quality (Model D: PQLV-1), performance collapsed from **4.41%** down to **2.32%**.
Filtering for low volatility systematically stripped out high-performing, high-ROCE compounders (such as midcap tech, auto ancillaries, and specialty chemicals) whose prices naturally fluctuated, leaving only low-growth, low-beta laggards.

### 3. Crippling Structural Cash Drag
The frozen protocol mandated:
* Target weight of 1/30 (3.33% of NAV) per qualifying stock.
* No reallocation of unfilled slots ("Unallocated capital remains cash").
Because the strict multi-year accounting gates (5 consecutive profitable years, positive 3Y revenue CAGR, ROCE $\ge 15\%$, CFO/PAT $\ge 0.8$) permitted only 3 to 10 stocks to qualify in earlier cycles, **60% to 90% of the portfolio sat uninvested in cash**. Cash yielding 0% in a double-digit equity bull run caused catastrophic underperformance.

### 4. Locked Holdout Failure
In the untouched, out-of-sample holdout window (January 2024 to October 2026), PQLV-1 delivered **negative absolute returns (-1.46% CAGR)** with an annualized Sharpe ratio of **-2.09**, while the Nifty 500 grew at **+5.59% CAGR**.

---

## 3. Governance Directive

1. **Strategy Status**: **REJECTED FOR PRODUCTION / LIVE ALERT GENERATION**.
2. **Production Code Isolation**: No scanners, scheduled daemons, alert tables, or broker APIs will be modified or created for PQLV-1.
3. **Research Archive**: All code, configurations, data manifests, CSV ledgers, and audit reports are permanently archived under `research/strategies/pqlv_1/` and `reports/` for historical research and academic reference.
