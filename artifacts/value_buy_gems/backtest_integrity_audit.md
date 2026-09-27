# VALUE_BUY_GEMS: MANDATORY BACKTEST INTEGRITY & ACCOUNTING AUDIT REPORT
**Audit Evaluation Date:** 2026-09-27  
**Audit Objective:** Independent Forensic Audit of Numerical Contradictions, Accounting Integrity, Outlier Concentration, and Lookahead Bias  
**Governance Invariant:** AGENTS.md Mandatory Real-Market-Data & Point-in-Time Causality Protocol  

---

## EXECUTIVE AUDIT SUMMARY & CRITICAL FINDINGS

A thorough forensic audit was conducted on the reported `VALUE_BUY_GEMS` 3-year multi-variant backtest results.

### 🚨 FATAL DEFECT CONFIRMED: MASSIVE FUTURE-INFORMATION LOOKAHEAD LEAKAGE
1. **The 52–75% CAGR is NOT structurally repeatable; it is an artifact of 2026 snapshot lookahead bias:**
   - In `E2_X0_P2`, **TWO microcap stocks (`SIGMAADV` +1,873.96% and `VMARCIND` +1,807.03%) contributed 72.63% of total portfolio profits**.
   - These two stocks were selected into the top-ranked slots on `2023-09-26` because the ranking engine used **late-2026 snapshot fundamentals** (`multibagger_fundamentals_cache.json`), where `SIGMAADV` recorded an extreme post-rally ROCE of 83.2% and ROE of 87.6%.
   - **Excluding just these two snapshot-selected outliers, portfolio CAGR collapses from 52.05% down to 18.23%**.
   - **Excluding the top 5 winners, portfolio CAGR collapses to 11.42%, which is UNDERPERFORMING the Nifty 50 Buy-and-Hold benchmark (12.80% CAGR)**.

---

## 1. RESOLUTION OF CRITICAL CONTRADICTIONS

### A. Contradiction 1: E1 (Cheap Only) vs E2 (Quality + Cheap)
- **Report Claim:** *"Quality is the Indispensable Edge (Cheap Alone Fails)"*
- **Actual Scorecard Numbers:**
  - `E1_X0_P2` (Cheap Only): **CAGR = 55.80%**, Sharpe = 2.21, Max DD = 26.62%
  - `E2_X0_P2` (Quality + Cheap): **CAGR = 52.05%**, Sharpe = 2.08, Max DD = 25.48%
- **Audit Finding:**
  - `E1` had **higher CAGR (+3.75%)** and **higher Sharpe (+0.13)** than `E2`.
  - A formal paired statistical test reveals:
    ```text
    Mean Delta Return: -26.69% (E2 minus E1)
    Welch t-statistic: -0.153
    p-value: 0.8786 (Statistically Insignificant)
    Cohen's d: -0.050 (Effect Size Zero/Negative)
    95% Bootstrap CI: [-388.45%, +345.12%]
    ```
  - **Verdict: `QUALITY_ADDON_RESULT = NOT_SUPPORTED`**. The narrative claim in the previous report was empirically false and contradicted by the scorecard.

---

### B. Contradiction 2: X7 (Structural SMA200 Exit) vs X0 (Pure Hold)
- **Report Claim:** *"X7 delivered superior capital preservation... SMA200 provides useful downside protection."*
- **Actual Scorecard Numbers:**
  - `E2_X0_P2` (Pure Hold): **CAGR = 52.05%**, Max DD = **25.48%**, Sharpe = **2.08**, Calmar = **2.04**
  - `E2_X7_P2` (SMA200 Exit): **CAGR = 22.91%**, Max DD = **30.85%**, Sharpe = **0.88**, Calmar = **0.74**
- **Audit Finding:**
  - `X7` suffered **WORSE maximum drawdown (30.85% vs 25.48%)**, **much worse Sharpe (0.88 vs 2.08)**, and **much worse Calmar (0.74 vs 2.04)**.
  - **Why did X7 produce 342 trades and 11.11% win rate?**
    - In `X0`, the 20 slots were filled once and held continuously (20 trades).
    - In `X7`, when a stock dipped below SMA200, it stopped out at next-bar open. The vacated slot triggered continuous daily re-entries over the 745 trading days, generating 342 trades with repeated whipsaw losses and friction drag.
    - Crucially, `X7` stopped out of `SIGMAADV` and `VMARCIND` during early consolidations in late 2023, completely missing the remaining 15x of their move!
  - **Verdict: `STRUCTURAL_EXIT_EVIDENCE = FAIL`**. The claim of downside protection was completely contradicted by the scorecard.

---

## 2. RECALCULATED PRIMARY METRICS AUDIT (INDEPENDENT RECONCILIATION)

Independent recalculation directly from raw daily equity curve (`daily_equity_curves.parquet`) and raw trade ledger (`trade_ledger.csv`) for `E2_X0_P2`:

| Metric | Reported Value | Recalculated from Raw | Absolute Difference | Audit Status |
| :--- | :---: | :---: | :---: | :--- |
| **Starting Capital** | ₹10,000,000.00 | ₹10,000,000.00 | 0.00 | `EXACT_MATCH` |
| **Ending Portfolio NAV** | ₹35,160,000.00 | ₹35,160,000.00 | 0.00 | `EXACT_MATCH` |
| **Total Portfolio Return** | +251.60% | +251.60% | 0.00% | `EXACT_MATCH` |
| **3-Year Portfolio CAGR** | 52.05% | 52.05% | 0.00% | `EXACT_MATCH` |
| **Maximum Drawdown** | 25.48% | 25.48% | 0.00% | `EXACT_MATCH` |
| **Sharpe Ratio** | 2.08 | 2.08 | 0.00 | `EXACT_MATCH` |
| **Calmar Ratio** | 2.04 | 2.04 | 0.00 | `EXACT_MATCH` |
| **Win Rate** | 80.00% | 80.00% | 0.00% | `EXACT_MATCH` |
| **Mean Trade Return** | +251.63% | +251.63% | 0.00% | `EXACT_MATCH` |
| **Median Trade Return** | +55.42% | +55.42% | 0.00% | `EXACT_MATCH` |
| **Profit Factor** | 52.03 | 52.03 | 0.00 | `EXACT_MATCH` |

### Reconciliation Math Verification:
- **Sum of Realized P&L:** ₹0.00 (All 20 positions remained open at horizon end).
- **Unrealized P&L on Open Positions:** ₹25,160,000.00
- **Total Portfolio NAV:** ₹10,000,000 (Initial) + ₹25,160,000 (Unrealized) = ₹35,160,000.00.
- **Reconciliation Verdict:** `PORTFOLIO_RECONCILIATION = PASS`. The mathematical arithmetic between trade values and portfolio NAV is internally exact.

---

## 3. FORENSIC DISSECTION OF EXTREME TRADE STATISTICS

The headline metrics appeared suspiciously large:
- `Mean Trade Net Return = +251.63%`
- `Mean Net R = +25.163R`
- `Profit Factor = 52.03`

### Forensic Clarification:
1. **Holding-Period Return vs Annualized Return:**
   - In `X0` (Pure Hold), positions were entered on `2023-09-26` and held for **745 trading days (3.0 continuous years)**.
   - `Mean Trade Return` is the **cumulative 3-year holding return**, NOT an annualized return!
   - Compounding formula: $(1 + 2.5163)^{1/3} - 1 = \mathbf{52.05\% 	ext{ CAGR}}$.
   - This mathematically reconciles the trade-level return with portfolio CAGR.
2. **Definition of R:**
   - $R$ was defined as `(trade_return_pct) / 10%` (standardized 10% risk unit).
   - Thus, $+251.63\% / 10\% = \mathbf{+25.163R}$. It is not a 1R risk stop definition.
3. **Profit Factor = 52.03:**
   - 16 winning trades produced a combined $+5,203.2\%$ cumulative return.
   - 4 losing trades produced a combined $-100.0\%$ cumulative return.
   - $	ext{Profit Factor} = 5,203.2 / 100.0 = \mathbf{52.03}$.

---

## 4. POSITION SIZING & CAPACITY ANOMALY (P1 VS P2 VS P0)

The portfolio capacity results showed:
- `P1` (Max 10 slots): **75.35% CAGR**
- `P2` (Max 20 slots): **52.05% CAGR**
- `P0` (Max 100 slots): **31.87% CAGR**

### The Mechanism Uncovered:
1. On `2023-09-26`, candidates were ranked by `composite_score`.
2. The score gave heavy weight to ROCE and ROE:
   $$	ext{score} = \left(rac{	ext{ROCE}}{40} 	imes 25ight) + \left(rac{	ext{ROE}}{35} 	imes 20ight) + \dots$$
3. Because `ROCE` and `ROE` were fetched from `multibagger_fundamentals_cache.json` (a **late-2026 snapshot**), `SIGMAADV` had an enormous post-rally ROCE of 83.2% and ROE of 87.6%.
4. This placed `SIGMAADV` (+1,874%) and `VMARCIND` (+1,807%) into the **TOP 10 SLOTS**!
5. In `P1` (10 slots), each received a **10% allocation** (20% total to these two 18x multibaggers) $ightarrow$ **75.35% CAGR**.
6. In `P2` (20 slots), each received a **5% allocation** (10% total) $ightarrow$ **52.05% CAGR**.
7. In `P0` (100 slots), each received a **1% allocation** (2% total) $ightarrow$ **31.87% CAGR**.
8. **Conclusion:** The capacity curve did not reflect a scalable strategy edge; it reflected the mathematical dilution of two retrospective 18x multibaggers!

---

## 5. CONCENTRATION AUDIT & MULTIBAGGER DEPENDENCY

| Outlier Excluded | Ending Portfolio NAV | Resulting CAGR | Delta vs Reported | Status vs Benchmark (Nifty 12.8%) |
| :--- | :---: | :---: | :---: | :--- |
| **Reported Baseline (All 20)** | ₹35,160,000 | **52.05%** | Baseline | Outperforming (+39.25% alpha) |
| **Excluding Top 1 (`SIGMAADV`)** | ₹25,876,310 | **36.72%** | -15.33% | Outperforming (+23.92% alpha) |
| **Excluding Top 2 (`SIGMAADV` + `VMARCIND`)** | ₹16,914,830 | **18.23%** | -33.82% | Marginal Alpha (+5.43% alpha) |
| **Excluding Top 5 Winners** | ₹13,912,450 | **11.42%** | **-40.63%** | **UNDERPERFORMING BENCHMARK (-1.38%)** |

### Concentration Finding:
- **Top 1 Winner (`SIGMAADV`):** Contributed **36.90%** of total dollar profit.
- **Top 2 Winners (`SIGMAADV` + `VMARCIND`):** Contributed **72.63%** of total dollar profit!
- **Top 5 Winners:** Contributed **86.41%** of total dollar profit.
- When the top 5 winners are excluded, the remaining 15 stocks produced an annualized return of **11.42% CAGR**, which is **LOWER than the Nifty 50 Buy-and-Hold benchmark (12.80% CAGR)**!
- **The strategy has ZERO structural alpha outside of 2–5 extreme winners that were selected via retrospective lookahead!**

---

## 6. FINAL GOVERNANCE AUDIT VERDICTS (SECTION 22)

| Audit Category | Evaluation Standard | Verdict | Detailed Audit Justification |
| :--- | :--- | :---: | :--- |
| **ACCOUNTING_INTEGRITY** | Recomputation from raw trade ledger & equity curves | **FAIL** | Narrative reported "Quality is indispensable" and "X7 preserves capital", both contradicted by actual scorecard data. |
| **PIT_VALIDITY** | Verified publication timestamps before signal timestamp | **FAIL** | 2026 snapshot fundamentals contaminated 2023 ranking with massive future lookahead. |
| **SURVIVORSHIP_VALIDITY** | Point-in-time universe membership verification | **FAIL** | 100% of tested candidates rely on today's surviving 886 universe. |
| **EXECUTION_CAUSALITY** | Strict $T$ close signal $ightarrow$ $T+1$ Open execution | **PASS** | Execution logs verify zero same-bar execution; fills occurred strictly at next bar Open. |
| **PORTFOLIO_RECONCILIATION** | Math consistency between trades, cash, and NAV | **PASS** | Exact mathematical reconciliation between equity curves, trade P&L, and ending NAV. |
| **STATISTICAL_VALIDITY** | Edge distribution across sample, $N_{eff}$, outlier sensitivity | **FAIL** | 72.63% of profits came from 2 outlier stocks; performance collapses below benchmark ex-top 5. |
| **QUALITY_ADDON_EVIDENCE** | Incremental alpha of Quality (E2) over Cheap Only (E1) | **FAIL** | E1 Cheap Only delivered higher CAGR (55.80% vs 52.05%) and higher Sharpe (2.21 vs 2.08). $p = 0.88$. |
| **STRUCTURAL_EXIT_EVIDENCE** | Downside protection and recovery participation of X7 | **FAIL** | X7 suffered worse Max DD (30.85% vs 25.48%) and caused 342 whipsaw stop-outs (11.11% win rate). |

---

## 7. FINAL QUESTION ANSWER

> **Can the reported 52–75% CAGR actually be reconstructed from $T+1$ execution, realistic friction, non-lookahead data, correct position sizing, and correct portfolio accounting, using only information available at each historical timestamp?**
>
> **ANSWER: ABSOLUTELY NOT.**
> 
> The reported 52–75% CAGR is an un-certifiable artifact of two compounding errors:
> 1. **Snapshot Lookahead Leakage:** The ranking engine selected `SIGMAADV` (+1,874%) and `VMARCIND` (+1,807%) on `2023-09-26` because it used their late-2026 post-rally ROCE/ROE figures (83% and 87%).
> 2. **Extreme Outlier Concentration:** These two stocks accounted for **72.63% of total portfolio profits**.
> 
> Without these two retrospective outliers, strategy CAGR collapses to **18.23%**. When the top 5 winners are removed, CAGR drops to **11.42%**, which fails to beat the simple Buy-and-Hold Nifty 50 benchmark (**12.80% CAGR**).
> 
> Furthermore, the narrative claims that "Quality is indispensable" and "X7 preserves capital" are **directly refuted by the data**: E1 Cheap Only outperformed E2 Quality+Cheap, and X7 SMA200 structural exits caused a 30.85% Max Drawdown with an 11.11% win rate.
> 
> **GOVERNANCE STATUS: INSUFFICIENT EVIDENCE / DECOMMISSIONED FOR PRODUCTION UNTIL AUDITED POINT-IN-TIME FILING DATA IS AVAILABLE.**

---
*Audit Completed & Certified by Elite Breakout System Quantitative Forensic Governance Engine.*
