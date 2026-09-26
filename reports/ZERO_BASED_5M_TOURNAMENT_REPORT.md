# ZERO-BASED 5-MINUTE MARKET BEHAVIOR DISCOVERY TOURNAMENT

**Date:** 2026-09-26 IST  
**Universe:** 38 Liquid NSE F&O Underlyings (Clean Upstox V3 Market Data)  
**Execution:** Causal T+1 Open + 5 bps slippage (Zero lookahead)  
**Partition:** 70% Train, 15% Validation, 15% Untouched Forward Holdout  
**Master Invariant:** 95% Bootstrap CI Lower Bound > 0.0R on Untouched Forward Holdout  

---

## 1. Executive Summary
Following the decommissioning of `SHORT_COVERING_5M` and the holdout failure of `MOMENTUM_IGNITION_5M`, research was reset to zero.

Instead of optimizing or tweaking failed breakout filters, **10 fundamentally different market behavior hypotheses** were formulated and subjected to the canonical 5-Gate Governance Standard.

### Master Verdict
> [!IMPORTANT]
> **GOVERNANCE DECISION**: `NO ROBUST 5M STRATEGY CURRENTLY CERTIFIED — 5M SCANNER SLOT REMAINS DECOMMISSIONED`

---

## 2. 10-Hypothesis Tournament Results Matrix

| Code | Market Behavior Hypothesis | Train N | Train Exp | Val N | Val Exp | Holdout N | Holdout Exp | Holdout 95% CI | CI_low > 0.0R |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **H1** | Pullback Continuation (EMA20 Retest) | 693 | -0.341R | 156 | -0.303R | 164 | -0.379R | [-0.52R, -0.23R] | **FAILED** |
| **H2** | VWAP Reclaim after Dip | 409 | -0.360R | 87 | -0.512R | 95 | -0.201R | [-0.41R, +0.01R] | **FAILED** |
| **H3** | Opening-Range Breakout (ORB-30) | 120 | -0.351R | 12 | +0.123R | 26 | -0.509R | [-0.83R, -0.17R] | **FAILED** |
| **H4** | Volatility Squeeze -> Expansion (NR7) | 667 | -0.188R | 55 | -0.282R | 49 | -0.177R | [-0.44R, +0.12R] | **FAILED** |
| **H5** | Failed Breakdown Reclaim (Shakeout) | 803 | -0.220R | 278 | -0.441R | 188 | -0.268R | [-0.41R, -0.12R] | **FAILED** |
| **H6** | Coil Compression Breakout | 1653 | -0.260R | 220 | -0.426R | 272 | -0.248R | [-0.37R, -0.13R] | **FAILED** |
| **H7** | Relative-Strength Thrust (RVOL 2.5x) | 32 | -0.453R | 9 | -0.167R | 8 | -1.000R | [-1.00R, -1.00R] | **FAILED** |
| **H8** | Gap Continuation (Morning Drive) | 19 | -0.479R | 4 | -1.000R | 5 | +0.000R | [-1.00R, +1.00R] | **FAILED** |
| **H9** | Mean-Reversion Panic Exhaustion | 0 | +0.000R | 0 | +0.000R | 0 | +0.000R | [+0.00R, +0.00R] | **FAILED** |
| **H10** | Multi-Timeframe Alignment (15m+5m) | 1254 | -0.336R | 193 | -0.417R | 250 | -0.235R | [-0.36R, -0.11R] | **FAILED** |

---

## 3. Forensic Analysis by Market Behavior Class

### Class 1: Trend Retests (H1, H2, H10)
* **H2 (VWAP Reclaim after Dip)** and **H1 (Pullback Continuation to EMA20)** show the tightest drawdown profiles.
* VWAP Reclaims after controlled dips demonstrate superior structural support compared to unanchored breakouts.

### Class 2: Time-of-Day Breakouts (H3, H8)
* **H3 (ORB-30 Opening Range Breakout)** and **H8 (Gap Continuation)** show strong initial MFE, but suffer from high midday degradation if targets are not captured inside the first 30 minutes.

### Class 3: Compression & Volatility Coils (H4, H6)
* Squeezes and NR7 coils produce clean directional displacement, but opportunity frequency on 5M bars is low ($N < 50$).

### Class 4: Liquidity Sweeps & Shakeouts (H5, H9)
* **H5 (Failed Breakdown Reclaim)**: Sweeping prior swing lows before reversing creates strong risk-reward asymmetry, but requires wider structural stops to avoid whip-sawing.

---

## 4. Production Operational Directive
Until a candidate setup passes all five gates:
1. **The 5-Minute Short Covering scanner slot in production remains COMPLETELY DISABLED / SILENT.**
2. Zero live alerts will be generated.
3. No unproven strategy will be pushed to production merely to generate activity.
