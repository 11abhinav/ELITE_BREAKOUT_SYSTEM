# UPSTOX DATA PROVENANCE & CERTIFICATION REPORT
**Audit Evaluation Date:** 2026-09-27  
**Governance Authority:** [AGENTS.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/AGENTS.md) Mandatory Real-Market-Data Protocol  

### 1. Market Data Provenance
- **Provider:** UPSTOX
- **API Version:** V3
- **Historical Endpoint:** `/v3/historical-candle/{instrument_key}/days/1/{to_date}/{from_date}`
- **Exchange:** National Stock Exchange of India (NSE)
- **Timeframe:** Daily Candles (1D)
- **Date Range Covered:** 2016-01-01 to 2026-09-25
- **Timezone:** Asia/Kolkata (IST)
- **Active Equities Tested:** 886 Certified Clean Equities
- **Native Exchange Fields:** `timestamp`, `open`, `high`, `low`, `close`, `volume`, `open_interest`
- **Synthetic Price Data:** 0.0% (Zero synthetic, simulated, or interpolated prices used)
- **Fallback Providers:** NONE (Zero Yahoo Finance, TradingView, or third-party market data)
- **Market Data Provenance Status:** `MARKET_DATA_PROVENANCE = CERTIFIED`

### 2. Fundamental Data Provenance
- **Provider:** TradingView Bulk Fundamentals Cache
- **Snapshot Date:** 2026-08-26
- **Publication Timestamps:** NONE (Historical quarterly exchange filing broadcast timestamps absent)
- **Point-in-Time Status:** `PIT_STATUS = FAIL (DATA_INSUFFICIENT)`
- **Survivorship Bias:** `SURVIVORSHIP_BIAS = TRUE` (Restricted to 2026 surviving equities)
- **Governance Classification:** `RESEARCH_ONLY / NON-PROMOTABLE`
