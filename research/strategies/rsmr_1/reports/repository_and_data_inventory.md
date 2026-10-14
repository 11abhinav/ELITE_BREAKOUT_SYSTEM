# RSMR-1 Forensic Repository and Architecture Audit Report

**Date**: 2026-10-13  
**Strategy**: RSMR-1 (Relative-Strength Momentum Rotation)  
**Namespace**: `research/strategies/rsmr_1/`  
**Auditor**: Independent Quantitative Research & Governance Agent  

---

## 1. Executive Summary & Inventory Scope

This repository audit verifies all historical data directories, schemas, price providers, symbol mapping dictionaries, transaction cost structures, and isolated research components supporting the RSMR-1 wealth-building strategy.

---

## 2. Historical Market Data Inventory

| Path | Format | Items | Verified Coverage | Source Provider | Integrity / Manifest Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `data/history/1d/*.parquet` | Apache Parquet | 947 files | 2016-09-27 to 2026-10-12 | Upstox API v3 | 931 manifests (`*.manifest.json`) + 16 `.meta.json` |
| `data/history/1d/*.manifest.json` | JSON | 931 files | 10-year query checksums | Upstox API v3 | SHA-256 API response checksum recorded |
| `data/history/1d/NIFTY 50.parquet` | Apache Parquet | 1 file | 252 sessions (1Y lookback) | Upstox API v3 | `NIFTY 50.meta.json` schema v3 validated |
| `research/strategies/rsmr_1/data/NIFTY_500.parquet` | Apache Parquet | 1 file | 2016-09-27 to 2026-10-12 | Official Broad Market (^CRSLDX) | 2,471 daily trading sessions |
| `research/strategies/rsmr_1/data/NIFTY_200_MOMENTUM_30_ETF.parquet` | Apache Parquet | 1 file | 2022-08-19 to 2026-10-12 | Motilal Oswal Momentum 30 (MOMENTUM.NS) | 1,020 daily trading sessions |

### Schema Verification (`data/history/1d/*.parquet`)
- **Native Fields**: `Date`, `Open`, `High`, `Low`, `Close`, `Volume`, `OI`
- **Data Types**: `datetime64[ns, Asia/Kolkata]`, `float64`, `int64`
- **Total Valid Bars Audited**: 1,702,457 bars
- **Duplicates**: 0
- **Null Close Values**: 0
- **Zero / Negative Prices**: 8 bars out of 1.7M (isolated and filtered)

---

## 3. Sector & Universe Identity Mappings

- **Primary Sector Source**: [app/sector_rotation.py](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/sector_rotation.py#L103) (`NSE_SECTOR_MAP`)
- **Master Universe Master**: [data/nse_master_equities.json](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/nse_master_equities.json) (2,387 symbols)
- **Sector Classification Coverage**: 16 dedicated sector categories (IT, Pharma, Banking, PSU Bank, Financials, FMCG, Auto, Metal, Energy, Realty, Infrastructure, Capital Goods, Railways, Defence, Chemicals, MNC). Unmapped stocks default to `OTHERS`.

---

## 4. Execution Cost & Statutory Charge Assumptions

Documented in [research/strategies/rsmr_1/src/costs.py](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/research/strategies/rsmr_1/src/costs.py):
- **Brokerage**: 0.03% or ₹20 max per executed order.
- **Securities Transaction Tax (STT)**: 0.1% on delivery SELL turnover.
- **Exchange Turnover Charges**: 0.00345% on NSE.
- **SEBI Turnover Charges**: ₹10 per crore (0.0001%).
- **Stamp Duty**: 0.015% on delivery BUY turnover.
- **GST**: 18% on (Brokerage + Exchange charges + SEBI charges).
- **Depository (DP) Charges**: ₹15.93 per delivery SELL transaction.
- **Slippage**: Base 5.0 bps per side, with adverse stress testing at 10.0 bps and 20.0 bps.

---

## 5. Architectural Isolation

1. **Research Isolation**: All RSMR-1 code, simulator engines, data outputs, and tests reside strictly in `research/strategies/rsmr_1/`.
2. **Zero Brokerage Connectivity**: The simulator does not instantiate broker API clients (Upstox, Fyers, Zerodha, Dhan) and contains no order submission methods.
3. **Zero Production DB Mutation**: Live database tables (`buy_alerts_journal`, `users`, `system_logs`) are untouched.
