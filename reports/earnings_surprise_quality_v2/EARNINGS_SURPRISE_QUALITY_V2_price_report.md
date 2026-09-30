# EARNINGS_SURPRISE_QUALITY_V2 — PRICE RECONSTRUCTION REPORT
Generated: 2026-09-30T14:55:41 IST

### DATA PROVENANCE
Provider: Upstox Historical Candle API V2
Endpoint: /v2/historical-candle/{instrument_key}/day/{to}/{from}
Exchange: NSE (NSE_EQ|ISIN instrument keys)
Instrument resolution: certified mapper (market_data.providers.upstox_instrument_mapper)
Interval: 1D (daily OHLCV)
Friction: 5.0 bps round-trip applied to hold-period returns
T+1 basis: next actual NSE trading session (weekends + official holidays skipped)
Synthetic data: NONE
Fallback providers: NONE
PROVENANCE_STATUS = CERTIFIED_UPSTOX_V2_1D

### PRICE FETCH SUMMARY
Total events: 371
Successfully priced (T+1 OK): 368
Failed / no candle: 3

### RETURN COMPARISON BY CATEGORY
| Category | N | Hold_Net_Mean | Hold_Net_Med | Ret20d_Mean | Win20d% | MFE | MAE |
|----------|---|---------------|--------------|-------------|---------|-----|-----|
| MISS | 71 | -3.24% | -4.44% | -7.45% | 11.3% | 6.66% | -14.01% |
| NEUTRAL | 86 | -0.52% | -2.28% | -7.54% | 8.1% | 7.71% | -11.53% |
| STRONG_BEAT | 136 | -0.29% | -2.46% | -7.10% | 7.4% | 8.88% | -10.79% |
| WEAK_BEAT | 75 | -0.39% | -2.99% | -6.16% | 14.7% | 9.56% | -12.27% |

### GOVERNANCE NOTES
- These raw means are DESCRIPTIVE only.
- Statistical significance requires block-bootstrap by event-date cluster.
- PEAD events cluster around earnings seasons (Q1: Feb/Mar, Q2: May,
  Q3: Aug, Q4: Nov). Adjacent events share macro regime → N_eff << raw N.
- Do NOT promote based on this table alone.

### NEXT STEP
Run block-bootstrap CI + permutation p-value before any promotion decision.

BACKTEST_STATUS = IN_CERTIFICATION (price reconstruction complete; 
                  statistical battery pending)
