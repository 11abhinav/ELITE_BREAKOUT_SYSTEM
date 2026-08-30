# Failure Anatomy Report: MULTIBAGGER (Base Accumulation)

**Report Generated:** 2026-08-30 20:05:56 IST  
**Strategy Family:** MULTIBAGGER (Base Accumulation)  

---

## 1. Scanner-Specific Quality Dimensions

The following dimensions represent the primary distinguishing factors between high-quality breakouts and false positives:

- **Base Duration**
- **Volatility Contraction**
- **Volume Expansion**
- **OBV Trend**
- **Breakout Transition**

---

## 2. Winning vs Losing Alert Signatures

| Setup Category | Distinguishing Signatures | Target Economic Behavior |
|---|---|---|
| **High-Quality Winning Setups** | Strong multi-bar consolidation, healthy volume expansion, aligned macro/sector tailwind. | MFE $\ge 2.0\text{R}$, swift target progression, low MAE ($< 0.5\text{R}$). |
| **High-Score False Positives (Confidently Wrong)** | Late-stage extended momentum, volume climax without continuation, immediate overhead resistance. | High pre-breakout score but rapid reversal into SL. |
| **Low-Score False Negatives (Missed Winners)** | Tight quiet contraction, low visible pre-breakout volume, contrarian sector setup. | Low pre-breakout score that subsequently explodes into $+3\text{R}$ run. |

---

## 3. Diagnostic Roadmap & Candidate Mechanisms
- **Operating Status:** `815 of 816 records uninitialized targets. Rehydration required before sample accumulation.`
- **Next Quality Mechanism:** Establish simplest effective mechanism (Ranking, Gating, or Sizing Modifier) and compare on the identical production population.