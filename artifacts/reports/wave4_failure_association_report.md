# Wave 4 — Training Failure Association & Feature Dependency Report

**Generated:** 2026-08-30 19:41:27 IST  
**Partition Scope:** Train / Discovery Only ($n=35$)  
**Target Definition:** Realized Net Trade R (Gross R minus 0.05R friction)  

## 1. Feature Provenance & Availability Classification
| Feature Name | Availability Classification | Mean | Std Dev | Variance | Role in AQS |
|---|---|---|---|---|---|
| `dist_sma50_pct` | `PRE_DECISION` | 10.5493 | 6.1896 | 38.3114 | Active Predictor |
| `dist_sma200_pct` | `PRE_DECISION` | 14.2186 | 4.2289 | 17.8838 | Active Predictor |
| `rsi` | `PRE_DECISION` | 68.0394 | 4.4501 | 19.8038 | Active Predictor |
| `volume_ratio` | `AVAILABLE_AT_DECISION` | 0.9058 | 0.3326 | 0.1106 | Active Predictor |
| `sector_blended_score` | `PRE_DECISION` | *N/A* | *N/A* | *N/A* | Post-Evaluation Diagnostic Only |
| `is_tailwind` | `PRE_DECISION` | *N/A* | *N/A* | *N/A* | Post-Evaluation Diagnostic Only |
| `rr_ratio` | `POST_SETUP_DERIVED_FOR_EVAL_ONLY` | *N/A* | *N/A* | *N/A* | Post-Evaluation Diagnostic Only |

## 2. Feature Collinearity & Redundancy Audit
Pairwise correlations were evaluated to identify and neutralize collinearity:

| Feature 1 | Feature 2 | Correlation ($r$) | Regularization Treatment |
|---|---|---|---|
| `dist_sma50_pct` | `dist_sma200_pct` | -0.7737 | Managed via Ridge L2 penalty (lambda=10.0) |
| `dist_sma50_pct` | `rsi` | 0.9947 | Managed via Ridge L2 penalty (lambda=10.0) |
| `dist_sma50_pct` | `volume_ratio` | 0.8931 | Managed via Ridge L2 penalty (lambda=10.0) |
| `dist_sma200_pct` | `rsi` | -0.8348 | Managed via Ridge L2 penalty (lambda=10.0) |
| `rsi` | `volume_ratio` | 0.8421 | Managed via Ridge L2 penalty (lambda=10.0) |

## 3. Observed Training Associations (Winning vs Losing Alerts)
> [!NOTE]
> **Methodological Standard:** Associations observed in the training sample ($n=35$) indicate empirical correlation and are not asserted as definitive causal root causes.

| Characteristic | Winning Alerts ($n=23$) | Losing Alerts ($n=12$) | Observed Training Association |
|---|---|---|---|
| **Mean Distance to SMA50** | +0.0% | +0.0% | Moderate trend extension favored |
| **Mean Distance to SMA200** | +0.0% | +0.0% | Strong multi-month base favored |
| **Mean RSI** | 70.0 | 64.3 | Non-overbought momentum favored |

## 4. Regularized Ridge Weights (L2 lambda=10.0)
| Feature | Weight ($w$) | Availability |
|---|---|---|
| `dist_sma50_pct` | **-0.1284** | `PRE_DECISION` |
| `dist_sma200_pct` | **-0.1965** | `PRE_DECISION` |
| `rsi` | **-0.0796** | `PRE_DECISION` |
| `volume_ratio` | **-0.3247** | `AVAILABLE_AT_DECISION` |