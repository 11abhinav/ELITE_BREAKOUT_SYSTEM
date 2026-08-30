# PULLBACK Quality Discovery Report — Failure Anatomy & Baseline Quality Mechanism

**Report Generated:** 2026-08-30 20:26:30 IST  
**Scanner Scope:** `PULLBACK` (Trend Retracement & Continuation)  
**Sample Population:** $N = 50$ scale-verified independent pullback triggers across 50 distinct NSE equities  
**Production Code Status:** **100% UNTOUCHED (Zero Mutations)**  

---

## 1. Executive Summary & Payoff Dynamics

The `PULLBACK` engine identifies orderly retracements within established uptrends designed to enter continuation swings with asymmetric $2.5\text{R}$ targets.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PULLBACK EMPIRICAL BASELINE (N = 50 Scale-Verified Outcomes, 50 Symbols)        │
├───────────────────────────────┬─────────────────────────────────────────────────┤
│ Baseline Gross Mean E[R]      │ +0.258R                                         │
│ Baseline Net Mean E[R]        │ +0.208R (post-0.05R transaction friction)       │
│ Realized Target Hit Rate (T1) │ 22.0% (11 / 50 reached full 2.5R Target)        │
│ Mean Maximum Fav. Excursion   │ +1.13R                                          │
│ Mean Maximum Adv. Excursion   │ 0.52R                                           │
└───────────────────────────────┴─────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **PULLBACK Economic Profile:**  
> With a $22.0\%$ target hit rate and controlled adverse excursions ($0.52\text{R}$ average MAE), the scanner exhibits positive baseline expectancy ($+0.208\text{R}$).  
> The objective of quality optimization is to filter out shallow chop and deep trend breakdowns while preserving orderly sweet-spot retracements.

---

## 2. Winner vs. Failure Anatomy

### A. The Anatomy of Orderly Trend Continuations ($N = 11$ Hits)
1. **The "Sweet-Spot" Pullback Depth ($6.0\% - 12.0\%$):**
   - Examples: `MARSONS` ($9.7\%$), `HINDZINC` ($8.4\%$), `TIMEX` ($8.9\%$).
   - Pattern: Retraces cleanly into institutional support without damaging the intermediate trend.
2. **Controlled Drawdown ($\text{MAE} \le 0.45\text{R}$):** Rebounds quickly upon touching the support band.
3. **High Excursion Velocity ($\text{MFE} \ge 2.5\text{R}$):** Achieves full target within 4–7 trading days.

### B. Top 3 Failure Signatures
1. **Signature 1: Shallow Noise Pullback ($\text{Depth} < 4.5\%$):**
   - Example: `HINDCOPPER` ($3.79\%$).
   - Pattern: The pullback was premature noise; price subsequently rolled over into a deeper selloff.
2. **Signature 2: Deep Structural Trend Breakdown ($\text{Depth} > 16.0\%$):**
   - Example: `SKMEGGPROD` ($24.6\%$).
   - Pattern: What appeared to be a pullback was actually a catastrophic trend reversal/distribution.
3. **Signature 3: Volume Exhaustion on Rebound:**
   - Pattern: Volume dry-up on the attempted recovery pivot.

---

## 3. Candidate Quality Mechanism: `AQS_PULLBACK_v1`

$$\text{AQS\_PULLBACK} = 50 + 15 \cdot \left[ 0.6 \cdot \text{Depth\_Fit} + 0.4 \cdot z(\text{Volume\_Rebound}) \right]$$

Where $\text{Depth\_Fit} = 1.0 - \frac{|\text{Pullback\_Depth} - 8.5\%|}{8.5\%}$.

### Governance Status:
- **Designation:** `CANDIDATE_MECHANISM` (in-sample discovery).
- **Next Phase:** Freezing candidate formula for out-of-sample holdout validation and forward shadow tracking.
