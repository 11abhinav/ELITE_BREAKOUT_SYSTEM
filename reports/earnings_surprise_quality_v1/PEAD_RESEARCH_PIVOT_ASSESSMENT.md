# PEAD RESEARCH — DATA INFRASTRUCTURE ASSESSMENT & STRATEGY PIVOT
# Generated: 2026-09-30 IST
# Status: RESEARCH DECISION

---

## 1. FINAL DATA INFRASTRUCTURE VERDICT

### What was attempted
- Phase 1: Screener PIT export → 10 quarters per symbol (hard ceiling)
- Phase 2: NSE Financial Results API fetch (fetch_nse_quarterly_history.py)
  - 10-symbol pilot: fetched=10, failed=0
  - Result: NSE=0 for every symbol — API returns filing METADATA only
    (company name, dates, XBRL link) — actual P&L figures not in response
  - All 7,416 quarterly rows in DB sourced from SCREENER only
- Phase 3: Considered XBRL parsing — requires one HTTP request per quarter
  per company per year; 886 symbols × 40 quarters × 1 req = 35,440 requests,
  months of rate-limited work, and XBRL coverage only begins 2018.

### Hard ceiling confirmed
```
Source          Quarters available    Pre-2020 data    Usable?
SCREENER        ~13 (2023Q2-2026Q2)   NO               NO (for consensus SUE)
NSE API         Metadata only         N/A              NO
XBRL files      2018+ only            NO               NO (coverage gap)
Yahoo Finance   GOVERNANCE BLOCKED    N/A              BLOCKED
Synthetic       GOVERNANCE BLOCKED    N/A              BLOCKED
```

### Current DB state
- Total quarterly rows: 7,416 across 774 symbols
- Symbols >=12 consecutive quarters: 13 (all 2023-2026 only)
- Symbols >=20 consecutive quarters: 0
- Symbols with any data before 2020: 17
- Year distribution: 97% of rows in 2023-2026

### Conclusion
**The consensus-model SUE (requiring 12 prior quarterly EPS history for
analyst-expectations-model baseline) CANNOT be certified with any currently
accessible provider that satisfies the governance provenance requirements.**

PEAD_CONSENSUS_SUE_V1 = DATA_INSUFFICIENT / NON_CERTIFIABLE

---

## 2. STRATEGY REDESIGN: YoY-SURPRISE PEAD

### Rationale
The academic PEAD signal does not require a consensus analyst model.
The seminal SUE definition (Livnat & Mendenhall 2006) uses:

  SUE_t = (EPS_actual_t - EPS_expected_t) / σ(surprises)

Where the "expected" benchmark is the most common practical implementation:
  EPS_expected_t = EPS_actual_{t-4}   (same quarter one year ago)

This is the Year-over-Year definition. It is:
  - Academically rigorous (used in Jegadeesh & Livnat 2006)
  - PIT-clean by construction (only uses past-filed quarterly data)
  - Achievable with 5-8 quarters of history
  - Available for 677-757 symbols in the current DB (2023-2026 window)

### YoY-SUE Formula (frozen)
```
EPS_expected_t  = EPS_filed_{t-4}          (same quarter, prior year)
EPS_surprise_t  = EPS_actual_t - EPS_expected_t
σ_baseline      = std(EPS_surprise_{t-1}, ..., EPS_surprise_{t-4})
                  (rolling 4-period std of past surprises)

YoY_SUE_t       = EPS_surprise_t / σ_baseline
                  (if σ_baseline = 0 or < ε: SUE = sign(surprise) × 1.0)

STRONG_BEAT     = YoY_SUE_t >= +1.5 σ
WEAK_BEAT       = +0.5 <= YoY_SUE_t < +1.5
MISS            = YoY_SUE_t <= -0.5
```

### Minimum data requirement (reduced from 12 to 5 quarters)
```
t-4: EPS for same quarter last year (the "expected" baseline)
t-3: EPS for σ calculation (surprise #1)
t-2: EPS for σ calculation (surprise #2)
t-1: EPS for σ calculation (surprise #3)
t:   EPS actual (the event quarter)
```
5 quarters minimum. 8 quarters recommended for robust σ.

### Eligible universe with current DB
```
Symbols with >=5 quarters:   757 symbols
Symbols with >=8 quarters:   703 symbols  ← recommended minimum
Symbols with >=10 quarters:  677 symbols  ← full std-dev baseline
```

### Expected event count (YoY-comparable quarters)
```
With 10Q history (2023Q3-2026Q2): ~4,062 events (677 syms × ~6 events)
With 8Q history:                   ~2,812 events (703 syms × ~4 events)
```

### Testable backtest window
- Entry date range: 2024-Q3 through 2026-Q2
  (first YoY pair available at Q5 = 2024-Q3 for symbols starting 2023-Q3)
- Price data: Upstox 1D candles (certified, available for full window)
- Hold period: T+1 entry, 60-day fixed hold or SMA crossunder exit
- Benchmark: Nifty 500 (same window)

---

## 3. RECOMMENDED NEXT STEP

Redesign EARNINGS_SURPRISE_QUALITY_V2 using YoY-SUE:

1. Freeze YoY-SUE formula (above) — no further changes post-registration
2. Reconstruct events from existing DB (677-757 symbols, 2024-2026 window)
3. Apply QUALITY gate (ROCE>=15%, Sales CAGR>=10%) — same as FUNDAMENTAL scanner
4. Apply STRONG_BEAT filter (YoY_SUE >= +1.5σ)
5. Fetch Upstox price data for T+1 through T+60 for each event
6. Run Arm A (in-sample 2024-2025) + Arm B (holdout 2026)
7. Apply temporal robustness gate (if sample allows multiple quarters)

### What this preserves from the original design
- PIT causality (filing_date <= signal_date)
- Quality gate (same thresholds)
- Upstox price data only
- LODR timestamp basis
- Fail-closed data governance

### What changes
- SUE baseline: consensus-model (12Q) → YoY same-quarter (5Q minimum)
- Backtest window: 2016-2026 → 2024-2026 (constrained by data)
- Event universe: 0 events → ~2,800-4,000 events

---

## 4. WHAT THIS ASSESSMENT DOES NOT DO

This document does NOT claim the strategy has edge.
It establishes that a certifiable backtest is now feasible with the
redesigned signal. Edge must be proven by the backtest results.

PEAD_YOY_SUE_V2 = DESIGN_READY / PENDING_BACKTEST_CERTIFICATION
