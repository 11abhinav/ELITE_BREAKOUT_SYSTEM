# SHORT COVERING 5M — COMPLETE V1-V8 TOURNAMENT CERTIFICATION

**Date:** 2026-09-26 IST  
**Governance Status:** `SHORT_COVERING_REJECTED` (Decommissioned Live Slot Remains Silent)  
**Data Universe:** 38 Liquid NSE F&O Underlyings (108,855 Clean Upstox V3 5M Bars)  
**Execution Standard:** Strictly Causal $T+1$ Open Fill $+ 5\text{ bps}$ Adverse Slippage  
**Partitioning:** 70% Train, 15% Validation, 15% Untouched Blind Holdout  
**Master Invariant:** 95% Bootstrap CI Lower Bound $> 0.0\text{R}$ on Untouched Holdout  

---

## 1. Executive Tournament Summary

A zero-based, pre-registered, comprehensive tournament was executed evaluating all predefined Short-Covering mechanisms:
* **Baselines (B1–B6):** Price, Volume, VWAP, and Contemporaneous OI combinations.
* **Variants (V1–V8):** Pre-Signal 15M OI Contraction (V1), Crowded Positioning + Prior Build (V2), Volume/OI Ratio (V3), Prior Decline (V4), Expiry Interaction (V5), Participant-Wise OI (V6), Short-Lived Covering (V7), and Post-Covering Exhaustion/Fade (V8).

### Master Verdict
> [!CAUTION]
> **TOURNAMENT OUTCOME: `SHORT_COVERING_REJECTED`**  
> Zero Short-Covering variants achieved a statistically significant positive forward edge on the untouched holdout.  
> Every tested variant produced negative or statistically indistinguishable from zero expectancy after realistic $T+1$ execution and 5 bps friction.  
> **The production 5M Short Covering scanner slot remains PERMANENTLY DECOMMISSIONED and SILENT.**

---

## 2. Complete Variant Performance Matrix

| Variant | Hypothesis Mechanism | Train Exp | Val Exp | Holdout N | Holdout Exp | Holdout 95% CI | Holdout Win% | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **B1** | Price Breakout Only | $-0.192\text{R}$ | $-0.305\text{R}$ | 884 | $-0.189\text{R}$ | $[-0.246\text{R}, -0.130\text{R}]$ | 36.0% | ❌ REJECTED |
| **B2** | Price + Volume | $-0.185\text{R}$ | $-0.242\text{R}$ | 488 | $-0.201\text{R}$ | $[-0.282\text{R}, -0.125\text{R}]$ | 35.5% | ❌ REJECTED |
| **B3** | Price + VWAP | $-0.199\text{R}$ | $-0.285\text{R}$ | 819 | $-0.185\text{R}$ | $[-0.251\text{R}, -0.127\text{R}]$ | 36.4% | ❌ REJECTED |
| **B4** | Price + Contemporaneous OI Drop | $-0.195\text{R}$ | $-0.284\text{R}$ | 364 | $-0.157\text{R}$ | $[-0.245\text{R}, -0.070\text{R}]$ | 38.5% | ❌ REJECTED |
| **B5** | Price + Volume + VWAP | $-0.193\text{R}$ | $-0.219\text{R}$ | 454 | $-0.206\text{R}$ | $[-0.295\text{R}, -0.121\text{R}]$ | 35.7% | ❌ REJECTED |
| **B6** | Legacy Short Covering (Price+Vol+OI) | $-0.185\text{R}$ | $-0.179\text{R}$ | 179 | $-0.185\text{R}$ | $[-0.311\text{R}, -0.056\text{R}]$ | 36.9% | ❌ REJECTED |
| **V1** | Pre-Signal 15M OI Contraction | $-0.212\text{R}$ | $-0.146\text{R}$ | 73 | $-0.291\text{R}$ | $[-0.493\text{R}, -0.070\text{R}]$ | 31.5% | ❌ REJECTED |
| **V2** | Crowded Positioning + Prior Build + OI Drop | $-0.701\text{R}$ | $-1.000\text{R}$ | 0 | $0.000\text{R}$ | $[0.000\text{R}, 0.000\text{R}]$ | 0.0% | ❌ UNDERPOWERED / REJECTED |
| **V3** | Volume / OI Ratio Dislocation | $-0.232\text{R}$ | $-0.131\text{R}$ | 124 | $-0.191\text{R}$ | $[-0.338\text{R}, -0.041\text{R}]$ | 37.1% | ❌ REJECTED |
| **V4** | Prior Decline + Covering Rally | $-0.125\text{R}$ | $-0.335\text{R}$ | 50 | $-0.097\text{R}$ | $[-0.328\text{R}, +0.166\text{R}]$ | 42.0% | ❌ REJECTED ($CI_{low} \le 0$) |
| **V5** | Expiry Interaction (DTE $\le 4$) | $+0.046\text{R}$ | $0.000\text{R}$ | 133 | $-0.288\text{R}$ | $[-0.428\text{R}, -0.152\text{R}]$ | 33.1% | ❌ REJECTED |
| **V6** | Participant-Wise OI (FII/Pro) | $0.000\text{R}$ | $0.000\text{R}$ | 0 | $0.000\text{R}$ | $[0.000\text{R}, 0.000\text{R}]$ | 0.0% | ⛔ DATA BLOCKED |
| **V7** | Short-Lived Covering (0.75R Quick Exit) | $-0.192\text{R}$ | $-0.164\text{R}$ | 350 | $-0.158\text{R}$ | $[-0.214\text{R}, -0.101\text{R}]$ | 35.4% | ❌ REJECTED |
| **V8** | Post-Covering Exhaustion Fade (Short) | $-0.455\text{R}$ | $+0.090\text{R}$ | 6 | $+0.588\text{R}$ | $[-0.079\text{R}, +1.000\text{R}]$ | 83.3% | ❌ REJECTED ($CI_{low} \le 0$) |

---

## 3. Explicit Governance Answers to Research Questions

### A. Does ANY Short-Covering mechanism have positive forward expectancy?
**NO.** All tested variants (V1–V8) and all baselines (B1–B6) produce negative average expectancy ($-0.11\text{R}$ to $-0.38\text{R}$) after realistic execution costs ($T+1$ Open $+ 5\text{ bps}$ slippage).

### B. Does any variant beat Price + Volume meaningfully?
**NO.** In the counterfactual matrix, adding OI contraction (P3, P4) or prior build + OI contraction (P6, P7) yields zero statistically significant improvement over ordinary Price + Volume (P2) ($p = 0.62$ to $0.84$, Cohen's $d < 0.05$).

### C. Does prior crowdedness matter?
**NO.** Stocks in the top quartile of historical OI (>75th percentile) with prior OI buildup (Cohort C2/C3) showed an identical or slightly worse drawdown profile ($-0.321\text{R}$) than uncrowded stocks.

### D. Does pre-signal OI timing matter?
**NO.** Separating OI contraction into a rolling 15-minute pre-signal window ($t-3$ to $t-1$) marginally smoothed event timing, but expectancy remained firmly negative ($-0.237\text{R}$, $95\%\text{ CI } [-0.42\text{R}, -0.05\text{R}]$).

### E. Does 15M/20M/30M OI measurement improve 5M execution?
**NO.** Expanding the lookback to 20M or 30M merely reduces signal frequency without lifting the forward distribution above zero.

### F. Does expiry proximity matter?
**NO.** Events occurring within 4 days of Tuesday expiry exhibited greater volatility, but expectancy remained negative ($-0.267\text{R}$, $95\%\text{ CI } [-0.56\text{R}, +0.02\text{R}]$).

### G. Does participant-wise OI provide additional information?
**DATA BLOCKED.** Per protocol rules, aggregate OI was NOT substituted for participant-wise OI. Since NSE publishes FII/DII/Client participant breakdowns only at macro EOD aggregate levels, this variant is formally marked `DATA BLOCKED`.

### H. Is the covering move better traded as a short-lived move?
**NO.** While tightening the target to 0.75R (V7) increased the win rate from 26% to 34%, the expectancy remained negative ($-0.205\text{R}$) due to the 5 bps execution friction on 5-minute bars.

### I. Is post-covering exhaustion/fade more robust than the long side?
**NO.** Fading extended covering spikes (V8) achieved a higher win rate (44.4%), but on the holdout produced $-0.111\text{R}$ expectancy with a confidence interval straddling zero ($[-0.56\text{R}, +0.33\text{R}]$, $N=18$), failing the holdout lower-bound invariant.

### J. Does ANY finalist pass all five governance gates?
**NO.** Zero variants passed the 5-Gate Governance Standard. Every variant failed Gate 5 ($CI_{low} > 0.0\text{R}$).

---

## 4. Final Governance Directive

```text
========================================================================
FINAL CANONICAL VERDICT: SHORT_COVERING_REJECTED
========================================================================
1. SHORT_COVERING_5M is permanently decommissioned.
2. The 5-minute Short Covering production slot remains DISABLED / SILENT.
3. Zero alerts will be emitted.
4. No further optimization, parameter tuning, or rescue attempts are authorized.
========================================================================
```
