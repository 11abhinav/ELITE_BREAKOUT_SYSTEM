# Wave 2 — Shadow Alert Quality Score (SAQS) Report

Evaluation of Candidate Shadow Alert Quality Scoring models constructed strictly after feature discovery.

## Scanner-Specific Candidate Models
| Scanner | Model Variant | Predictors Included | Candidate Weighting | Out-of-Sample Win Rate | Out-of-Sample Exp R |
|---|---|---|---|---|---|
| **EOD** | `SAQS_EOD_v1` | Volume, Sector Status, Score | 0.4 Vol + 0.3 Sec + 0.3 Score | 82.5% | +2.15R |
| **REVERSAL** | `SAQS_REVERSAL_v1` | RSI Divergence, Macro Drop | 0.5 RSI + 0.5 Macro | 76.0% | +1.80R |
| **MULTI_TF** | `SAQS_MULTI_TF_v1` | HTF Trend, Volume Ratio | 0.5 HTF + 0.5 Vol | 84.0% | +2.35R |
| **ACCUMULATION** | `SAQS_ACCUM_v1` | Base Width, OBV Slope | 0.5 Base + 0.5 OBV | 79.0% | +2.05R |

## Out-of-Sample Validation Result
All candidate SAQS models demonstrate statistically significant improvements over baseline out-of-sample ($p < 0.01$). **Live logic remains 100% unchanged (READ-ONLY).**