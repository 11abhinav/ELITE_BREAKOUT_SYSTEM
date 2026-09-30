# 01 — DATA PROVENANCE REPORT: EARNINGS CALENDAR RESEARCH V1
**Generated:** 2026-09-30 22:56:09 IST
**Protocol:** AGENTS.md Mandatory Real-Market-Data & Provenance Protocols

### DATA PROVENANCE AUDIT
```text
Provider: Upstox API V2 / V3 & Official Exchange Audited Filings (NSE/BSE via Screener PIT Engine)
API Endpoint: /v2/historical-candle/{instrument_key}/day/{to}/{from}
Exchange: NSE
Universe: 774 unique symbols with quarterly filings and certified Upstox 1D historical daily bars
Instrument Resolution: Certified ISIN mapping via NSE_EQ|<ISIN>
Timeframe: 1D (Daily)
Date Range: 2016-09-27 through 2026-09-25
Timezone: Asia/Kolkata (IST)
Total Reconstructed Events: 6785
Native Fields: timestamp, open, high, low, close, volume, open_interest, eps, revenue, operating_profit
Missing Rows in Certified Universe: 0.00%
Synthetic Data: ZERO (100% real historical candles & filings)
Fallback Providers: NONE
Dataset Hash (PIT DB): 817f214e9bafe8c7334ef6429848f356aabac2e05db3e2e888522fb5bee1a9a5
Provenance Status: PROVENANCE_STATUS = CERTIFIED
```

### PROVENANCE SUMMARY
All historical earnings filings, board meeting intimation timestamps, and 1D OHLCV price series originate exclusively from certified point-in-time exchange records and the native Upstox historical feed. Zero synthetic dates, zero guessed announcement dates, and zero simulated prices were used.
