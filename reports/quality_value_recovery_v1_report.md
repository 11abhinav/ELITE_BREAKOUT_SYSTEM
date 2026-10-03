# QUALITY_VALUE_RECOVERY_WEALTH_V1 - Preliminary Backtest Report
**Run Date:** 2026-10-03 13:23:06

## Universe Statistics
- Total Unique Companies Evaluated: 860
- Total Annual Statements Processed: 8634

## Quality Breakdown
- Statements meeting Baseline Quality (ROCE > 15%, Profitable): 4976
- Statements showing Improving Fundamentals (Profit/ROCE > 3Y Med): 3663

## Next Steps
The architecture is ready. The next module will merge these point-in-time fundamental 
epochs with daily price data to evaluate the PE and EV/EBITDA drawdowns and execute the 
entry/exit models defined in the master prompt.