# ESD-1: FINAL GO / NO-GO GOVERNANCE VERDICT

## VERDICT: REJECT

**Strategy**: Point-in-Time Earnings Surprise Drift (`ESD_1`)  
**Research Phase**: Independent Historical Backtest, Event Study & Falsification Suite  
**Governance Date**: October 13, 2026  
**Auditor**: Independent Quantitative & Forensic Research Suite  
**Determination**: **DO NOT PROMOTE ESD-1 TO THE PRODUCTION ALERT SYSTEM. REJECT HYPOTHESIS AS A STANDALONE ALERT ENGINE.**  

---

## 1. Executive Summary & Verdict Rationale

Following a rigorous, point-in-time historical backtest, comprehensive event study, and portfolio simulation across **806 verified NSE equities** and **3,738 corporate earnings announcements**, the definitive governance determination is **`REJECT`**.

The core hypothesis—that quarterly earnings beats ($SUE \ge 1.0$) followed by a 1-day market-relative price confirmation generate persistent post-announcement abnormal drift over a 63-session holding period—**is empirically falsified on contemporary Indian equity data**.

### Key Evidence for Rejection:
1. **Negative Absolute and Relative Returns**:
   - The strategy produced a **Net CAGR of -0.65%** over the active evaluation span (2023–2026), trailing the Nifty 500 TRI (+9.94%) by **-10.59 percentage points p.a.**
   - Total cumulative portfolio return was **-2.03%** while the benchmark gained **+34.61%**.
2. **Abysmal Trade Win Rate & Profit Factor**:
   - The portfolio simulation completed 21 trades with a **win rate of only 33.3%** (14 losses, 7 wins).
   - Profit Factor was **0.74** (< 1.0), meaning gross losses substantially exceeded gross profits.
3. **Event-Study Median Drift is Systematically Negative**:
   - Across 564 extreme earnings beats ($SUE \ge 2.0$), the **median 63-day market-adjusted return was -1.31%**, with a win rate of only **47.0%**.
   - Across 367 strong earnings beats ($1.0 \le SUE < 2.0$), the **median 63-day market-adjusted return was -0.55%**, with a win rate of only **46.9%**.
   - Over 53% of all post-announcement earnings beat positions underperformed the broad market.
4. **Severe Cash Drag & Structural Inefficiency**:
   - Because quarterly earnings are announced in narrow 4-week clusters (Jan, Apr, Jul, Oct), the portfolio suffered extreme cash drag (80%–95% idle cash outside earnings seasons), guaranteeing severe underperformance during market advances.
5. **Locked Holdout Failure (2024–2026)**:
   - In the pre-registered locked holdout window (2024 to October 2026), the strategy produced **-0.95% Net CAGR** vs **+5.59% Nifty 500 TRI**, failing the holdout gate.
6. **Data Provenance & Historical Scope Failures**:
   - The repository's quarterly financial statements originate from `SCREENER_QUARTERLY_STANDALONE`, which is strictly classified as `FORENSIC_REFERENCE_ONLY` under `AGENTS.md` and prohibited from production strategy certification.
   - The strict frozen rule requiring 8 historical YoY changes was mathematically unviable across 99.8% of the market due to the lack of pre-2023 quarterly filings.

---

## 2. Evaluation Against Pre-Registered Acceptance Gates

| Gate ID | Governance Gate | Acceptance Requirement | Actual Audited Result | Status | Failure Reason |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Gate A** | **Data Integrity & Lineage** | Point-in-time filing lineage; zero lookahead; certified data provenance. | 100% causal timestamps, but source is Screener-derived (`AGENTS.md` violation) and pre-2023 quarterly data missing. | **FAIL** | Prohibited source provenance & truncated history. |
| **Gate B** | **Economic Edge** | Net CAGR $\ge \text{Nifty 500 TRI} + 3.0\%$ p.a. over evaluation window. | Strategy Net CAGR: **-0.65%** vs Benchmark TRI: **+9.94%** (Excess: **-10.59%**). | **FAIL** | **CRITICAL FAILURE**: Severe negative excess return. |
| **Gate C** | **Risk Profile** | Sharpe > Benchmark Sharpe; Max Drawdown $\le \text{Benchmark Max DD} + 5.0\%$. | Sharpe: **-1.93** (vs Benchmark: +0.21); Max DD: **-8.07%** (vs Benchmark: -18.84%). | **FAIL** | Sharpe ratio deeply negative. |
| **Gate D** | **Temporal Consistency** | Positive benchmark-relative performance in $\ge 3 \text{ of } 4$ temporal windows. | 2016–2020: 0 events; 2021–2023: -48.37% excess; 2024–2026: -6.54% excess. Zero positive windows. | **FAIL** | Failed 100% of evaluable temporal splits. |
| **Gate E** | **Event-Level Drift** | 63-session market-adjusted return of positive-SUE events statistically positive. | Median 63-day excess is **-0.55% to -1.31%**; win rate is **46.9% to 47.0%**. | **FAIL** | Median drift is negative; edge non-existent. |
| **Gate F** | **Holdout Integrity** | Positive net benchmark-relative performance in locked 2024–2026 holdout. | Holdout Net CAGR: **-0.95%** vs Benchmark: **+5.59%** (Alpha: **-6.54%**). | **FAIL** | Failed untouched forward evaluation. |
| **Gate G** | **Cost Robustness** | Economically meaningful under 10 bps and 20 bps adverse slippage. | Net CAGR worsens to **-1.45%** (10 bps) and **-3.05%** (20 bps). | **FAIL** | Accelerated capital loss under costs. |
| **Gate H** | **Reproducibility** | Result reproducible from source manifests, code version, and test outputs. | 100% reproducible via `run_esd_1_backtest.py`. | **PASS** | Fully auditable and reproducible. |

**Gate Score**: 1 Pass, 7 Fails. **Mandatory Outcome: REJECT.**

---

## 3. Metric Reconciliation Summary: Expected vs Measured

| Metric | Expected / Threshold | Measured ESD-1 Result | Benchmark (Nifty 500 TRI) | Variance |
| :--- | :---: | :---: | :---: | :---: |
| **Net Annualized Return (CAGR)** | $\ge +12.94\%$ | **-0.65%** | +9.94% | **-10.59% (FAIL)** |
| **Cumulative Total Return** | $\ge +40.0\%$ | **-2.03%** | +34.61% | **-36.64% (FAIL)** |
| **Sharpe Ratio** | $> 0.21$ | **-1.93** | +0.21 | **-2.14 (FAIL)** |
| **Maximum Drawdown** | $\ge -23.84\%$ | **-8.07%** | -18.84% | +10.77% (Bounded by cash) |
| **Trade Win Rate** | $\ge 50.0\%$ | **33.3%** | N/A | **-16.7% (FAIL)** |
| **Profit Factor** | $\ge 1.50$ | **0.74** | N/A | **-0.76 (FAIL)** |
| **Event-Study Median 63D Excess** | $> +0.0\%$ | **-0.55% to -1.31%** | 0.00% | **Negative Drift (FAIL)** |
| **Holdout Alpha (2024–2026)** | $> 0.0\%$ | **-6.54%** | 0.00% | **-6.54% (FAIL)** |

---

## 4. Key Unresolved Limitations & Falsification Root Cause

1. **Standalone PEAD Does Not Work in Modern Indian Equities**:
   Simple post-earnings announcement drift using time-series SUE and a fixed 63-day holding period does not generate positive alpha in contemporary Indian markets. Post-2020 market efficiency rapidly absorbs earnings surprises within trading hours.
2. **Long-Only Fixed Holding Suffers Bear Market & Correction Drag**:
   Without an objective technical trend filter (e.g. Price > SMA200) or regime gating, buying an earnings beat during a broad market correction simply exposes capital to ongoing systemic distribution.
3. **Severe Event Clustering & Idle Cash Drag**:
   Because earnings announcements are concentrated in 4 discrete quarterly earnings seasons, an unhedged 20-slot portfolio remains 80%–95% in cash for roughly 8 months of the year, ensuring massive underperformance against a fully invested benchmark.

---

## 5. Architectural Recommendations for Future Research

Now that both **RSMR-1** (Price Momentum) and **ESD-1** (Standalone Earnings Surprise Drift) have been rigorously audited and rejected/held:
1. **Do NOT Pursue Standalone Unhedged Earnings Drift**:
   Do not attempt further parameter tuning on SUE thresholds, holding periods, or response filters for ESD-1.
2. **Earnings Beats Should Serve as Catalysts, Not Sizing Engines**:
   Prior research (`EARNINGS_CALENDAR_RESEARCH_V1`) showed that earnings beats can provide incremental alpha (+112 bps) **only when combined with established fundamental quality compounders and technical trend confirmation**, not as an isolated stand-alone event strategy.
3. **Focus on Regime-Gated Confluence Systems**:
   Future strategy candidate proposals should focus on structural confluence models that incorporate dynamic cash preservation during Bear regimes to prevent the drawdowns that caused both RSMR-1 and ESD-1 to fail.

---

## 6. Final Directive

**ESD-1 is permanently classified as `REJECT`.**  
No alert routing, scheduling, broker integration, or live monitoring will be enabled for ESD-1. All research code, event datasets, and audit ledgers remain preserved for auditability in `research/strategies/esd_1/`.
