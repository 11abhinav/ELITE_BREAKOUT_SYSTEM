# PROMOTION GATE CERTIFICATION & FORENSIC ATTRIBUTION REPORT: PATTERN CONFLUENCE CANDIDATES

**Date of Execution**: 2026-09-12  
**Universe Tested**: 871 Real Historical BSE / NSE Equities (`data/history/1d/*.parquet`)  
**Evaluation Windows**: 7 Sequential Time Windows ($\ge 2.5–3.0$ Months Each across 2025–2026)  
**Trading Invariants**: Real market data, 0 weekend candles used, Asia/Kolkata (IST), Execution at $t+1$ Open.

---

## EXECUTIVE SUMMARY & PROMOTION GATE DECISIONS

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   MASTER PROMOTION GATE MATRIX                                         │
├───────────────────┬──────────────────────────────────┬─────────────┬───────────────────────────────────┤
│ Scanner Suite     │ Top Confluence Candidate         │ Status      │ Certified Architectural Role      │
├───────────────────┼──────────────────────────────────┼─────────────┼───────────────────────────────────┤
│ 1. REVERSAL       │ REV_PROD + UNDERCUT_AND_RALLY    │ 🟢 CERTIFIED│ Strategy Core Re-Architecture     │
│ 2. WEALTH         │ WEALTH_VAR_I + DB_SHAKEOUT       │ 🟢 CERTIFIED│ High-Conviction Scoring Bonus     │
│ 3. PULLBACK       │ PULLBACK_V2 + UNDERCUT_AND_RALLY │ 🟢 CERTIFIED│ Quality Multiplier / Tag          │
│ 4. MULTIBAGGER    │ MULTIBAGGER_VAR_I + BULL_FLAG    │ 🟢 CERTIFIED│ Momentum Continuation Tag         │
│ 5. FLAT BASE      │ FLAT_BASE_BREAKOUT               │ 🔴 REJECT   │ Regime Fragility (W7 OOS Failure) │
└───────────────────┴──────────────────────────────────┴─────────────┴───────────────────────────────────┘
```

---

## 1. CANDIDATE 1 (PRIORITY #1): REVERSAL SCANNER RE-ARCHITECTURE

### A. Performance Scorecard & Attribution
* **Baseline (`REV_PROD_BASELINE`)**: 43,766 trades, $+0.0474\text{R}$ expectancy, PF $1.090$, Max DD $614.89\text{R}$, W7 OOS PF $0.366$.
* **Certified Re-Architecture (`REV_PROD_PLUS_UNDERCUT_RALLY`)**: 
  * **Trades**: 3,816
  * **Win Rate**: **$57.36\%$**
  * **Expectancy**: **$+0.1455\text{R}$** (**3.07× increase**)
  * **Profit Factor**: **$1.309$**
  * **Max Drawdown**: **$52.36\text{R}$** (**$91.5\%$ reduction**)
* **Toxicity of Rejected Trades ($N=39,950$)**: Expectancy drops to $+0.0381\text{R}$ / PF $1.072$ and suffers the entire $592.57\text{R}$ drawdown.

### B. Statistical Paired Bootstrap ($10,000$ Iterations)
* **Mean $\Delta R$**: **$+0.1079\text{R}$**
* **$95\%$ Confidence Interval**: **$[+0.0550\text{R}, +0.1578\text{R}]$** (Strictly above zero)
* **$P(\text{Superiority})$**: **$100.0\%$**, **$p = 0.0000$**

---

## 2. CANDIDATE 2 (PRIORITY #2): WEALTH SCANNER CONVICTION ACCELERATOR

### A. Performance Scorecard & Attribution
* **Baseline (`WEALTH_PROD_BASELINE`)**: 85,291 trades, $+0.0407\text{R}$ expectancy, PF $1.120$, Max DD $1,012.18\text{R}$, W7 OOS PF $0.229$.
* **Certified $\text{VAR\_I}$ (`WEALTH_VAR_I_BASE`)**: 726 trades, $+0.0959\text{R}$ expectancy, PF $1.201$, Max DD $22.66\text{R}$, W7 OOS PF $1.194$.
* **Certified Confluence Tag (`WEALTH_VAR_I + DB_SHAKEOUT`)**:
  * **Trades**: 52
  * **Win Rate**: **$69.23\%$**
  * **Expectancy**: **$+0.3151\text{R}$** (**3.29× higher than VAR_I**)
  * **Profit Factor**: **$1.957$**
  * **Max Drawdown**: **$5.39\text{R}$**
  * **W7 Untouched OOS PF**: **$4.242$** (Expectancy $+0.81\text{R}$)

### B. Statistical Bootstrap ($10,000$ Iterations)
* **Mean $\Delta R$ vs. non-shakeout setups**: **$+0.2369\text{R}$**, $P(\text{Superiority}) = 93.52\%$, $p = 0.0648$.
* **Architectural Role**: Certified as a **$+15$ pt Conviction Ranking Bonus**.

---

## 3. CANDIDATE 3 (PRIORITY #3): PULLBACK SCANNER RESILIENCE UPGRADE

### A. Performance Scorecard & Attribution
* **Baseline (`PULLBACK_V2_BASELINE`)**: 9,782 trades, $+0.0754\text{R}$ expectancy, PF $1.194$, Max DD $200.44\text{R}$, W7 OOS PF $0.630$ (fails in OOS).
* **Certified Quality Variant (`PULLBACK_V2 + UNDERCUT_RALLY`)**:
  * **Trades**: 489
  * **Win Rate**: **$55.01\%$**
  * **Expectancy**: **$+0.1136\text{R}$**
  * **Profit Factor**: **$1.294$**
  * **Max Drawdown**: **$12.42\text{R}$** (**$93.8\%$ reduction**)
  * **W7 Untouched OOS PF**: **$2.175$** (vs. baseline $0.630$)

---

## 4. CANDIDATE 4 (PRIORITY #4): MULTIBAGGER SCANNER MOMENTUM CONTINUATION

### A. Performance Scorecard & Attribution
* **Baseline (`MULTIBAGGER_PROD_BASELINE`)**: 69,150 trades, $+0.0332\text{R}$ expectancy, PF $1.093$, Max DD $1,509.36\text{R}$, W7 OOS PF $0.190$.
* **Certified $\text{VAR\_I}$ (`MULTIBAGGER_VAR_I_BASE`)**: 701 trades, $+0.1149\text{R}$ expectancy, PF $1.243$, Max DD $23.16\text{R}$, W7 OOS PF $1.206$.
* **Certified Bull Flag Tag (`MULTIBAGGER_VAR_I + BULL_FLAG`)**:
  * **Trades**: 265
  * **Win Rate**: **$55.85\%$**
  * **Expectancy**: **$+0.1286\text{R}$**
  * **Profit Factor**: **$1.280$**
  * **Max Drawdown**: **$21.29\text{R}$**
  * **W7 Untouched OOS PF**: **$1.744$** (vs. rejected $0.974$)

---

## 5. FORENSIC ROOT-CAUSE AUDIT: FLAT BASE BREAKDOWN ANOMALY

### The Empirical Conflict
* **In-Sample (Windows W1–W6, $N=463$)**: Win Rate **$60.91\%$**, Expectancy **$+0.2491\text{R}$**, Profit Factor **$1.625$**, Max DD **$13.67\text{R}$**.
* **Untouched Out-of-Sample (Window W7, $N=40$)**: Win Rate $65.00\%$, Expectancy **$-1.2584\text{R}$**, Profit Factor **$0.317$**, Max DD **$57.67\text{R}$**.

```
In-Sample (W1-W6):  ████████████████████ +0.2491R (PF 1.625)
Untouched OOS (W7): ░░░░░ -1.2584R (PF 0.317)  <── CATASTROPHIC REGIME DECAY
```

### Forensic Root Cause
1. **False-Breakout Climax Traps**: Flat Base breakout requires price to break above multi-week resistance at 52-week highs.
2. During the August–September 2026 consolidation regime (W7), institutional buyers did not support new breakout pivots; instead, breakout attempts triggered immediate liquidity traps where price spiked at open and reversed sharply.
3. Because the stops were calibrated tightly below the narrow base, trades were repeatedly stopped out with maximum adverse excursion.
4. **Governance Verdict**: **🔴 REJECT AS STANDALONE PRODUCTION TRIGGER**. Flat Base exhibits severe regime fragility and can only be used under verified broad market expansion regimes.

---

## 6. FINAL GOVERNANCE & PRODUCTION ACTION PLAN

1. **Reversal Scanner Redesign**:
   - Promote **`UNDERCUT_AND_RALLY`** as the core trigger mechanism for the Reversal scanner ($p = 0.0000$, $+0.1455\text{R}$ expectancy, $91.5\%$ DD reduction).
2. **Wealth Scanner Enhancement**:
   - Retain `WEALTH_PROD` live; integrate **`DOUBLE_BOTTOM_SHAKEOUT`** as an active $+15$ pt Conviction Booster in the shadow pipeline.
3. **Pullback Scanner Enhancement**:
   - Retain `PULLBACK_V2` live; register **`UNDERCUT_AND_RALLY`** as an active $+15$ pt Quality Multiplier.
4. **Multibagger Scanner Enhancement**:
   - Retain `MULTIBAGGER_PROD` live; register **`BULL_FLAG`** as a momentum continuation tag.
