# SHORT_COVERING_DAILY — FINAL TOURNAMENT CERTIFICATION REPORT

**Date:** 2026-09-26 IST  
**Strategy Family:** `SHORT_COVERING_DAILY`  
**Governance Status:** `OI_NOT_INCREMENTAL / INCONCLUSIVE` (Research Only)  
**Universe:** 38 Liquid NSE F&O Underlyings (Clean Upstox V2/V3 Daily Futures Data)  
**Execution Standard:** Strictly Causal $T+1$ Open Fill $+ 5\text{ bps}$ Adverse Slippage  
**Partitioning:** 70% Train, 15% Validation, 15% Untouched Blind Holdout  
**Master Invariant:** 95% Bootstrap CI Lower Bound $> 0.0\text{R}$ on Untouched Holdout  

---

## 1. Executive Tournament Summary

The **SHORT_COVERING_DAILY** tournament evaluated whether multi-session positioning unwinds combined with price response produce a distinct, statistically certified trading edge beyond ordinary price momentum.

### Master Verdict
> [!CAUTION]
> **TOURNAMENT OUTCOME: `OI_NOT_INCREMENTAL` (Zero Variants Passed Promotion Standard)**  
> * **Zero variants** met the mandatory 5-Gate Promotion standard on the untouched forward holdout ($CI_{{low}} > 0.0\text{R}$).
> * **Counterfactual analysis proves that OI contraction does NOT add statistically significant value over ordinary Price + Volume momentum** ($p = 0.53$ to $0.89$, Cohen's $d < 0.10$).
> * **The 5-minute Short Covering production slot remains PERMANENTLY DECOMMISSIONED and SILENT.**
> * `SHORT_COVERING_DAILY` remains strictly **RESEARCH ONLY**.

---

## 2. Complete Performance Matrix (Baselines B1–B3 & Variants D1–D10)

| Code | Variant & Mechanism | Train Exp | Val Exp | Holdout N | Holdout Exp | Holdout 95% Bootstrap CI | Holdout Win% | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **B1** | Price Momentum Only | $-0.509\text{R}$ | $-0.404\text{R}$ | 16 | $-0.377\text{R}$ | $[-0.827\text{R}, +0.092\text{R}]$ | 25.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **B2** | Price + Volume ($\text{RVOL} \ge 1.5$) | $-0.607\text{R}$ | $-0.605\text{R}$ | 4 | $-0.375\text{R}$ | $[-1.000\text{R}, +0.875\text{R}]$ | 25.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **B3** | Price + Trend/Reclaim ($\text{Close} > \text{EMA20}$) | $-0.492\text{R}$ | $-1.000\text{R}$ | 10 | $-0.654\text{R}$ | $[-1.000\text{R}, -0.154\text{R}]$ | 10.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **D1** | Price + OI Contraction | $-0.562\text{R}$ | $+0.036\text{R}$ | 8 | $-0.460\text{R}$ | $[-1.000\text{R}, +0.307\text{R}]$ | 25.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **D2** | Price + High Prior OI (Crowding $\ge 75$th) | $-0.509\text{R}$ | $-0.404\text{R}$ | 10 | $-0.099\text{R}$ | $[-0.750\text{R}, +0.629\text{R}]$ | 40.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **D3** | Core SC: Prior Build + OI Drop + Price $\uparrow$ | $-0.507\text{R}$ | $+0.381\text{R}$ | 3 | $+0.439\text{R}$ | $[-1.000\text{R}, +1.500\text{R}]$ | 66.7% | ❌ UNDERPOWERED ($CI_{low} \le 0$) |
| **D4** | D3 + Volume ($\text{RVOL} \ge 1.5$) | $-1.000\text{R}$ | $0.000\text{R}$ | 1 | $+1.500\text{R}$ | $[+1.500\text{R}, +1.500\text{R}]$ | 100.0% | ❌ SEVERELY UNDERPOWERED ($N=1$) |
| **D5** | D3 + Relative Strength ($\text{RS} > 0$) | $-0.507\text{R}$ | $+0.381\text{R}$ | 3 | $+0.439\text{R}$ | $[-1.000\text{R}, +1.500\text{R}]$ | 66.7% | ❌ UNDERPOWERED ($CI_{low} \le 0$) |
| **D6** | D3 + Level Reclaim ($> 5\text{d High}$) | $-1.000\text{R}$ | $0.000\text{R}$ | 1 | $+1.500\text{R}$ | $[+1.500\text{R}, +1.500\text{R}]$ | 100.0% | ❌ SEVERELY UNDERPOWERED ($N=1$) |
| **D7** | D3 + Expiry Interaction ($\text{DTE} \le 4$) | $0.000\text{R}$ | $0.000\text{R}$ | 0 | $0.000\text{R}$ | $[0.000\text{R}, 0.000\text{R}]$ | 0.0% | ❌ UNDERPOWERED ($N=0$) |
| **D8** | 3-Day Positioning Unwind | $-0.538\text{R}$ | $+0.296\text{R}$ | 5 | $-0.500\text{R}$ | $[-1.000\text{R}, +0.500\text{R}]$ | 20.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **D9** | 5-Day Positioning Unwind | $-0.465\text{R}$ | $-0.177\text{R}$ | 3 | $-1.000\text{R}$ | $[-1.000\text{R}, -1.000\text{R}]$ | 0.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |
| **D10**| 10-Day Structural Unwind | $0.000\text{R}$ | $0.000\text{R}$ | 2 | $-0.305\text{R}$ | $[-1.000\text{R}, +0.389\text{R}]$ | 50.0% | ❌ FAILED HOLDOUT ($CI_{low} \le 0$) |

---

## 3. Explicit Answers to the 20 Governance Questions

### DATA
1. **Is the OI data genuinely verified?**  
   **YES.** All futures daily candles were fetched directly from Upstox API v2 (`/v2/historical-candle/NSE_FO|.../day/...`) with verified 7-element tuples containing real exchange OI. Zero synthetic, simulated, or proxy OI was used.
2. **Is near+next aggregation correctly constructed?**  
   **YES.** $\text{Aggregate OI} = \text{OI}(\text{Near}) + \text{OI}(\text{Next})$ was computed session-by-session using active `SEP` and `OCT` stock futures.
3. **Are contract rollovers correct?**  
   **YES.** Expiries strictly follow the NSE regulatory schedule (Last Tuesday of the month cutover effective August 11, 2026).
4. **Are all observations point-in-time?**  
   **YES.** Day $T$ signals evaluate only data available at or before Day $T$ close. Entry is modeled strictly at Day $T+1$ Open $+ 5\text{ bps}$ adverse slippage.

### MECHANISM
5. **Does price rise + OI decline have positive forward drift?**  
   **NO.** In the holdout, D1 produced $-0.460\text{R}$ expectancy ($95\%\text{ CI } [-1.000\text{R}, +0.307\text{R}]$), which is worse than pure price momentum (B1: $-0.377\text{R}$). In full-sample attribution, D1 vs B1 shows delta expectancy of $+0.062\text{R}$ with $p = 0.7813$ and Cohen's $d = 0.069$ (failing $p < 0.05, d > 0.20$).
6. **Does prior OI crowding matter?**  
   **NO.** D2 produced $-0.099\text{R}$ holdout expectancy with $95\%\text{ CI } [-0.750\text{R}, +0.629\text{R}]$. In counterfactual testing, P2 (Price + High Prior OI) delivered $-0.447\text{R}$ vs P1 (Price only) $-0.471\text{R}$ ($\Delta = +0.024\text{R}, p = 0.89$).
7. **Does prior OI build matter?**  
   **NO.** While D3 showed $+0.439\text{R}$ in the holdout, it was on only $N=3$ trades with $95\%\text{ bootstrap CI } [-1.000\text{R}, +1.500\text{R}]$ ($CI_{low} \le 0$, failing Gate 5). Full-dataset attribution yielded $p = 0.4142$, failing statistical significance.
8. **Does a multi-session OI window improve the signal?**  
   **NO.** Expanding the window from 1D (D1: $-0.460\text{R}$) to 3D (D8: $-0.500\text{R}$), 5D (D9: $-1.000\text{R}$), and 10D (D10: $-0.305\text{R}$) produced uniformly negative holdout expectancies.
9. **Does volume add independent information?**  
   **NO.** D4 generated only $N=1$ holdout event ($+1.500\text{R}$), and only 2 trades across the entire history. Attribution vs D3 yielded $p = 1.0$, failing promotion hurdles.
10. **Does relative strength add independent information?**  
    **NO.** D5 produced identical results to D3 ($\Delta\text{ expectancy} = 0.0\text{R}, p = 1.0$) because qualifying large-cap breakout stocks were already outperforming the benchmark.
11. **Does expiry proximity matter?**  
    **NO.** D7 generated $N=0$ holdout events (underpowered) and fails incremental value testing ($p = 1.0$).
12. **Does level reclaim matter?**  
    **NO.** D6 generated $N=1$ holdout event, and across the entire history delivered negative delta expectancy vs D3 ($-0.261\text{R}, p = 1.0$).

### EXECUTION
13. **Does the edge survive T+1 Open?**  
    **NO.** Raw daily breakout signals have negative expectancy even at pre-cost open.
14. **Does it survive 5 bps?**  
    **NO.** 5 bps friction subtracts an additional $0.023\text{R}$ to $0.028\text{R}$ from each trade.
15. **Does it survive higher slippage?**  
    **NO.** At 10 bps and 15 bps, holdout expectancy decays by a further $0.055\text{R}$ on average.

### ROBUSTNESS
16. **Does it survive symbols?**  
    **NO.** Trades are heavily clustered in high-beta names (DLF, COFORGE) with erratic distribution across the remaining 36 symbols.
17. **Does it survive market regimes?**  
    **NO.** Negative expectancy persisted across both choppy and trending daily periods.
18. **Does it survive parameter perturbation?**  
    **NO.** Across 1D, 3D, 5D, and 10D windows, all holdout expectancies remain negative ($-0.305\text{R}$ to $-1.000\text{R}$).
19. **Does it survive multiple-testing scrutiny?**  
    **NO.** Zero variants pass Bonferroni-corrected alpha ($p < 0.00227$).

### GOVERNANCE
20. **Does ANY candidate pass all five gates?**  
    **NO.** Zero variants achieved $CI_{low} > 0.0\text{R}$ on the untouched holdout.

---

## 4. Final Governance Order

```text
========================================================================================
FINAL CANONICAL VERDICT:
OI_NOT_INCREMENTAL / INCONCLUSIVE (REMAIN RESEARCH ONLY)
========================================================================================
1. Critical Negative-Control Conclusion:
   Ordinary Price Momentum (B1) and Price + Volume (B2) perform equal to or better 
   than all OI-conditioned variants (D1-D10). OI contraction does NOT provide 
   distinct, statistically certified incremental alpha on daily bars.
2. Production Directive:
   - SHORT_COVERING_DAILY is NOT certified for production promotion.
   - Status remains: RESEARCH ONLY.
   - The production Short Covering scanner slot remains PERMANENTLY DISABLED / SILENT.
   - Zero live production alerts will be generated.
========================================================================================
```
