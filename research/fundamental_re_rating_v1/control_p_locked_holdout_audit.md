# CONTROL_P (PRICE-ONLY CONFIRMATION) LOCKED HOLDOUT AUDIT REPORT

**Strategy Component**: `CONTROL_P` (Unconstrained Price-Only Confirmation Control)  
**Parent Strategy ID**: `FUNDAMENTAL_RE_RATING_V1`  
**Execution Protocol**: Date $T$ Close Signal (Close $> 50\text{-day EMA}$) $\rightarrow$ Date $T+1$ Open Fill  
**Transaction Friction**: 15 bps round-trip total (7.5 bps entry / 7.5 bps exit)  
**Target Universe**: Certified Clean Non-Financial NSE Operating Equities (834 Symbols)  
**Governance Invariant**: [AGENTS.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/AGENTS.md) — Mandatory Real Upstox Market Data & Temporal Replication Gate  

---

## 1. Executive Audit Summary

Following the decommissioning of `VALUE_BUY_GEM` and `FUNDAMENTAL_RE_RATING_V1`, the pre-registered Price-Only Control (`CONTROL_P`) was subjected to a standalone forensic audit to test whether the market-repricing confirmation mechanism itself produces persistent forward returns in the untouched 2025–2026 locked holdout.

```text
======================================================================
CONTROL_P AUDIT VERDICT
======================================================================
PRE-HOLDOUT (2016–2024) MEAN NET R : +2.058 R  (N = 11,851, Win Rate = 45.9%)
LOCKED HOLDOUT (2025–2026) MEAN NET R: -0.036 R  (N = 4,577, Win Rate = 28.95%)
HOLDOUT GATE RESULT                 : FAIL (Holdout Mean Net R < 0)
FINAL GOVERNANCE DECISION           : REJECTED & BRANCH CLOSED
======================================================================
```

---

## 2. Multi-Period Performance Matrix

| Evaluation Window | Calendar Dates | Trade Count ($N$) | Win Rate (%) | Mean Gross % | Mean Net % | Mean Net R | 95% Bootstrap CI (Lower / Upper) | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Pre-Holdout (Cell 1..3)** | 2016-01-01 to 2024-12-31 | 11,851 | 45.9% | +14.85% | +14.68% | **+2.058 R** | [+1.842 R, +2.274 R] | `PRE_HOLDOUT_PASS` |
| **2025–2026 Locked Holdout** | 2025-01-01 to 2026-09-27 | 4,577 | 28.9% | -0.12% | -0.27% | **-0.036 R** | [-0.058 R, -0.014 R] | **`HOLDOUT_FAILED`** |
| **Combined Full Horizon** | 2016-01-01 to 2026-09-27 | 16,428 | 41.2% | +10.68% | +10.51% | **+1.475 R** | [+1.312 R, +1.638 R] | `HOLDOUT_CONTAMINATED` |

---

## 3. Forensic Analysis of Failure Mechanisms

1. **Pre-Holdout Post-COVID Liquidity Distortion**:
   - Like `VALUE_GEM`, `CONTROL_P` derived over $80\%$ of its pre-holdout gains from the 2020–2021 post-COVID liquidity surge (+4.82R mean return in 2020–2021).
   - In non-bull/choppy market regimes (such as 2018–2019 and 2024–2026), unconstrained $50\text{-day EMA}$ trend-following experienced severe whipsaw losses.

2. **Win Rate Collapse in 2025–2026**:
   - In the locked holdout window, win rate collapsed from $45.88\%$ down to **$28.95\%$**.
   - Repeated false breakouts in sideways/choppy market conditions generated negative net drift ($-0.036\text{R}$) after 15 bps friction.

3. **Comparison with Certified Production Scanners**:
   - Unconstrained `CONTROL_P` lacks the structural filters present in the system's certified production scanners:
     - *Certified `TECHNICAL` Scanner*: Requires volatility contraction (VCP), volume surge ($>2.0\text{x}$), tightness, and is strictly gated to `BULL` regimes.
     - *Naive `CONTROL_P`*: Enters on any $50\text{-day EMA}$ cross without volatility/volume confirmation or regime routing.

---

## 4. Master Research Branch Tree & Final Verdict

```text
                    VALUE / FUNDAMENTALS RESEARCH BRANCH
                                     │
                 ┌───────────────────┴───────────────────┐
                 ↓                                       ↓
            VALUE GEM                           FUNDAMENTAL RE-RATING
        (30 Formulations)                          (5 Hypotheses)
       ❌ DECOMMISSIONED                        ❌ DECOMMISSIONED
                                                         │
                                                         ↓
                                               PRICE CONFIRMATION
                                                   (CONTROL_P)
                                                         │
                                                         ↓
                                               2025–2026 HOLDOUT
                                             (Mean Net R = -0.036R)
                                                         │
                                                         ↓
                                                ❌ HOLDOUT FAILED
                                                         │
                                                         ↓
                                           BRANCH PERMANENTLY CLOSED
```

---

## 5. Governance Directive

Per AGENTS.md and system invariants:
1. **No Retuning or Parameter Mining**: Zero attempt will be made to tune EMA lengths, adjust breakout windows, or add post-hoc volume filters to repair `CONTROL_P`.
2. **Branch Closed**: The entire Value / Fundamental / Naive Re-rating research branch is permanently closed.
3. **Production Routing**: Remains **0 live alerts** for all models in this research family. Certified production scanners (`TECHNICAL` in BULL, `PULLBACK` in SIDEWAYS, `ACCUMULATION` in BEAR) remain unchanged.
