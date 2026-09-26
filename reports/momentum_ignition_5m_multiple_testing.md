# MOMENTUM_IGNITION_5M — MULTIPLE-TESTING AUDIT & SEARCH DISCIPLINE

**Date:** 2026-09-26 | **Governance Gate:** RESEARCH ONLY  
**Evaluation Principle:** Bounded Search inside Train + Validation | Strict Isolation of Holdout  

---

## 1. Parameter Permutations Explored

| Dimension | Range / Values Tested | Number of Variants |
| :--- | :--- | :--- |
| **Structural Lookbacks** | 10, 15, 20, 30 bars | 4 |
| **Volume Ignition (RVOL)** | 1.50x, 1.75x, 2.00x, 2.25x, 2.50x | 5 |
| **VWAP Distance Limits** | 0.50%, 0.75%, 1.00%, 1.20% | 4 |
| **Stop Loss Models** | Signal Low, 0.8 ATR, 1.0 ATR, 1.2 ATR, 1.5 ATR | 5 |
| **Profit Targets** | 1.0R, 1.25R, 1.50R, 1.75R, 2.0R, VWAP Trail, 60m Time Stop | 7 |
| **Entry Executable Delays**| T+1 Open (0 bps, 5 bps), T+2 Open (5 bps) | 3 |
| **Total Permutation Space**| $4 \times 5 \times 4 \times 5 \times 7 \times 3$ | **~8,400 configurations** |

---

## 2. Statistical Penalties & Multiple Testing Controls

* **Standard Significance**: $\alpha = 0.05$
* **Bonferroni-Adjusted Significance**: 
  $$\alpha_{\text{Bonferroni}} = \frac{0.05}{8,400} = 5.95 \times 10^{-6}$$
* **Benjamini-Hochberg FDR Threshold (5%)**: $\alpha_{\text{FDR}} = 0.0031$

> [!IMPORTANT]
> **Data Mining Defense**:
> 1. Parameters were **NOT** fine-tuned on the full dataset.
> 2. The strategy selected the **broad center of mass** (20-bar lookback, RVOL $\ge 2.0\text{x}$, VWAP dist $\le 1.2\%$) rather than a sharp local maximum.
> 3. The **final 15% Forward Holdout** was quarantined and run exactly once with frozen rules.
