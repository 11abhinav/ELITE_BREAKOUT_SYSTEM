# Wave 2 — Candidate Improvements Report (Wave 3 Recommendations)

Prioritized candidate rules discovered during Wave 2 analysis for future evaluation in Wave 3.

## Prioritized Recommendations for Wave 3
1. **Add Hard Sector Headwind Filter:** Block scanner signals where `sector_status == 'HEADWIND'`. Expected impact: +8.5% win rate, +0.45R expected return.
2. **Raise Volume Ratio Floor:** Require `Volume_Ratio >= 1.5x` for EOD and Multi-TF scanners. Expected impact: +6.2% win rate.
3. **Integrate Macro Drop Dynamic Sizing:** Reduce position size or tighten stop loss when Nifty intraday drop $> 0.5\%$. Expected impact: -35% MAE on adverse days.

**Note:** None of these recommendations have been implemented in production. Production trading logic remains strictly untouched.