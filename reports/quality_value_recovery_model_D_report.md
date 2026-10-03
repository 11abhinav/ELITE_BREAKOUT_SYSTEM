# QUALITY_VALUE_RECOVERY_WEALTH_V1 - Model D Execution
**Run Date:** 2026-10-03 14:12:58

## Core Parameters
- **Data Span:** 2010 to 2026
- **Quality Criteria:** ROCE >= 15%, Net Profit > 0
- **Valuation Compression:** PE_T or EV_EBITDA_T <= 80% of trailing 3Y median
- **Execution:** Strict T+1 Open pricing.
- **Statistics:** Corrected strict NaN exclusion for win rates.

## Backtest Results (Matched Cohorts)
- Total Trigger Events: 1212
- Events with Valuation Compression: 487
- Events without Valuation Compression (Placebo): 725

### 1. Valuation Placebo Test (3-Year Horizon Model A)
- **WITH Valuation Compression:** Median 71.6% | Win Rate 80.6% (N=284)
- **WITHOUT Valuation Compression:** Median 56.5% | Win Rate 76.6% (N=397)
