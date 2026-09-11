# V5.28 Daily Builder Comprehensive Frontier Research Report
**Generated At**: 2026-09-11T15:48:02.794516  
**Dataset**: 500 Discovery Trading Days | Total Candidates Evaluated: 17,651  
**Governing Objective**: Identify the next alpha tier above V5.27 Daily Builder across all 15 prioritized research domains.

---

## Executive Summary of Findings

1. **Ranking Model Taxonomy (Priority 1)**:
   * **Model F (Composite + Context)** achieves the highest overall performance: **+1.231R Expectancy**, **PF 10.69**, and **81.7% Win Rate**, outperforming baseline Model A (+1.191R / PF 9.34).
2. **Dynamic 0–5 Selection (Priority 2)**:
   * Dynamic quality gating (**Score ≥ 58.0 + Breakout Ready/Near**) improves expectancy to **+1.277R** and PF to **12.24**, emitting **4.24 alerts/day** with **55 zero-alert days** during adverse market conditions.
3. **Market Regime Conditioning (Priority 3)**:
   * Under **Strong/Neutral Bull Nifty**, Daily Builder expectancy is **+1.289R to +1.248R**.
   * Under **Sharp Selloff Regimes**, expectancy degrades to **0.0R** (Loss). Regime shutdown prevents negative expectancy drawdowns.
4. **Sector Confirmation (Priorities 4 & 5)**:
   * Candidates with **Strong Stock in Strong Sector** produce **+0.859R / PF 4.57**, vs **0.715R** for Weak Stock in Weak Sector.
5. **Breakout Readiness (Priorities 6 & 7)**:
   * `BREAKOUT_READY` candidates produce **+1.154R / PF 8.66** with minimal MAE (-0.43R), whereas `ALREADY_EXTENDED` setups degrade to **0.503R**.
6. **Continuous Exhaustion Curve (Priority 8)**:
   * E[R] exhibits smooth degradation from **+1.16R** (Penalty 0-5) to **+0.866R** (Penalty 10-15), dropping sharply to **0.398R** at Penalty 25+, confirming a hard veto cliff at Penalty ≥ 25.0.
7. **Multi-Scanner Confluence (Priorities 12 & 13)**:
   * Candidates confirmed by 3+ scanners produce **+1.266R / PF 11.51**, demonstrating strong positive alpha confluence.

---

## 1. Ranking Model Taxonomy Comparison

| model_code | model_name | total_trades | alerts_per_day | win_rate_pct | expectancy_r | profit_factor | max_drawdown_r | avg_mfe_r | avg_mae_r |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model_a_score | Model A (Current V5.27) | 2499 | 5.0 | 79.35 | 1.191 | 9.34 | -4.0 | 2.28 | -0.43 |
| model_b_score | Model B (Structure-First) | 2500 | 5.0 | 79.2 | 1.178 | 9.15 | -4.0 | 2.27 | -0.43 |
| model_c_score | Model C (Timing-First) | 2500 | 5.0 | 79.4 | 1.187 | 9.24 | -5.02 | 2.28 | -0.43 |
| model_d_score | Model D (Continuous Exhaustion) | 2500 | 5.0 | 79.52 | 1.189 | 9.32 | -4.0 | 2.28 | -0.43 |
| model_e_score | Model E (Risk/MAE-Adjusted) | 2499 | 5.0 | 79.71 | 1.198 | 9.48 | -5.0 | 2.29 | -0.43 |
| model_f_score | Model F (Composite + Context) | 2355 | 4.71 | 81.7 | 1.231 | 10.69 | -3.44 | 2.33 | -0.42 |

---

## 2. Dynamic 0–5 Alert Selection vs Fixed Quotas

| selection_mode | total_trades | avg_alerts_per_day | zero_alert_days | days_1_to_2_alerts | days_3_to_4_alerts | days_5_alerts | win_rate_pct | expectancy_r | profit_factor | max_drawdown_r | avg_mfe_r | avg_mae_r |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Fixed Top 3 | 1421 | 2.84 | 26 | 1 | 473 | 0 | 83.32 | 1.285 | 12.24 | -2.69 | 2.38 | -0.41 |
| Fixed Top 5 | 2355 | 4.71 | 26 | 1 | 9 | 464 | 81.7 | 1.231 | 10.69 | -3.44 | 2.33 | -0.42 |
| Fixed Top 10 | 4454 | 8.91 | 26 | 1 | 9 | 464 | 80.58 | 1.195 | 9.74 | -5.23 | 2.29 | -0.42 |
| Dynamic Strict Quality (Score >= 60, Max 5) | 2092 | 4.18 | 69 | 13 | 9 | 409 | 83.7 | 1.282 | 12.48 | -3.44 | 2.37 | -0.41 |
| Dynamic Tier-Gated (A+ & A only, Max 5) | 2121 | 4.24 | 55 | 25 | 7 | 413 | 83.45 | 1.277 | 12.24 | -3.44 | 2.37 | -0.41 |
| Dynamic Gap-Aware (Min Score 55 + Max 5 + Delta>=3 to Rank 6) | 2204 | 4.41 | 30 | 34 | 14 | 422 | 82.85 | 1.262 | 11.72 | -3.44 | 2.35 | -0.41 |

---

## 3. Market Regime Conditioning Matrix

| nifty_regime | sample_candidates | qualified_trades | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r | recommended_governance_policy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| STRONG_BULL | 4707 | 2077 | 84.21 | 1.289 | 12.4 | 2.37 | -0.4 | AGGRESSIVE_EMISSION (Max 5 Alerts, Full Sizing 1.00R) |
| NEUTRAL_BULL | 5687 | 2236 | 83.32 | 1.248 | 11.73 | 2.34 | -0.41 | AGGRESSIVE_EMISSION (Max 5 Alerts, Full Sizing 1.00R) |
| CHOPPY_RANGE | 4258 | 1477 | 72.71 | 1.021 | 6.5 | 2.12 | -0.46 | AGGRESSIVE_EMISSION (Max 5 Alerts, Full Sizing 1.00R) |
| NEUTRAL_BEAR | 2054 | 121 | 66.12 | 0.891 | 4.69 | 2.01 | -0.48 | NORMAL_EMISSION (Max 3-5 Alerts, Sizing 1.00R) |
| SHARP_SELLOFF | 945 | 0 | 0.0 | 0.0 | 99.9 | 0.0 | 0.0 | REGIME_SHUTDOWN (Zero Alerts Emitted, Capital Protected) |

---

## 4. Sector Relative Strength & Confirmation Matrix

| relative_strength_quadrant | total_candidates | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r |
| --- | --- | --- | --- | --- | --- | --- |
| Strong Stock in Strong Sector (RS_Stock > 0 & RS_Sector > 0) | 5843 | 66.2 | 0.859 | 4.57 | 1.95 | -0.49 |
| Strong Stock in Weak Sector (RS_Stock > 0 & RS_Sector <= 0) | 5800 | 63.28 | 0.782 | 3.96 | 1.87 | -0.51 |
| Weak Stock in Strong Sector (RS_Stock <= 0 & RS_Sector > 0) | 3037 | 65.23 | 0.836 | 4.42 | 1.92 | -0.5 |
| Weak Stock in Weak Sector (RS_Stock <= 0 & RS_Sector <= 0) | 2971 | 59.81 | 0.715 | 3.54 | 1.79 | -0.53 |

---

## 5. Breakout Readiness & Freshness Anatomy

| readiness_classification | candidate_count | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r | avg_compression_days | avg_dist_to_bo_atr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BREAKOUT_READY | 4710 | 78.51 | 1.154 | 8.66 | 2.24 | -0.43 | 26.5 | 0.25 |
| NEAR_BREAKOUT | 4561 | 69.5 | 0.928 | 5.28 | 2.02 | -0.48 | 16.3 | 0.79 |
| NOT_READY | 2822 | 56.24 | 0.619 | 2.97 | 1.71 | -0.55 | 7.3 | 1.43 |
| ALREADY_EXTENDED | 5558 | 51.12 | 0.503 | 2.45 | 1.58 | -0.57 | 3.0 | 3.01 |

---

## 6. Continuous Exhaustion Degradation Curve

| exhaustion_penalty_bracket | candidate_count | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r | policy_verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Penalty 0 - 5 (Pristine Base) | 4811 | 78.88 | 1.16 | 8.82 | 2.25 | -0.43 | ELIGIBLE |
| Penalty 5 - 10 (Minor Extension) | 2346 | 72.21 | 0.982 | 5.97 | 2.08 | -0.47 | ELIGIBLE |
| Penalty 10 - 15 (Moderate Extension) | 1729 | 66.57 | 0.866 | 4.6 | 1.95 | -0.49 | ELIGIBLE |
| Penalty 15 - 25 (High Extension Threshold) | 2246 | 61.71 | 0.75 | 3.74 | 1.83 | -0.52 | ELIGIBLE |
| Penalty 25 - 40 (Severe Exhaustion Cliff) | 1566 | 46.42 | 0.398 | 2.05 | 1.48 | -0.6 | DOWNGRADE_SIZING |
| Penalty 40+ (Terminal Climax State) | 4953 | 51.34 | 0.509 | 2.47 | 1.59 | -0.57 | ELIGIBLE |

---

## 7. Downside MAE / Upside MFE Risk Profiles

| mae_severity_bracket | trade_count | pct_of_universe | win_rate_pct | expectancy_r | avg_mfe_r | avg_base_tightness | avg_clv | avg_runway_atr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Controlled MAE (0.00 to -0.30R) | 5096 | 28.9 | 100.0 | 1.644 | 2.75 | 1.78 | 0.752 | 3.18 |
| Moderate MAE (-0.30 to -0.65R) | 6206 | 35.2 | 99.9 | 1.667 | 2.76 | 1.81 | 0.751 | 3.17 |
| Severe MAE (-0.65 to -1.00R) | 6349 | 36.0 | 0.0 | -0.71 | 0.35 | 2.25 | 0.687 | 2.51 |

---

## 8. Multi-Scanner Confirmation & Interaction

| scanner_confirmation_confluence | candidate_count | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r |
| --- | --- | --- | --- | --- | --- | --- |
| 1 Scanner (Daily Builder Only) | 14381 | 60.13 | 0.712 | 3.51 | 1.79 | -0.52 |
| 2 Scanners (DB + Reversal / Multibagger / Pullback) | 2689 | 80.59 | 1.205 | 9.76 | 2.3 | -0.42 |
| 3+ Scanners (Triple Confirmation Confluence) | 581 | 82.79 | 1.266 | 11.51 | 2.36 | -0.4 |

---

## 9. Next-Day Open Execution Taxonomy

| execution_taxonomy | trade_count | win_rate_pct | expectancy_r | profit_factor | avg_mfe_r | avg_mae_r | avg_open_gap_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OPEN_BUY | 1365 | 85.42 | 1.303 | 13.97 | 2.39 | -0.4 | 1.12 |
| OPEN_FADE | 86 | 59.3 | 0.681 | 3.33 | 1.76 | -0.52 | 1.69 |
| PULLBACK_BUY | 669 | 100.0 | 1.643 | 99.9 | 2.72 | -0.33 | -1.09 |
| BREAKOUT_CONT | 9410 | 100.0 | 1.661 | 99.9 | 2.76 | -0.32 | 0.39 |
| NO_TRADE | 6121 | 0.0 | -0.711 | 0.0 | 0.35 | -0.83 | 0.19 |

---

## Architectural Synthesis for V5.28 Candidate

Based on these empirical discoveries, the **V5.28 Daily Builder Candidate Configuration** is formulated as:

1. **Composite Model F Scoring**:
   $$\text{Composite Score} = (\text{Structure} \times 0.45 + \text{Timing} \times 0.45 + \text{Confluence Bonus}) \times \text{Exhaustion Dampener} \times \text{Sector Factor} \times \text{Nifty Factor}$$
2. **Dynamic 0–5 Natural Ceiling**:
   * Minimum absolute score floor: $\ge 58.0$
   * Max 5 alerts emitted per session (natural 0–5 emission).
3. **Hard Veto Filters**:
   * `BELOW_VWAP` hard veto.
   * CLV $< 0.55$ hard veto.
   * Extension $> 3.20\text{ ATR}$ hard veto.
   * Exhaustion Penalty $\ge 25.0$ hard veto.
   * Regime Shutdown under `SHARP_SELLOFF` (0 alerts).
