# RSMR-1 Historical Data Quality & Provenance Audit Report

**Date**: 2026-10-13  
**Strategy**: RSMR-1 (Relative-Strength Momentum Rotation)  
**Dataset**: Real Upstox 1D NSE Historical Candles (2016–2026)  
**Output Files**: 
- [research/strategies/rsmr_1/data/data_quality_by_symbol.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/rsmr_1/data/data_quality_by_symbol.csv)
- [research/strategies/rsmr_1/data/data_exceptions.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/rsmr_1/data/data_exceptions.csv)

---

## 1. Provenance Statement
In strict accordance with the **Mandatory Real-Market-Data Backtest Protocol** (`AGENTS.md`):
- **Provider**: Upstox API v3 Historical Candle Service
- **Endpoint**: `https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{end_date}/{start_date}`
- **Exchange**: National Stock Exchange of India (NSE)
- **Timezone**: Asia/Kolkata (IST)
- **Timeframe**: Daily (1D)
- **Date Range**: 2016-09-27 to 2026-10-12 (2,490 distinct calendar trading sessions)
- **Provenance Status**: `PROVENANCE_STATUS = CERTIFIED`

---

## 2. Universe Audit Statistics

- **Total Parquet Datasets Examined**: 947
- **Datasets with Verified Upstox Checksums**: 931 manifests (`*.manifest.json`)
- **Total Historical Bars**: 1,702,457
- **Duplicate Timestamps**: 0
- **Null Closing Prices**: 0
- **Zero / Negative Prices**: 8 bars out of 1,702,457 (isolated and filtered during cleaning)

---

## 3. Liquidity Distribution (60-Day Median Traded Value)

| Metric | Traded Value (INR) |
| :--- | :--- |
| **Mean Daily Turnover** | ₹36.39 Crore |
| **Median Daily Turnover** | ₹7.31 Crore |
| **25th Percentile** | ₹2.23 Crore |
| **75th Percentile** | ₹25.11 Crore |
| **Maximum Observed Daily Turnover** | ₹1,293.60 Crore |

**Liquidity Filter Threshold**: ₹50 Lakh (₹5,000,000 INR) minimum median daily turnover.  
**Participation Limit**: 1.0% maximum daily participation rate. Across all tested capital scales (₹5L, ₹25L, ₹1Cr), average single-slot allocations remain well below the 1.0% liquidity ceiling.

---

## 4. Data Exceptions & Exclusions
A total of **68 symbols** were flagged as data exceptions in [data_exceptions.csv](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/rsmr_1/data/data_exceptions.csv):
- **Reason 1 (< 252 Historical Bars)**: 56 recent IPO listings (e.g., `3BBLACKBIO`, `ADVAIT`, `AKCAPIT`, `AYE`, `BUILDPRO`) that lack the minimum 200–252 trading sessions needed for trend and 6-month momentum calculation at historical decision dates.
- **Reason 2 (Sub-10L Turnover / Illiquid)**: 12 micro-cap illiquid listings.

All exceptions were handled causally: rather than deleting the files, they simply fail the eligibility gate until sufficient trading history has accumulated.
