# Validation Contract Template

Every validator must complete every section of this template. No optional sections. Treat this like a strict interface specification before writing any Python code.

## 1. Dataset Name
[e.g., Bhavcopy]

## 2. Purpose
[e.g., Daily EOD prices, volumes, and market snapshot for all symbols]

## 3. Source
[e.g., NSE API / CSV Dump]

## 4. Update Frequency
[e.g., Daily at 18:30 IST]

## 5. Parser
[e.g., Pandas read_csv with dtype casting]

## 6. Primary Key
[e.g., (SYMBOL, SERIES)]

## 7. Required Columns
[List all columns that MUST be present, e.g., SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, TTL_TRD_QNTY]

## 8. Optional Columns
[List columns that are recognized but not mandatory, e.g., DELIV_QTY, DELIV_PER]

## 9. Expected Types
[e.g., SYMBOL: str, OPEN: float, TTL_TRD_QNTY: int]

## 10. Business Rules
[List all domain invariants, e.g., HIGH >= LOW, OPEN > 0, CLOSE > 0]

## 11. Historical Rules
[List cross-sectional invariants, e.g., Date monotonic, No duplicate symbols in same series]

## 12. Quality Metrics
[List what impacts the 0-100 score, e.g., Missing DELIV_QTY penalizes 5 points]

## 13. Cache Strategy
[e.g., APPEND / REPLACE / KEEP_CACHE]

## 14. Failure Severity Matrix
Define specific failures and their severities mapping to `ValidationCode`:
- **CRITICAL:** Missing Required Column -> `SCH001`
- **WARNING:** Missing Optional Column -> `QLT001`
- **CRITICAL:** Negative Prices -> `BUS002`

## 15. Golden Test Files
[List the specific CSV/JSON fixtures that will be created for `pytest`]
- `valid_dataset.csv`
- `missing_column.csv`
- `corrupt_types.csv`
