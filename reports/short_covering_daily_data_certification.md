# SHORT_COVERING_DAILY — DATA PROVENANCE & CERTIFICATION AUDIT

**Audit Date:** 2026-09-26 IST  
**Provider:** Upstox API v2 / v3 Historical Candle Endpoints  
**Coverage:** 38 Liquid NSE F&O Underlyings  
**Underlying Equity Data:** NSE Daily OHLCV (`data/history/1d/*.parquet`)  
**Derivative Data:** Near-Month (`SEP`), Next-Month (`OCT`), and Far-Month (`NOV`) FUTSTK daily candles with real exchange OI  
**Provenance Registry:** `reports/clean_upstox_daily_oi_provenance.csv`  

### Critical Provenance Audit Findings:
1. **Near + Next Aggregation Verified:** Daily aggregate OI successfully constructed as `OI(Near) + OI(Next)` for every trading day.
2. **Rollover Handling:** Contracts resolved with verified NSE Last-Tuesday expiry regulation (effective August 11, 2026).
3. **1-Year Crowding Window Limitation:** In Upstox API, individual futures contracts trade for 3 months before expiry; historical continuous futures data prior to July 2026 is purged by Upstox, meaning 1-year historical continuous futures OI cannot be fetched directly from Upstox without archived multi-year exchange bhavcopies.
