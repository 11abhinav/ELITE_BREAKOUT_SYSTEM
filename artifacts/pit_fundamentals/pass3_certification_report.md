# FULL UNIVERSE PIT FUNDAMENTAL CERTIFICATION REPORT (PASS 3)

**Generated At**: 2026-09-27T23:42:37.092015  
**Provenance Provider**: Screener Audited Annual Disclosures  
**Dataset Path**: `/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/pit_fundamentals_v1/pit_fundamentals_v1.db`  
**Dataset SHA256**: `684963962032aa3d4e2a3974c55d13baf423b723d2a7763fff363bef5d35407f`  
**Governance Status**: **`FULL_UNIVERSE_PIT_STATUS = PARTIAL`**

---

## 1. Executive Summary

- **Approved Universe**: 886 symbols
- **PIT Hydrated Symbols**: 795 / 886 (89.73%)
- **Dataset Rows**: 7707 rows
- **Zero PIT Causality Violations**: True

---

## 2. Gate Results

| Gate | Description | Metric | Result |
|------|-------------|--------|--------|
| **Gate 1** | PIT Coverage $\ge 98\%$ | 89.73% | ❌ FAIL |
| **Gate 2** | Multi-Year Coverage $\ge 90\%$ (2018–2026) | Min year 60.61% | ❌ FAIL |
| **Gate 3** | Market Cap Tier Coverage | Large 92.0%, Mid 89.0%, Small 90.33%, Micro 88.81% | ✅ PASS |
| **Gate 4** | Field Completeness $\ge 85\%$ | Min field 84.54% | ❌ FAIL |
| **Gate 5** | Zero PIT Causality Violations | 0 violations | ✅ PASS |
| **Gate 6** | Variant Completeness (V0–V10) | All variants $\ge 85\%$ | ❌ FAIL |

---

## 3. Variant-Level Completeness (V0–V10)

| Variant Name | Required Fields | Coverage Pct | Status |
|--------------|-----------------|--------------|--------|
| `V0_V1_GROWTH_QUALITY` | `roce, roe, total_debt, total_equity, operating_profit, eps` | 84.54% | `FAIL` |
| `V2_VALUATION_MULTIPLES` | `net_profit, eps, total_equity, revenue` | 89.39% | `PASS` |
| `V3_HISTORICAL_VALUATION_5Y` | `revenue, net_profit, eps, operating_profit` | 89.62% | `PASS` |
| `V4_SECTOR_PEER_RANK` | `roce, operating_margin, revenue` | 84.54% | `FAIL` |
| `V5_MULTI_YEAR_EPS_GROWTH` | `eps, net_profit` | 89.73% | `PASS` |
| `V6_REVENUE_OCF_QUALITY` | `revenue, operating_cash_flow` | 89.39% | `PASS` |
| `V7_FCF_YIELD_QUALITY` | `free_cash_flow, operating_cash_flow, net_profit` | 89.5% | `PASS` |
| `V10_COMBINED_COMPOSITE` | `roce, roe, revenue, operating_profit, net_profit, eps, operating_cash_flow, total_debt, total_equity` | 84.54% | `FAIL` |

---

## 4. Provenance & Compliance Verification

```text
Provider: Screener (Audited Annual Disclosures)
Source Provider Column: source_provider = 'SCREENER'
Financial Sector NII Rule: mapping_rule = 'NII_AS_REVENUE'
Point-in-Time Availability: conservative_availability_timestamp (SEBI Reg33 conservative)
Causality Guard: WHERE conservative_availability_timestamp < signal_timestamp
Revision History Policy: Erroneous ingestion updated in-place; Restatements inserted as revision_number > 1
Strategy Definitions: FROZEN (V0–V10 unchanged)
```
