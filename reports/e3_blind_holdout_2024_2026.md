# E3 Exit Engine — Blind Holdout 2024–2026

> **FROZEN IMPLEMENTATION** — No parameter changes from 2019–2023 OOS.

**Run date:** 2026-10-03 16:28:53 IST
**Holdout epoch:** 2024-01-01 → 2026-10-03
**E3 thresholds:** Margin collapse > 30.0% | D/E > 1.25 | 3 consecutive profit declines

---

### DATA PROVENANCE
```
Provider:             Upstox
Dataset:              pit_fundamentals_v1.db
DB SHA256:            1a88e3165f89c7ded1c46c055a7fc240...
Price data:           Upstox 1D historical (data/history/1d/)
Holdout period:       2024-01-01 → 2026-10-03
Timezone:             Asia/Kolkata (IST)
Exchange:             NSE
Execution:            T+1 open after conservative_availability_timestamp
Synthetic data:       None
Fallback providers:   None
PROVENANCE_STATUS:    CERTIFIED
```

---

## 1. 2024–2026 Holdout Cohort Results

**Entries in holdout:** 196
**Exited by E3:**      62
**Censored (active):** 0

### Performance vs Buy & Hold
| Metric | Buy & Hold | E3 Exit Engine |
|---|---|---|
| Median Return | 0.98x | 0.99x |
| Mean Return   | 1.11x | 1.10x |
| Median MFE    | 1.24x | 1.20x |
| Median MAE    | 0.82x | 0.83x |

### Winner Preservation
| Tier | BnH Winners | E3 Preserved | False Positives |
|---|---|---|---|
| 2x | 24 | 20 | 4 |
| 3x | 7 | 5 | 2 |
| 5x | 0 | 0 | 0 |
| 10x | 0 | 0 | 0 |

### Value Trap Handling
- Total traps in cohort: 102
- Traps exited before trough (E3 better than BnH): 34

## 2. Trigger Attribution (All Exits)
| Trigger Type | Count |
|---|---|
| Margin | 114 |
| Profit | 54 |
| Multiple | 5 |

## 3. Active / Censored Positions (Pre-Holdout Entries Still Open as of 2026-10-03)
**Count:** 180

| Symbol | Entry Date | BnH Return to Date | E3 Exit Fired? |
|---|---|---|---|
| ADANIPOWER | 2022-12-21 | 3.50x | No (active) |
| ALLDIGI | 2018-02-20 | 2.09x | No (active) |
| ASTRAL | 2020-03-23 | 3.10x | No (active) |
| BBTC | 2020-08-11 | 0.98x | No (active) |
| BOSCHLTD | 2018-03-19 | 2.77x | No (active) |
| BOSCHLTD | 2022-06-17 | 3.60x | No (active) |
| DIXON | 2023-06-23 | 3.10x | No (active) |
| DMART | 2018-10-23 | 3.33x | No (active) |
| FMGOETZE | 2018-03-19 | 1.09x | No (active) |
| GARFIBRES | 2020-03-18 | 4.00x | No (active) |
| GHCL | 2020-12-11 | 2.14x | No (active) |
| GODREJAGRO | 2022-06-06 | 1.28x | No (active) |
| ICRA | 2018-07-19 | 1.39x | No (active) |
| INDIAMART | 2021-05-11 | 0.47x | No (active) |
| INFY | 2023-10-26 | 0.73x | No (active) |
| ITC | 2020-02-01 | 1.25x | No (active) |
| JPOLYINVST | 2022-12-14 | 2.23x | No (active) |
| KDDL | 2019-06-14 | 9.03x | No (active) |
| KRISHANA | 2019-06-19 | 36.01x | No (active) |
| LTM | 2020-03-18 | 3.08x | No (active) |

---

## Certification Verdict
| Criterion | Value | Threshold | Status |
|---|---|---|---|
| Minimum holdout entries | 196 | ≥ 20 | ✅ |
| 5x winner false-positive rate | 0.0% | ≤ 20% | ✅ |
| Value trap catch rate | 33.3% | ≥ 40% | ❌ |

### **E3 Holdout Status: ❌ FAIL — Holdout conditions not met**

> Note: This is an untouched blind holdout. No parameter changes were made after viewing these results.
> E3 is frozen at the 2019-2023 OOS certified thresholds.