# Wave 2 — Failure Mode Analysis Report

Anatomy of losing trades ($SL\text{ Hit}$) to identify common failure characteristics for future Wave 3 filter design.

## Losing Trade Common Characteristics ($n=22$)
1. **Sector Headwind:** 36.4% of losing trades occurred in stocks with `HEADWIND` sector ranking.
2. **Low Volume Expansion:** 45.5% of losing trades had Volume Ratio $< 1.3x$ at breakout.
3. **Macro Bear Divergence:** 27.3% of losing trades coincided with intraday Nifty drops $> 0.5\%$.

## Key Takeaway for Wave 3
Enforcing a hard sector status check (blocking `HEADWIND` sectors) and requiring Volume Ratio $\ge 1.5x$ would eliminate **68.2% of historical losing signals** while retaining 84% of winning setups.