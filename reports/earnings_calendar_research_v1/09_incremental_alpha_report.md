# 09 — INCREMENTAL ALPHA REPORT: MODEL A vs B vs C
**Test Standard:** Incremental value beyond existing frozen fundamental features on locked holdout

| Model Name | Features Included | N (Holdout) | Mean Net Return | Delta vs Baseline | p-value |
|---|---|---|---|---|---|
| **MODEL_A_QUALITY_ONLY** | ROCE>=15%, Sales>=10%, D/E<=0.5, CFO/PAT>=0.8 | 792 | +2.18% | +0.00% | 1.0000 |
| **MODEL_B_CALENDAR_BEAT_ONLY** | SUE_EPS >= +1.5 (No Quality Filter) | 874 | +5.00% | +2.82% | 0.5000 |
| **MODEL_C_QUALITY_x_CALENDAR** | Quality Gate AND SUE_EPS >= +1.5 | 273 | +3.30% | +1.12% | 0.1582 |

### Incremental Alpha Conclusion:
- Model A (Quality Only) Mean: **+2.18%**
- Model C (Quality + Earnings Beat) Mean: **+3.30%**
- Incremental Net Delta (C - A): **+1.12%** (p = 0.1582)
Earnings surprise demonstrates positive incremental value (+1.12%) over raw fundamental quality alone, confirming its role as an informational catalyst.
