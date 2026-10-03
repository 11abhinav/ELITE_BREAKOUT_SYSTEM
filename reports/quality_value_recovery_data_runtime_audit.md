# QUALITY_VALUE_RECOVERY — Data Provenance & Telemetry Runtime Audit Report

**Strategy ID:** `QUALITY_VALUE_RECOVERY`  
**Data Provider:** Upstox API & Exchange Filings (PIT Statement Filings)  
**Primary Dataset:** `data/canonical_pit_rebuilt.parquet`  
**Dataset SHA256:** `943a651fa26a8d97...`  
**Date:** 2026-10-03  
**Status:** `DATA_PROVENANCE_CERTIFIED`

---

## 1. Data Provenance & Upstox Integration

```text
### DATA PROVENANCE
Provider: Upstox API + Exchange Filings (XBRL Statements)
API Version: Upstox v2 (GET /v2/fundamentals/{isin}/key-ratios)
Exchange: NSE / BSE
Universe: 886 Certified Clean Indian Equities
Timeframe: Point-in-Time (Annual + Quarterly Filings 2016–2026)
Date Range: 2016-04-01 to 2026-10-03
Timezone: Asia/Kolkata (IST)
Native Fields: timestamp, open, high, low, close, volume, open_interest, revenue, operating_profit, net_profit, operating_cash_flow, total_debt, total_equity, cash_and_equivalents
Missing Rows: Explicitly classified per symbol census (Zero synthetic fill)
Provenance Status: PROVENANCE_STATUS = CERTIFIED
```

---

## 2. Telemetry Logging Standard Enforcement

Every log line emitted during dataset loading, API requests, exchange filing sweeps, or symbol evaluation explicitly includes scanner identity and operation tags:

```text
[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA] 🧹 START — pre-scan fundamental sweep.
[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA] Universe=886 | Complete=845 | Incomplete=41
[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA] Fetching Upstox/NSE filings for SYMBOL...
[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA] ✅ Loaded and calculated certified PIT dataset (886 symbols)
```

---

## 3. Pre-Recovery Engine Architecture

```text
QUALITY_VALUE_RECOVERY
       ↓
FundamentalPreRecoveryEngine (scanner_name="QUALITY_VALUE_RECOVERY")
       ↓
identify_incomplete_symbols()  [Field-Level Granularity: ROCE, CAGR, CFO/PAT, D/E]
       ↓
FundamentalSourceRouter (Upstox Key-Ratios API + NSE Filings)
       ↓
Dual-Source Reconciliation (Fail-closed on DATA_CONFLICT)
       ↓
publish_canonical_pit() (Enforces 18-dimension Never-Downgrade Gate)
       ↓
load_pit_dataset() (Pure read of certified canonical PIT file)
```

---

## 4. Operational Telemetry Checklist

- [x] **Provider Audit:** Upstox API used as primary data provider; no synthetic OHLCV or fake fundamental metrics.
- [x] **Point-in-Time Integrity:** Signals strictly use statements published prior to signal timestamp (`publication_timestamp < signal_timestamp`).
- [x] **Data Conflict Resolution:** Divergent numbers across providers trigger `DATA_CONFLICT` and block trade signal creation.
- [x] **Explicit Telemetry Tags:** 100% of data loading lines prefixed with `[SCANNER: QUALITY_VALUE_RECOVERY] [FETCH_DATA]`.
