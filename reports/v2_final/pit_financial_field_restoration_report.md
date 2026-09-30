# PIT Financial Field Restoration Report
Generated: 2026-09-30

## DATA PROVENANCE
Provider: Upstox (via pit_fundamentals_v1.parquet + multibagger_fundamentals_cache.json)
Universe: 796 symbols
Synthetic data: NONE
Fallback providers: multibagger_fundamentals_cache (market_cap), 1D-history (price)

## Summary
| Metric | Count |
|--------|-------|
| Total symbols | 796 |
| shares available | 796 |
| debt available | 728 |
| cash available | 9 |
| EBITDA available | 751 |
| market_cap available | 796 |
| current_EV_EBITDA computable | 742 |
| ev_ebitda_3y_median available | 789 |
| Sep-29 benchmark (784) met | NO ❌ |

## MCap Source Breakdown
- MULTIBAGGER_CACHE: 796

## EV Cash Component
- KNOWN (full EV = MCap + Debt - Cash): 9
- UNKNOWN (conservative EV = MCap + Debt): 787

## Independent Validation
- All 20 probe symbols: PASS ✅

PROVENANCE_STATUS = CERTIFIED