# CERTIFICATION CORRECTIONS REPORT
**Date:** 2026-09-26 | **Run Start:** 2026-09-26 16:29:56 IST | **Run End:** 2026-09-26 16:34:12 IST
**Total Wall-Clock (replay + stats):** 256.7s
**Output Dir:** `reports/certification/CORRECTIONS_2026-09-26/`

---

## Correction 1 — TECHNICAL_INTRADAY: Wyckoff Spring Type 2 Isolated Holdout

**Pooled verdict (unchanged):** DECOMMISSIONED
Pooled CI [-0.1310R, -0.0623R],
N=3019 — entirely negative, Gate 5 fails unconditionally.

**Wyckoff Spring Type 2 — isolated:**

| Metric | Value |
|--------|-------|
| N (isolated) | 424 |
| Win Rate | 42.9% |
| Mean Realized R | +0.0041R |
| 95% Bootstrap CI | [-0.0919R, +0.1000R] |
| Gate 5 Verdict | **DECOMMISSIONED** |
| p vs other patterns | 0.0140 |
| Cohen's d | 0.1133 |

**Bull Flag — isolated:**

| Metric | Value |
|--------|-------|
| N | 2595 |
| Mean R | -0.1139R |
| 95% CI | [-0.1496R, -0.0769R] |
| Gate 5 | **DECOMMISSIONED** |

Isolation rationale: `pattern` column assigned at signal detection (before outcome walk)
at `run_full_system_certification.py:L678`. Filtering by pattern is structurally
equivalent to re-running with a single-pattern gate. If Wyckoff Spring CI_low > 0.000R
and N >= 10, it qualifies as a standalone CERTIFIED_PRODUCTION candidate — but requires
a fresh single-pattern replay script (not just a post-hoc ledger filter) before live routing.

**Wall-clock:** 6.14s

---

## Correction 2 — REVERSAL: Regime-Gated CIs (Binary Verdict)

**`CERTIFIED_CONDITIONAL` label: RETIRED.** Binary only: CERTIFIED_PRODUCTION or DECOMMISSIONED.

**Final Verdict: DECOMMISSIONED**

| Regime | N | Win Rate | Mean R | 95% Bootstrap CI | Gate 5 | Verdict |
|--------|---|----------|--------|------------------|--------|---------|
| BEAR | 289 | 44.6% | +0.0627R | [-0.0648R, +0.1897R] | FAIL | DECOMMISSIONED |
| SIDEWAYS | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | FAIL | DECOMMISSIONED |
| BULL | 21 | 38.1% | -0.1180R | [-0.5743R, +0.3708R] | FAIL | DECOMMISSIONED |
| OVERALL | 310 | 44.2% | +0.0505R | [-0.0720R, +0.1762R] | FAIL | DECOMMISSIONED |


**Operational Gate Definition:**
_No regime cleared Gate 5 — DECOMMISSIONED._

Even if a regime passes Gate 5, live routing requires written gate implementation,
peer review, and dedicated capital allocation tracking before any signal fires live.

**Wall-clock:** 2.36s

---

## Correction 3 — WEALTH_ENGINE / MULTIBAGGER: One-Row-Per-Holding Rebuild

### Root Cause Confirmed

Prior ledgers (N~37k/36k) produced by `replay_portfolio_compounder()` at
`run_full_system_certification.py:L970`:

```python
for i in range(200, len(df) - holding_days - 2, 20):  # stride=20 bars
```

For a 10-year daily series (~2,500 bars): `(2500-200-62)/20 ~ 112 rows/symbol x 890 symbols`.
Each row is a periodic snapshot that overlaps prior open positions. This is NOT
one-row-per-holding. The `avg_rho=0.28` was hardcoded at `L1052` — identical for
both scanners, explaining the coincidental N_eff=3.6 match.

### WEALTH_ENGINE Corrected Result

| Metric | Prior (INVALID) | Corrected |
|--------|-----------------|-----------|
| Ledger type | 20-bar periodic sampling | One-row-per-holding |
| N raw | ~37,291 | 32489 |
| avg_rho | 0.28 (hardcoded) | 0.1594 (actual pairwise) |
| N_eff | 3.6 | 6.3 |
| Mean R | (invalid) | 0.2758 |
| 95% CI | (invalid) | [0.2569, 0.2947] |
| Gate 5 Verdict | ~~FULLY_CERTIFIED (WRONG)~~ | **STATISTICALLY_UNDERPOWERED** |

### MULTIBAGGER Corrected Result

| Metric | Prior (INVALID) | Corrected |
|--------|-----------------|-----------|
| Ledger type | 20-bar periodic sampling | One-row-per-holding |
| N raw | ~36,799 | 29594 |
| avg_rho | 0.28 (hardcoded) | 0.1652 (actual pairwise) |
| N_eff | 3.6 | 6.1 |
| Mean R | (invalid) | 0.2923 |
| 95% CI | (invalid) | [0.2719, 0.3125] |
| Gate 5 Verdict | ~~FULLY_CERTIFIED (WRONG)~~ | **STATISTICALLY_UNDERPOWERED** |

If N_eff < 10: verdict is STATISTICALLY_UNDERPOWERED per charter regardless of CI sign.
These scanners may need longer lookback or relaxed gate filters to accumulate sufficient
non-overlapping holdings.

---

## Correction 4 — Wall-Clock Timing

**This corrections run:** Start=2026-09-26 16:29:56 IST, End=2026-09-26 16:34:12 IST, Elapsed=256.7s

**Prior run (run_full_system_certification.py):** No `time.time()` instrumentation existed.
Confirmed by inspection — zero timing calls in the script. Duration cannot be reconstructed.

The stride-20 sampling in the prior run's WEALTH_ENGINE/MULTIBAGGER replay would have
made those scans structurally faster than a genuine bar-by-bar pass, consistent with the
user's concern. The corrected one-row-per-holding approach eliminates the stride entirely.
All future runs must instrument wall-clock at the start/end of each scanner replay function.

---

## Correction 5 — Gate 4 Regime x Naive Baseline Tables

### Gate 4: `EOD` — Regime x Naive Baseline

| Regime | System Type | N | Win Rate | Mean R | 95% CI | p vs Naive | Cohen's d | Max DD |
|--------|-------------|---|----------|--------|--------|------------|-----------|--------|
| BULL | `FULL_SCANNER` | 1768 | 43.1% | +0.0829R | [+0.0255R, +0.1420R] | 0.6356 | 0.0121 | 35.70R |
| BULL | `NAIVE_BASELINE` | 11112 | 42.4% | +0.0679R | [+0.0454R, +0.0907R] | 1.0000 | 0.0000 | 56.10R |
| BEAR | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| BEAR | `NAIVE_BASELINE` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `FULL_SCANNER` | 141 | 47.5% | +0.1243R | [-0.0729R, +0.3260R] | 0.3273 | -0.0912 | 11.14R |
| SIDEWAYS | `NAIVE_BASELINE` | 627 | 49.0% | +0.2371R | [+0.1382R, +0.3351R] | 1.0000 | 0.0000 | 11.25R |
| OVERALL | `FULL_SCANNER` | 1909 | 43.4% | +0.0860R | [+0.0303R, +0.1409R] | 0.7627 | 0.0073 | 35.00R |
| OVERALL | `NAIVE_BASELINE` | 11739 | 42.8% | +0.0770R | [+0.0545R, +0.0993R] | 1.0000 | 0.0000 | 43.08R |

### Gate 4: `PULLBACK` — Regime x Naive Baseline

| Regime | System Type | N | Win Rate | Mean R | 95% CI | p vs Naive | Cohen's d | Max DD |
|--------|-------------|---|----------|--------|--------|------------|-----------|--------|
| BULL | `FULL_SCANNER` | 7380 | 46.5% | +0.1296R | [+0.1032R, +0.1557R] | 0.9838 | 0.0003 | 27.17R |
| BULL | `NAIVE_BASELINE` | 20453 | 46.0% | +0.1292R | [+0.1134R, +0.1451R] | 1.0000 | 0.0000 | 34.46R |
| BEAR | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| BEAR | `NAIVE_BASELINE` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `NAIVE_BASELINE` | 13574 | 44.2% | +0.0907R | [+0.0709R, +0.1100R] | 1.0000 | 0.0000 | 60.91R |
| OVERALL | `FULL_SCANNER` | 7380 | 46.5% | +0.1296R | [+0.1028R, +0.1553R] | 0.2973 | 0.0135 | 27.17R |
| OVERALL | `NAIVE_BASELINE` | 34027 | 45.3% | +0.1139R | [+0.1016R, +0.1262R] | 1.0000 | 0.0000 | 49.88R |

### Gate 4: `ACCUMULATION` — Regime x Naive Baseline

| Regime | System Type | N | Win Rate | Mean R | 95% CI | p vs Naive | Cohen's d | Max DD |
|--------|-------------|---|----------|--------|--------|------------|-----------|--------|
| BULL | `FULL_SCANNER` | 2392 | 44.9% | +0.1716R | [+0.1197R, +0.2244R] | 0.2277 | 0.0264 | 30.96R |
| BULL | `NAIVE_BASELINE` | 20561 | 43.0% | +0.1369R | [+0.1190R, +0.1549R] | 1.0000 | 0.0000 | 31.08R |
| BEAR | `FULL_SCANNER` | 144 | 38.9% | -0.0420R | [-0.2306R, +0.1566R] | 0.4422 | -0.0693 | 14.69R |
| BEAR | `NAIVE_BASELINE` | 732 | 40.2% | +0.0446R | [-0.0453R, +0.1386R] | 1.0000 | 0.0000 | 25.49R |
| SIDEWAYS | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `NAIVE_BASELINE` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| OVERALL | `FULL_SCANNER` | 2536 | 44.5% | +0.1595R | [+0.1086R, +0.2102R] | 0.3442 | 0.0196 | 35.32R |
| OVERALL | `NAIVE_BASELINE` | 21293 | 42.9% | +0.1338R | [+0.1164R, +0.1513R] | 1.0000 | 0.0000 | 36.25R |

### Gate 4: `TECHNICAL` — Regime x Naive Baseline

| Regime | System Type | N | Win Rate | Mean R | 95% CI | p vs Naive | Cohen's d | Max DD |
|--------|-------------|---|----------|--------|--------|------------|-----------|--------|
| BULL | `FULL_SCANNER` | 3346 | 45.1% | +0.2037R | [+0.1591R, +0.2482R] | 0.3891 | 0.0180 | 15.37R |
| BULL | `NAIVE_BASELINE` | 7851 | 44.6% | +0.1801R | [+0.1509R, +0.2094R] | 1.0000 | 0.0000 | 19.25R |
| BEAR | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| BEAR | `NAIVE_BASELINE` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `FULL_SCANNER` | 0 | 0.0% | +0.0000R | [+0.0000R, +0.0000R] | 1.0000 | 0.0000 | 0.00R |
| SIDEWAYS | `NAIVE_BASELINE` | 4403 | 40.4% | +0.0529R | [+0.0165R, +0.0904R] | 1.0000 | 0.0000 | 43.59R |
| OVERALL | `FULL_SCANNER` | 3346 | 45.1% | +0.2037R | [+0.1590R, +0.2480R] | 0.0063 | 0.0534 | 15.37R |
| OVERALL | `NAIVE_BASELINE` | 12254 | 43.1% | +0.1344R | [+0.1115R, +0.1570R] | 1.0000 | 0.0000 | 32.97R |


---

## Correction 6 — MULTI_TF Audit Trail

| Field | Under Contaminated Data | Under Clean Data |
|-------|------------------------|-----------------|
| N | 21 | 63 |
| Mean R | -0.94R | +0.0066R |
| CI | (not computed) | [-0.65R, +1.26R] |
| Label | "unambiguously decommissioned" | no detectable edge |
| **Verdict** | DECOMMISSIONED | **DECOMMISSIONED** |

Decommission direction is correct. Prior reasoning ("unambiguous catastrophic loser")
was based on contaminated data noise, not a clean statistical signal. CI [-0.65R, +1.26R]
spans zero — no actionable edge exists on clean data. Audit trail amended.

---

## Outstanding Verdict Summary

| Scanner | Prior (WRONG) | Corrected | Blocker Resolved |
|---------|--------------|-----------|-----------------|
| TECHNICAL_INTRADAY pooled | CERTIFIED_SIMPLIFIED | DECOMMISSIONED | YES |
| Wyckoff Spring isolated | (not computed) | DECOMMISSIONED | YES |
| REVERSAL | CERTIFIED_CONDITIONAL | DECOMMISSIONED | YES |
| WEALTH_ENGINE | FULLY_CERTIFIED | STATISTICALLY_UNDERPOWERED | YES |
| MULTIBAGGER | FULLY_CERTIFIED | STATISTICALLY_UNDERPOWERED | YES |
| MULTI_TF audit trail | Incomplete | Corrected | YES |
| Gate 4 tables (4 scanners) | Missing | Rendered | YES |
| Wall-clock timing | Uninstrumented | Instrumented | YES (forward) |
