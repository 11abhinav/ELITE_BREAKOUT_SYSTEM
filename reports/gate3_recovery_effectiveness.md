# Gate 3 — Recovery Effectiveness

**Date:** 2026-10-03 16:19:29 IST
**Canonical path:** `/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/data/canonical_pit_rebuilt.parquet`
**Universe:** 886 / 886 expected

## Field-Level Completeness
| Field | Column Found | Complete | % | Missing | Status |
|---|---|---|---|---|---|
| ROCE | `roce_5y_avg` | 810 | 91.4% | 76 | ✅ |
| sales_cagr_5y | `sales_cagr_5y` | 730 | 82.4% | 156 | ✅ |
| pat_cagr_5y | `pat_cagr_5y` | 707 | 79.8% | 179 | ✅ |
| cfo_pat_5y | `cfo_pat_5y_ratio` | 855 | 96.5% | 31 | ✅ |
| debt | `debt_to_equity` | 884 | 99.8% | 2 | ✅ |

## Provenance
| CERTIFIED | Count | % | Status |
|---|---|---|---|
| Certified (Eligible) | 642 / 662 | 97.0% | ✅ |
| Total Accounted (incl. Structural Ineligible) | 866 / 886 | 97.7% | ✅ |

## Recovery Queue
| Blocked (all required fields missing) | 2 / 886 | 0.2% | ✅ |
| Observability: partial missing (≥1 field) | 236 / 886 | 26.6% | Informational |

## Field-Level Breakdown (First 20 Incomplete Symbols)
| Symbol | Missing Fields |
|---|---|
| AADHARHFC | ROCE, sales_cagr_5y |
| AAVAS | ROCE, sales_cagr_5y |
| ABCAPITAL | ROCE, sales_cagr_5y |
| ADOR | pat_cagr_5y |
| AEGISVOPAK | sales_cagr_5y, pat_cagr_5y |
| AIIL | ROCE, sales_cagr_5y, pat_cagr_5y |
| AIMTRON | sales_cagr_5y, pat_cagr_5y |
| ANONDITA | ROCE, sales_cagr_5y, pat_cagr_5y, cfo_pat_5y |
| APTUS | ROCE, sales_cagr_5y |
| ARIS | sales_cagr_5y, pat_cagr_5y |
| ARSSBL | sales_cagr_5y, pat_cagr_5y |
| ARVINDFASN | pat_cagr_5y |
| ASHOKLEY | pat_cagr_5y |
| ATULAUTO | pat_cagr_5y |
| AUBANK | ROCE, sales_cagr_5y |
| AURIONPRO | pat_cagr_5y |
| AWFIS | pat_cagr_5y |
| AXISBANK | ROCE, sales_cagr_5y |
| AXISCADES | pat_cagr_5y |
| AYE | ROCE, sales_cagr_5y |

## Gate 3 Verdict: ✅ PASS — Proceed to Gate 4