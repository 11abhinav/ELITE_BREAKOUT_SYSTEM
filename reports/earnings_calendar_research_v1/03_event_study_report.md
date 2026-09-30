# 03 — EVENT STUDY REPORT: HORIZON RETURNS & ABNORMAL DRIFT
**Study Scope:** Event-Relative Cumulative Returns from Date T+1 Open to +60 Trading Days

| Sub-Cohort | Event Count | +1D Net Ret | +5D Net Ret | +20D Net Ret | +60D Net Ret | MFE | MAE |
|---|---|---|---|---|---|---|---|
| STRONG_BEAT (SUE >= 1.5) | 1284 | +0.41% | -0.30% | -7.10% | -0.29% | +8.88% | -10.79% |
| WEAK_BEAT (+0.5 <= SUE < 1.5) | 1384 | +0.12% | -0.45% | -6.82% | -0.38% | +8.15% | -11.45% |
| NEUTRAL (-0.5 < SUE < +0.5) | 1103 | -0.15% | -0.80% | -7.25% | -1.15% | +7.40% | -12.10% |
| MISS (SUE <= -0.5) | 1062 | +0.70% | -0.29% | -7.45% | -3.24% | +6.66% | -14.01% |
| QUALITY x STRONG_BEAT | 387 | +0.55% | -0.15% | -6.40% | +0.85% | +9.65% | -9.95% |

### Core Event Study Finding:
- Relative alpha exists: STRONG_BEAT outperforms MISS by **+295 bps** over 60 trading days (p = 0.0373).
- Absolute returns are constrained in market pullback regimes without technical trend gating.
