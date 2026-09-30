# 11 — DATA QUALITY & COVERAGE REPORT
**Audit of Universe Coverage and Data Gates:**

| Dimension | Count | Coverage % | Status | Forensic Cause |
|---|---|---|---|---|
| TOTAL_QUARTERLY_EVENTS | 7416 | 100.0% | **PASS** | COMPLETE_PIT_EXTRACTION |
| UNIQUE_SYMBOLS | 774 | 100.0% | **PASS** | FULL_NSE_CASH_EQUITY_UNIVERSE |
| EPS_AVAILABILITY | 7389 | 99.6% | **PASS** | AUDITED_QUARTERLY_FILINGS |
| REVENUE_AVAILABILITY | 6997 | 94.4% | **PASS** | AUDITED_QUARTERLY_FILINGS |
| OPERATING_PROFIT_AVAILABILITY | 6997 | 94.4% | **PASS** | AUDITED_QUARTERLY_FILINGS |
| SCHEDULED_DATE_AVAILABILITY | 7416 | 100.0% | **PASS** | LODR_REG_29_INTIMATION |
| ACTUAL_RELEASE_TIMESTAMP | 7416 | 100.0% | **PASS** | LODR_STATUTORY_CONSERVATIVE_23_59_59 |
| CALENDAR_KNOWN_TIMESTAMP | 7416 | 100.0% | **PASS** | RECONSTRUCTED_BOARD_INTIMATION |
| SECTOR_MAPPING | 7416 | 100.0% | **PASS** | NSE_BSE_MASTER_UNIVERSE |

### Coverage Summary:
- **100.0% coverage** between the 774 quarterly fundamental symbols and Upstox 1D historical candle series.
- **Zero lookahead leakage** detected across all 6785 event dates.
