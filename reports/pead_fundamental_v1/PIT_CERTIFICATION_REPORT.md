# PEAD_FUNDAMENTAL_V1 — PIT Dataset Certification Report

**Run timestamp:** 2026-09-30 08:20:18 IST
**DB path:** `/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_fundamentals_v1/pit_fundamentals_v1.db`
**Total rows:** 15058 | QUARTERLY: 7351 | ANNUAL: 7707

## Gate Results

| Gate | Name | Status | Detail |
|------|------|--------|--------|
| [01] | QUARTERLY row count > 0 | ✅ PASS | 7351 QUARTERLY rows found |
| [02] | QUARTERLY symbols >= 500 | ✅ PASS | 774 symbols have at least one QUARTERLY row |
| [03] | QUARTERLY earliest period <= 2013-03-31 | ✅ PASS | Earliest period_end_date = 2012-06-30 |
| [04] | QUARTERLY EPS coverage >= 85% | ✅ PASS | 99.6% of quarterly rows have non-null EPS |
| [05] | QUARTERLY Revenue coverage >= 90% | ✅ PASS | 94.5% of quarterly rows have non-null revenue |
| [06] | QUARTERLY Operating Profit coverage >= 85% | ✅ PASS | 94.5% of quarterly rows have non-null operating_profit |
| [07] | ANNUAL OCF coverage >= 60% (industrial) | ✅ PASS | 99.4% of annual industrial rows have OCF |
| [08] | ANNUAL ROCE coverage >= 70% (industrial) | ✅ PASS | 93.7% of annual industrial rows have ROCE |
| [09] | ANNUAL ROE coverage >= 70% (industrial) | ✅ PASS | 98.4% of annual industrial rows have ROE |
| [10] | All rows have LODR_STATUTORY_DEADLINE_CONSERVATIVE basis | ✅ PASS | All rows correctly tagged |
| [11] | PIT chronology: conservative_ts > period_end_date | ✅ PASS | 0 rows where conservative_ts <= period_end_date (PIT violation) |
| [12] | No duplicate (symbol, period_end_date, statement_type, revision_number) | ✅ PASS | 0 duplicate rows found |
| [13] | No symbol with quarterly gap > 3 consecutive quarters | ❌ FAIL | Max gap = 37Q | 24 symbols with gap>3Q (sample: ['ABB(2019-09-30->2022-03-31)', 'ABB(2022-06-30->2024-09-30)', 'AIMTRON(2024-09-30->2025-09-30)']) |
| [14] | DB SHA256 matches ingestion manifest | ✅ PASS | Manifest SHA=3700fc8060666d9e... | Actual=3700fc8060666d9e... |

## Coverage by Statement Type

```
statement_type  total_rows  symbols  has_eps  has_revenue   earliest     latest
        ANNUAL        7707      795     7612         7254 2005-12-31 2026-06-30
     QUARTERLY        7351      774     7324         6949 2012-06-30 2026-06-30
```

## DATA PROVENANCE SECTION

- **Provider:** Screener.in (HTML scrape)
- **Timestamp basis:** `LODR_STATUTORY_DEADLINE_CONSERVATIVE`
- **actual_publication_timestamp:** Set to SEBI LODR statutory deadline (T+45d Q1-Q3, T+60d Q4/Annual) at 23:59:59 IST
- **Provenance limitation:** True board-meeting timestamps NOT recoverable from Screener
- **PIT safety:** Conservative (data appears at deadline, never before statutory requirement — safe direction for causality)
- **PEAD engine rule:** Must use `conservative_availability_timestamp` for pre-event gating

## Certification Verdict

**FAIL — PEAD BACKTEST BLOCKED (1 gates failed)**

Gates passed: **13/14**

> [!CAUTION]
> PEAD_FUNDAMENTAL_V1 backtest is BLOCKED until all certification gates pass.
