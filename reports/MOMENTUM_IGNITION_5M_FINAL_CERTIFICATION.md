# MOMENTUM_IGNITION_5M — FINAL CERTIFICATION & GOVERNANCE AUDIT

> [!IMPORTANT]
> **OFFICIAL GOVERNANCE DECISION**: `RESEARCH ONLY`  
> **STRATEGY STATUS**: `CANDIDATE ARCHITECTURE`  
> **PRODUCTION / PAPER TRADING GATE**: **BLOCKED / HELD**  
> **MANDATORY INVARIANT CHECK**: 95% Bootstrap CI Lower Bound > 0.0R $\implies$ **FAILED (Crosses Zero)**

---

## 1. Executive Summary & Audit Mandate
Under the canonical 5-Gate Governance Standard, `MOMENTUM_IGNITION_5M` was subjected to adversarial disproof on verified Upstox V3 market data.

The goal was **NOT** to force the strategy into production, but to establish whether genuine forward alpha survives:
1. Strict point-in-time executable fill (T+1 Open + 5 bps).
2. Elimination of unfillable bar-close lookahead.
3. Factor ablation testing ($p < 0.05$ and $d > 0.20$).
4. Frozen parameter evaluation on an untouched 15% forward holdout.

---

## 2. Baseline Tournament & Factor Attribution Summary

### A. 6-Baseline Tournament Results
| Baseline | N Events | Peak MFE [95% CI] | MAE | +30m Ret | Exp (R) | PF |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **B1: Price Only** | 199 | 0.74% [0.65–0.86] | -0.85% | -0.09% | -0.202R | 0.71 |
| **B2: Price + Volume** | 174 | 0.76% [0.65–0.88] | -0.86% | -0.07% | -0.150R | 0.77 |
| **B3: Price + Vol + VWAP** | 141 | 0.72% [0.6–0.85] | -0.79% | -0.05% | -0.114R | 0.81 |
| **B4: Price + Vol + Struct**| 137 | 0.81% [0.68–0.95] | -0.86% | -0.06% | -0.129R | 0.80 |
| **B5: Candidate (P+V+S+VWAP)**| 120 | 0.76% [0.63–0.9] | -0.79% | -0.04% | -0.085R | 0.85 |
| **B6: B5 + OI Drop** | 39 | 0.85% [0.58–1.15] | -0.82% | +0.07% | +0.169R | 1.30 |

### B. Incremental Factor Attribution Summary
* **Volume Lift (B2 vs B1)**: RVOL >= 2.0x improves MFE from 0.74% to 0.76% and lifts Profit Factor from 0.71 to 0.77.
* **Structure Lift (B4 vs B2)**: CLV >= 0.65 and wick filtering filters out 21% of noisy bars, improving peak MFE from 0.76% to 0.81%.
* **VWAP Gate (B5 vs B4)**: Proximity to VWAP (<= 1.20%) clamps maximum adverse excursion and prevents extended chasing.
* **Open Interest (B6 vs B5)**: OI contraction filters out 68% of signals without achieving statistical significance (p = 0.3769, d = 0.1683). **OI is explicitly excluded from mandatory rules.**

---

## 3. Final Untouched Forward Holdout Results (Section 30)

| Metric | Realized Value on 15% Forward Holdout |
| :--- | :--- |
| **Holdout Sample Size** | **12 trades** |
| **Realized Win Rate** | **25.0%** |
| **Realized Expectancy** | **-0.375R** |
| **95% Bootstrap CI** | **[-1.000R, +0.250R]** |
| **CI Lower Bound ($CI_{low} > 0.0\text{R}$)** | **FAILED — Lower bound dips negative** |

---

## 4. Final Governance Audit Decision: `RESEARCH ONLY`

### Why `MOMENTUM_IGNITION_5M` Cannot Be Promoted Yet:
1. The realized point expectancy in the untouched forward holdout was **-0.375R**, and the **lower bound of the 95% bootstrap confidence interval crosses zero (-1.000R)**.
2. The core governance invariant states: **"If $CI_{low} \le 0.0\text{R} \implies$ REMAIN RESEARCH ONLY."**
3. Therefore, no production code, schedulers, or alert dispatchers are permitted to be changed.

### Certified Next Steps for Promotion:
* Ingest an expanded 6-month historical Upstox V3 dataset to increase the holdout trade sample ($N \ge 150$).
* Re-evaluate the single-run forward holdout.
* Only when $CI_{low} > +0.05\text{R}$ consistently may `MOMENTUM_IGNITION_5M` advance to paper trading.
