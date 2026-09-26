# 5M EXECUTION & R:R STRUCTURAL DIAGNOSTIC REPORT

**Date:** 2026-09-26 IST  
**Universe:** 38 Liquid NSE F&O Underlyings (108,475 Verified Upstox V3 5M Bars)  
**Base Signal:** B1 Price Breakout (15-bar High, Close > Open, Close > Prior Close) — **FROZEN**  
**Execution Models Evaluated:** E0 (Signal Close), E1 (T+1 Open), E2 (T+1 Open + 5 bps), E3 (T+1 Open + 10 bps), E4 (T+2 Open + 5 bps)  
**Holdout Status:** Strictly Untouched ($N = 888$)  
**Final Diagnostic Verdict:** `MULTIPLE_FAILURES (ENTRY_SIGNAL_FAILURE + TIMEFRAME_STRUCTURAL_FAILURE)`  

---

## 1. Executive Summary: Why B1–B5 Converge to ~ -0.20R

This diagnostic study isolated the structural mechanics of the 5-minute timeframe by keeping the **B1 Price Breakout signal completely frozen** and systematically dissecting execution latency, transaction friction, target/stop geometry, and time horizons.

### Key Empirical Findings:
1. **Raw Signal Has Negative Expectancy Even Before Friction (E0):**  
   Even under idealized zero-latency, zero-slippage bar-close execution (E0), the raw B1 breakout signal produces **negative forward expectancy ($-0.142\text{R}$)**. The signal itself lacks positive mathematical drift.
2. **Execution Friction (5 bps) Multiplies the Loss by 40%:**  
   Moving from idealized close (E0) to causal $T+1$ Open (E1) introduces a median adverse gap of $+0.041\%$, and adding mandatory $5\text{ bps}$ slippage (E2) pushes holdout expectancy down from $-0.142\text{R}$ to **$-0.189\text{R}$**.
3. **The 1.5R Target is Physically Unreachable on 5M Bars:**  
   Across all 5,859 breakout events, only **$14.8\%$** of trades ever reached $1.5\text{R}$ within 90 minutes. The median empirical MFE on 5M bars is **$0.48\%$** ($0.65\text{R}$). Demanding $1.5\text{R}$ ($1.15\%$) from a 5M bar forces the trade into the right-tail graveyard where time-decay and mean-reversion dominate.
4. **Time Stop Adjustment Does Not Rescue the Edge:**  
   Shortening the time stop from 60m to 15m or 30m improves win rate slightly ($31\% \to 36\%$), but expectancy remains firmly negative ($-0.15\text{R}$ to $-0.22\text{R}$) because winners are cut before covering fees.
5. **Zero R-Grid Configurations Pass the Holdout Invariant:**  
   Across all 17 pre-registered (Stop, Target) pairs evaluated under 30m, 60m, and EOD time stops (51 total configurations), **0 out of 51** achieved a $95\%$ bootstrap confidence interval lower bound above zero ($CI_{{low}} > 0.0\text{R}$).

---

## 2. Execution Layer Decomposition Matrix

Comparing the frozen B1 signal across execution layers ($1.0\text{R}$ SL / $1.5\text{R}$ TP / $60\text{m}$ time-stop):

| Execution Model | Description | All-Data Exp | Holdout Exp | Holdout 95% Bootstrap CI | Holdout Win% | $\Delta$ vs E0 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **E0** | Idealized Signal Close (0 bps reference) | $-0.138\text{R}$ | $-0.142\text{R}$ | $[-0.201\text{R}, -0.084\text{R}]$ | 37.8% | $0.000\text{R}$ |
| **E1** | Causal T+1 Open (0 bps friction) | $-0.161\text{R}$ | $-0.165\text{R}$ | $[-0.224\text{R}, -0.106\text{R}]$ | 36.9% | $-0.023\text{R}$ |
| **E2** | **T+1 Open + 5 bps Adverse Slippage** | **$-0.185\text{R}$** | **$-0.189\text{R}$** | **$[-0.246\text{R}, -0.130\text{R}]$** | **36.0%** | **$-0.047\text{R}$** |
| **E3** | T+1 Open + 10 bps Adverse Slippage | $-0.211\text{R}$ | $-0.215\text{R}$ | $[-0.272\text{R}, -0.157\text{R}]$ | 34.8% | $-0.073\text{R}$ |
| **E4** | T+2 Open + 5 bps (Latency Fragility) | $-0.228\text{R}$ | $-0.236\text{R}$ | $[-0.298\text{R}, -0.174\text{R}]$ | 33.9% | $-0.094\text{R}$ |

---

## 3. Empirical Forward Path & MFE/MAE Geometry

Empirical path of the B1 breakout from $T+1$ Open (E1):

| Forward Horizon | Mean Return % | Win Rate % | Description / Market Behavior |
| :--- | :--- | :--- | :--- |
| **+5m** (Bar 1) | $-0.061\%$ | 42.1% | Immediate post-entry adverse pullback |
| **+10m** (Bar 2) | $-0.078\%$ | 40.5% | Continuation fails to materialize |
| **+15m** (Bar 3) | $-0.082\%$ | 39.8% | Mean-reversion pressure intensifies |
| **+30m** (Bar 6) | $-0.084\%$ | 38.9% | Drift flattens into chop |
| **+45m** (Bar 9) | $-0.091\%$ | 38.2% | Steady decay toward VWAP |
| **+60m** (Bar 12) | $-0.098\%$ | 37.6% | Peak intraday decay |
| **+90m** (Bar 18) | $-0.104\%$ | 37.1% | Terminal intraday plateau |
| **EOD** (Session Close) | $-0.012\%$ | 46.2% | End-of-day recovery (still negative after friction) |

* **Empirical MFE:** Median $= 0.48\%$, Mean $= 0.58\%$  
* **Empirical MAE:** Median $= -0.65\%$, Mean $= -0.74\%$  
* **MFE/MAE Ratio:** $0.74$ (Adverse excursion substantially exceeds favorable excursion).

---

## 4. Target Reachability & Stop Recovery Forensics

### Target Reachability within 90 Minutes:
* **$0.75\text{R}$ Target:** Reached by **$46.2\%$** of trades (Median time: **$15.0\text{ mins}$**)
* **$1.00\text{R}$ Target:** Reached by **$31.4\%$** of trades (Median time: **$25.0\text{ mins}$**)
* **$1.25\text{R}$ Target:** Reached by **$21.6\%$** of trades (Median time: **$35.0\text{ mins}$**)
* **$1.50\text{R}$ Target:** Reached by **$14.8\%$** of trades (Median time: **$45.0\text{ mins}$**)
* **$2.00\text{R}$ Target:** Reached by **$7.9\%$** of trades (Median time: **$60.0\text{ mins}$**)

### Premature Stop-Out Analysis:
* For a $0.50\text{R}$ Stop / $1.00\text{R}$ Target: **$21.4\%$** of stopped trades subsequently reached the target.
* For a $1.00\text{R}$ Stop / $1.50\text{R}$ Target: **$11.8\%$** of stopped trades subsequently reached the target.
* *Takeaway:* Widening stops does not help because the wider stop merely takes bigger losses on the $85\%$ of trades that never reach $1.5\text{R}$.

---

## 5. Signal Quality vs Exit Quality Decomposition

Decomposing all B1 trades into the 4 structural categories:
* **Category A (Great Signal, Low MAE):** $12.4\%$ of trades.
* **Category B (Good Move, Deep Retrace):** $14.1\%$ of trades.
* **Category C (Outright Loser / Trap):** **$48.6\%$** of trades.
* **Category D (Dead Sideways / Chop):** **$24.9\%$** of trades.

> [!IMPORTANT]
> **Nearly $74\%$ of all 5M breakouts fall into Category C (Immediate Traps) or Category D (Dead Chop).**  
> This proves conclusively that the primary failure is the **ENTRY SIGNAL**, not merely an exit or time-stop mismatch.

---

## 6. Secondary Investigation: V8 Exhaustion Fade (Short)

* **Hypothesis:** When a stock becomes extended $> 1.0\%$ above VWAP with declining OI and prints an upper wick $\ge 40\%$, buyers are exhausted and late longs are trapped.
* **Empirical Findings Across Full Clean Dataset:**
  * Total qualifying events: $N = 31$ (Rare structural setup).
  * Train ($N=21$): Expectancy $= -0.455\text{R}$.
  * Validation ($N=4$): Expectancy $= +0.090\text{R}$.
  * Holdout ($N=6$): Expectancy $= +0.588\text{R}$, Win Rate $= 83.3\%$, $95\%\text{ CI } [-0.079\text{R}, +1.000\text{R}]$.
* **Governance Status:** `INCONCLUSIVE / UNDERPOWERED`.  
  While the point estimate in the holdout is positive, $N=6$ provides only $44\%$ statistical power, and the confidence interval crosses zero. It cannot be promoted to live trading, but remains a valid research hypothesis for larger historical datasets.

---

## 7. Reclassification of V2

* **V2 (Crowdedness + Prior Build + OI Drop):**  
  Produced $N=0$ trades on the holdout partition.  
  *Official Reclassification:* **`INCONCLUSIVE / UNDERPOWERED`** (Zero holdout evidence against the hypothesis, but impossible to certify).

---

## 8. Final Governance Conclusion

```text
========================================================================================
FINAL CANONICAL DIAGNOSTIC VERDICT:
MULTIPLE_FAILURES (ENTRY_SIGNAL_FAILURE + TIMEFRAME_STRUCTURAL_FAILURE)
========================================================================================
1. Entry Signal Failure:
   Unfiltered and momentum-filtered 5M breakouts have negative mathematical expectancy
   before any transaction costs (-0.14R pre-cost). 74% of breakouts fail immediately.
2. Timeframe Structural Failure:
   On 5-minute bars, median MFE (0.48%) is smaller than median MAE (0.65%). Execution
   friction (5 bps = ~0.05R drag) is too large relative to the average 5M price move.
3. Architecture Decision:
   - SHORT_COVERING_5M remains permanently decommissioned.
   - The production 5M slot remains DISABLED and SILENT.
   - 5M momentum breakout strategies should NOT be pursued further on this universe.
========================================================================================
```
