# 04 — STRATEGY ARM COMPARISON REPORT
**Matrix of All Pre-Registered Formulations:**

| Strategy ID | Description | N | N_eff | Holdout Mean | Holdout 95% CI | Adj p | Rolling Pos % | Status |
|---|---|---|---|---|---|---|---|---|
| `ARM_A_E10` | Pre-Event Proximity E-10 (Calendar Notice Retrospective) | 6785 | 4875 | -1.05% | [-1.35%, -0.75%] | 1.0000 | 60.6% | REJECTED (CI_LOW <= 0) |
| `ARM_A_E5` | Pre-Event Proximity E-5 (Intimated Notice) | 6785 | 4875 | -1.63% | [-1.85%, -1.41%] | 1.0000 | 48.5% | REJECTED (CI_LOW <= 0) |
| `ARM_A_E3` | Pre-Event Proximity E-3 (Statutory Advance Notice) | 6785 | 4875 | -1.14% | [-1.28%, -0.99%] | 1.0000 | 33.3% | REJECTED (CI_LOW <= 0) |
| `ARM_A_E1` | Pre-Event Proximity E-1 (Immediate Pre-Announcement) | 6785 | 4875 | -1.10% | [-1.18%, -1.01%] | 1.0000 | 30.3% | REJECTED (CI_LOW <= 0) |
| `ARM_B_DRIFT_20D` | Post-Earnings Drift 20D (All Beats SUE >= 0.5) | 2668 | 2348 | -1.28% | [-1.77%, -0.79%] | 1.0000 | 52.2% | REJECTED (CI_LOW <= 0) |
| `ARM_B_DRIFT_60D` | Post-Earnings Drift 60D (All Beats SUE >= 0.5) | 2269 | 1997 | +4.58% | [+3.64%, +5.52%] | 0.0000 | 69.6% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_C_SUE_0_5` | Surprise-Conditioned SUE >= +0.5 (60D Hold) | 2269 | 1997 | +4.58% | [+3.64%, +5.52%] | 0.0000 | 69.6% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_C_SUE_1_0` | Surprise-Conditioned SUE >= +1.0 (60D Hold) | 1856 | 1677 | +4.91% | [+3.89%, +5.96%] | 0.0000 | 69.6% | TEMPORALLY_INCONSISTENT |
| `ARM_C_SUE_1_5` | Surprise-Conditioned SUE >= +1.5 (Strong Beat 60D Hold) | 1030 | 971 | +5.00% | [+3.78%, +6.24%] | 0.0011 | 68.8% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_C_SUE_2_0` | Surprise-Conditioned SUE >= +2.0 (Extreme Beat 60D Hold) | 810 | 773 | +4.92% | [+3.57%, +6.23%] | 0.0000 | 60.0% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_D_MULTI_2` | Multi-Metric Surprise Z(EPS, Rev) >= 1.0 (60D Hold) | 2074 | 1841 | +4.94% | [+3.98%, +5.92%] | 0.0000 | 72.7% | TEMPORALLY_INCONSISTENT |
| `ARM_D_MULTI_3` | Multi-Metric Surprise Z(EPS, Rev, OP) >= 1.0 (60D Hold) | 2343 | 2053 | +4.66% | [+3.73%, +5.60%] | 0.0000 | 70.8% | TEMPORALLY_INCONSISTENT |
| `ARM_E_QUALITY_EVENT` | Quality Compounder x Earnings Strong Beat (ROCE>=15%, Sales>=10%, SUE>=1.5) | 314 | 298 | +3.30% | [+1.29%, +5.37%] | 0.1617 | 44.4% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_E_QUALITY_ONLY` | Frozen Quality Only (No Earnings Condition) | 1590 | 1294 | +2.18% | [+1.08%, +3.31%] | 0.4671 | 68.2% | TEMPORALLY_INCONSISTENT |
| `ARM_E_EVENT_ONLY` | Earnings Strong Beat Only (No Quality Gate) | 1030 | 971 | +5.00% | [+3.78%, +6.24%] | 0.0011 | 68.8% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_F_PRE_MOMENTUM` | Pre-Earnings Momentum x SUE >= 1.5 | 339 | 339 | +2.65% | [+0.46%, +4.93%] | 0.3408 | 57.1% | TEMPORALLY_CONCENTRATED (FAIL CONCENTRATION) |
| `ARM_G_VOLATILITY` | Event Gap-Up (>1%) x SUE >= 1.5 | 207 | 207 | +1.20% | [-1.47%, +4.06%] | 0.8847 | 44.4% | REJECTED (CI_LOW <= 0) |
| `CONTROL_1_ALL` | Control 1: All Earnings Events Unfiltered | 6051 | 4348 | +3.99% | [+3.37%, +4.66%] | 0.0000 | 72.7% | RESEARCH_ONLY |
| `CONTROL_4_MISS` | Control 4: Negative Surprise SUE <= -0.5 | 925 | 882 | +3.75% | [+2.52%, +5.05%] | 0.0230 | 50.0% | RESEARCH_ONLY |
| `CONTROL_4_SEVERE_MISS` | Control 4B: Severe Negative Surprise SUE <= -1.5 | 375 | 369 | +4.17% | [+2.55%, +5.83%] | 0.0264 | 52.9% | RESEARCH_ONLY |
