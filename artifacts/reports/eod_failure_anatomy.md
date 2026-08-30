# Failure Anatomy Report: EOD (Daily Breakout)

**Report Generated:** 2026-08-30 20:05:56 IST  
**Strategy Family:** EOD (Daily Breakout)  

---

## 1. Scanner-Specific Quality Dimensions

The following dimensions represent the primary distinguishing factors between high-quality breakouts and false positives:

- **Trend (dist SMA50/SMA200)**
- **Momentum (RSI)**
- **Volume Ratio**
- **Sector Status**
- **Macro Regime**

---

## 2. Winning vs Losing Alert Signatures

| Setup Category | Distinguishing Signatures | Target Economic Behavior |
|---|---|---|
| **High-Quality Winning Setups** | Strong multi-bar consolidation, healthy volume expansion, aligned macro/sector tailwind. | MFE $\ge 2.0\text{R}$, swift target progression, low MAE ($< 0.5\text{R}$). |
| **High-Score False Positives (Confidently Wrong)** | Late-stage extended momentum, volume climax without continuation, immediate overhead resistance. | High pre-breakout score but rapid reversal into SL. |
| **Low-Score False Negatives (Missed Winners)** | Tight quiet contraction, low visible pre-breakout volume, contrarian sector setup. | Low pre-breakout score that subsequently explodes into $+3\text{R}$ run. |

---

## 3. Diagnostic Roadmap & Candidate Mechanisms
- **Operating Status:** `26 clean non-zero geometry replays (all RELIANCE, +1.100R net). 44 mock zero-target records excluded.`
- **Next Quality Mechanism:** Establish simplest effective mechanism (Ranking, Gating, or Sizing Modifier) and compare on the identical production population.