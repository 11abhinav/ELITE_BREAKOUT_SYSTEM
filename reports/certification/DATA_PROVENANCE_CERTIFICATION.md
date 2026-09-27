# DATA PROVENANCE CERTIFICATION
**Provider:** Upstox Native API (Historical V2 / V3 Endpoints)  
**Universe:** 886 Certified Clean Equities (National Stock Exchange of India)  
**Trade Count:** 81,653 Causal Breakout Trades  
**Date Range:** 2016-11-21 to 2026-09-25 (9.83 Calendar Years)  
**Timezone:** Asia/Kolkata (IST)  
**Dataset SHA256:** `da291c0047416613e7c5a38ac096ea51f69a39da0f1fb2f289f415acf14848cb`  
**Provenance Status:** `PROVENANCE_STATUS = CERTIFIED`  

---

## DATA AUDIT SUMMARY
1. **Source Verifiability:** 100% of underlying candles originate from Upstox API historical data.
2. **Exclusions:** Zero Yahoo Finance data, zero TradingView data, zero simulated or synthetic prices.
3. **Native Fields Utilized:** `timestamp, open, high, low, close, volume`.
4. **Corporate Action Adjustment:** Clean universe audited across 927 symbols; 41 symbols with unadjusted splits or abnormal listing gaps were permanently quarantined.
5. **Point-in-Time Integrity:** Signals generated at session T Close; execution at T+1 Open. Zero future candle leakage.

---
*Authored by Elite Breakout System Research Engine.*
