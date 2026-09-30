# PEAD_FUNDAMENTAL_V1 — Morning Summary Report

**Generated:** 2026-09-30 01:20:50 IST
**Ingestion PID:** 58236 (completed)

---

## 1. Ingestion Result

| Metric | Value |
|--------|-------|
| Total rows ingested | 15,058 |
| QUARTERLY rows | 7,351 |
| Symbols with QUARTERLY data | 774 |
| Earliest quarterly period | 2012-06-30 |
| Failed symbols | 99 |
| Timestamp basis | `LODR_STATUTORY_DEADLINE_CONSERVATIVE` |

```
statement_type  rows  symbols  has_eps   earliest     latest
        ANNUAL  7707      795     7612 2005-12-31 2026-06-30
     QUARTERLY  7351      774     7324 2012-06-30 2026-06-30
```

## 2. Certification Result

**Verdict: ❌ FAIL** | Gates: 13/14

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

## 3. Dataset Freeze

| Item | Value |
|------|-------|
| DB SHA256 (frozen) | `3700fc8060666d9ece5b95dc9ce91679...` |
| Frozen manifest | `data/pit_fundamentals_v1/FROZEN_DATASET_MANIFEST.json` |

## 4. Next Step

> [!CAUTION]
> PEAD backtest is BLOCKED. Fix the 1 failed certification gate(s) first.
> Check `reports/pead_fundamental_v1/PIT_CERTIFICATION_REPORT.md` for details.

## 5. Failed Symbols (99)

```
['ABBOTINDIA', 'ACCENTMIC', 'AJAXENGG', 'ANTELOPUS', 'ASTRAZEN', 'AUTOAXLES', 'AVL', 'AYE', 'BANARISUG', 'BAYERCROP', 'BDL', 'BHARATSE', 'BHARTIHEXA', 'BUTTERFLY', 'CANFINHOME', 'CANTABIL', 'CAPITALSFB', 'CASTROLIND', 'CLSEL', 'COLPAL', 'CSBBANK', 'CUB', 'DATAPATTNS', 'DCBBANK', 'DDEVPLSTIK', 'DYCL', 'ELANTAS', 'ELLEN', 'ESABINDIA', 'FEDFINA', 'GANDHITUBE', 'GILLETTE', 'GODIGIT', 'GOODYEAR', 'GOPAL', 'GRSE', 'GUJGASLTD', 'GVT&D', 'HAWKINCOOK', 'HEIDELBERG', 'HONAUT', 'ICICIGI', 'INDIANHUME', 'INDOTECH', 'INDRAMEDCO', 'INGERRAND', 'INTERARCH', 'JAGSNPHARM', 'KARURVYSYA', 'KINGFA', 'KRISHANA', 'KROSS', 'KSHINTL', 'LAOPALA', 'LGEINDIA', 'MANGLMCEM', 'MBAPL', 'NETWEB', 'NIRLON', 'NITINSPIN', 'NOVARTIND', 'OBSCP', 'ORIENTCEM', 'ORIENTELEC', 'PAGEIND', 'PAUSHAKLTD', 'PFIZER', 'PGHH', 'PGHL', 'POWERINDIA', 'RELAXO', 'ROLEXRINGS', 'SANOFICONR', 'SBICARD', 'SBILIFE', 'SCHNEIDER', 'SGFIN', 'SHANTIGEAR', 'SHILCTECH', 'SHRINGARMS', 'SMLMAH', 'SPORTKING', 'STALLION', 'STEELCAS', 'SUPRIYA', 'SWARAJENG', 'TASTYBITE', 'THANGAMAYL', 'TIMEX', 'TIPSMUSIC', 'TMB', 'TNPL', 'VENKEYS', 'VENUSPIPES', 'VESUVIUS', 'VMART', 'VOLTAMP', 'VSTIND', 'WAAREEINDO']
```
