# MULTIBAGGER Quality Discovery & Tier Evaluation Report

**Report Generated:** 2026-08-30 20:26:30 IST  
**Scanner Scope:** `MULTIBAGGER` (Base Accumulation & Volume Expansion Breakout)  
**Sample Evaluated:** $N = 33$ scale-verified independent base breakouts across 33 distinct NSE equities  
**Production Code Status:** **100% UNTOUCHED (Zero Mutations)**  

---

## 1. Executive Summary & Payoff Dynamics

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ MULTIBAGGER EMPIRICAL BASELINE (N = 33 Scale-Verified Outcomes, 33 Symbols)     │
├───────────────────────────────┬─────────────────────────────────────────────────┤
│ Baseline Gross Mean E[R]      │ +0.222R                                         │
│ Baseline Net Mean E[R]        │ +0.172R (post-0.05R transaction friction)       │
│ Realized Target Hit Rate (T1) │ 12.1% (4 / 33 reached full 3.0R Target)         │
│ Mean Maximum Fav. Excursion   │ +1.51R                                          │
│ Mean Maximum Adv. Excursion   │ 0.74R                                           │
└───────────────────────────────┴─────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Governance Designation:** `IN_SAMPLE_RANKING_SIGNAL`  
> The candidate mechanism **`AQS_ACCUM_v1`** demonstrated strong in-sample tier separation. To prevent overfitting, the formula and weights ($0.6 / 0.4$) are now **FROZEN** and will NOT be re-tuned on this sample. It will be validated on unseen out-of-sample and forward events.

---

## 2. In-Sample Tier Separation & 2x2 Classification Matrix

$$\text{AQS\_ACCUM} = 50 + 15 \cdot \left[ 0.6 \cdot z(\text{PRE\_RSI\_14}) + 0.4 \cdot z(-\text{BASE\_WIDTH}) \right]$$

### A. Tier Breakdown Across 33 Equities

| Quality Tier | Sample $N$ | Net Mean $E[R]$ | Realized Win Rate | Mean MFE | Mean MAE | Max Tail Loss | Payoff Assessment |
|---|---|---|---|---|---|---|---|
| **`TOP_33PCT` (Tier 1)** | **11** | **+0.322R** | **18.2%** | **1.96R** | 0.76R | -1.05R | **Alpha Expansion ($\Delta = +0.150\text{R}$ vs baseline)** |
| **`MID_33PCT` (Tier 2)** | **11** | **+0.245R** | **9.1%** | **1.27R** | 0.59R | -1.05R | **Positive Expectancy Retention** |
| **`BOTTOM_33PCT` (Filtered)**| **11** | **-0.082R** | **9.1%** | **1.29R** | 0.87R | -1.05R | **NEGATIVE EXPECTANCY (Filter Target)** |

### B. 2x2 Classification Table (Top+Mid Retained vs. Bottom Filtered)

```
                            CLASSIFICATION MATRIX (N = 33)
                  ┌───────────────────────────┬───────────────────────────┐
                  │  Top+Mid Retained (N=22)  │   Bottom Filtered (N=11)  │
┌─────────────────┼───────────────────────────┼───────────────────────────┤
│ Losers (N = 29) │            19             │            10             │
├─────────────────┼───────────────────────────┼───────────────────────────┤
│ Winners (N = 4) │             3             │             1             │
└─────────────────┴───────────────────────────┴───────────────────────────┘
```

- **Winner Retention:** $\mathbf{75.0\%}$ ($3 / 4$ winners retained: `ACUTAAS`, `360ONE`, `ANUP`).
- **Opportunity Cost:** 1 winner filtered (`ANANDRATHI`).
- **Loser Recall:** $\mathbf{34.5\%}$ ($10 / 29$ false breakouts filtered).
- **Economic Impact:**
  - Full Population Baseline ($N=33$): $+0.172\text{R}$ Net.
  - Retained Subpopulation ($N=22$): $\mathbf{+0.281\text{R}}$ Net ($\Delta = \mathbf{+0.109\text{R}}$).
  - Filtered Subpopulation ($N=11$): $\mathbf{-0.082\text{R}}$ Net.

---

## 3. Top Failure Signatures in False Breakouts

1. **Deep Base Undercut / Institutional Liquidation ($\text{MAE} > 1.0\text{R}$):** `ALKYLAMINE`, `AARTIPHARM`.
2. **Stalled Breakout / Lack of Momentum Follow-Through ($\text{MFE} < 0.80\text{R}$):** `ADANIPORTS`, `ADANIENT`, `ABB`, `ANGELONE`.
3. **Excessive Base Width / Overhead Supply ($\text{Base Width} > 4.5$):** `ADANIPORTS` (4.96), `ANTHEM` (4.91).

---

## 4. Frozen Candidate Status & Promotion Track

```
  33-Event In-Sample Discovery
               │
               ▼
   FREEZE AQS_ACCUM_v1 (LOCKED)
               │
               ▼
   Out-of-Sample Holdout Testing
               │
               ▼
  >= 50 Diverse Forward Events
               │
               ▼
   Production Promotion Review
```
