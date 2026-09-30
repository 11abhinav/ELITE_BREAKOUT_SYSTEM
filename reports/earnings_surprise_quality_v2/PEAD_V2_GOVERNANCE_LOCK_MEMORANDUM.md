# FORMAL GOVERNANCE MEMORANDUM: EARNINGS_SURPRISE_QUALITY_V2

**Document ID:** `GOV-MEMO-PEAD-V2-2026-09-30`  
**Date:** 2026-09-30 16:40:00 IST  
**Authoritative Verdict:** **RESEARCH ONLY — NOT CERTIFIED FOR STANDALONE PRODUCTION — ZERO PRODUCTION ALERTS**  
**Classification:** Research Feature (Relative Edge Observed in Current Sample)  

---

## 1. Executive Summary & Audit Matrix

| Dimension | Empirical Finding | Statistical Proof | Governance Verdict |
| :--- | :---: | :---: | :---: |
| **Real Market Data Provenance** | 100% Native Upstox 1D | Upstox API V2; zero synthetic values | ✅ **PASS** |
| **T+1 Execution Reconstruction** | 20 / 20 Exact Matches | External spot checks corroborated (`INDGN`, `AJANTPHARM`, `MCX`) | ✅ **PASS** |
| **Relative 60D Net Edge (Beat vs. Miss)** | **+2.96%** | $p = 0.0373$ (nominal permutation, unadjusted) | ⚠️ **RELATIVE EDGE OBSERVED IN SAMPLE** |
| **Relative MFE Edge (Beat vs. Miss)** | **+2.22%** | $p = 0.0406$ (nominal permutation, unadjusted) | ⚠️ **RELATIVE EDGE OBSERVED IN SAMPLE** |
| **Relative MAE Edge (Beat vs. Miss)** | **+3.22%** | $p = 0.0013$ (significantly smaller adverse drawdown) | ⚠️ **RELATIVE EDGE OBSERVED IN SAMPLE** |
| **Standalone 60D Strong Beat Return** | **-0.29%** | 95% Bootstrap CI: `[-2.30%, +1.86%]` | ❌ **FAIL (CROSSES ZERO)** |
| **Temporal Replication** | 94.3% in 2026 | Dominant single calendar year; 0 multi-year replication | ❌ **FAIL (TEMPORALLY CONCENTRATED)** |
| **Production State** | Zero Live Alerts | Enforced in `governance_registry.py` | 🔒 **LOCKED (UNDER_CERTIFICATION)** |

---

## 2. Core Empirical Takeaway: Signal vs. Standalone Strategy

The experiment decisively decouples two distinct questions:

### Question 1: Can earnings surprise distinguish better outcomes from misses?
- **Current Evidence:** **YES / Promising (Relative Edge Observed in Current Sample)**.
- `STRONG_BEAT` consistently out-drifts `MISS` across horizons:
  - 60D Net Return: $+2.96\%$ relative ($p = 0.0373$, nominal)
  - MFE: $+2.22\%$ relative ($p = 0.0406$, nominal)
  - MAE: $+3.22\%$ relative ($p = 0.0013$, nominal)
- In correction regimes (2026Q3), `STRONG_BEAT` dropped $-2.74\%$ while `MISS` collapsed $-8.49\%$ ($\Delta = +5.76\%$).
- *Statistical Caution:* Reported $p$-values are nominal and not yet adjusted for the full family of hypotheses/metrics tested. They reflect an observed relative effect within this sample, not final certification.

### Question 2: Can a trader simply buy a STRONG_BEAT and achieve positive standalone returns?
- **Current Evidence:** **NO / Fails Standalone Long Deployment**.
- Standalone 60D return for `STRONG_BEAT` is **$-0.29\%$** and the 95% confidence interval spans `[-2.30%, +1.86%]`, crossing zero widely.
- Buying unconditioned earnings surprises in falling or consolidating markets yields flat-to-negative absolute returns.

---

## 3. The Primary Certification Blocker: Temporal Concentration

Because contiguous 9-quarter depth in the local filing dataset currently begins in 2024, **94.3% of all priced events occurred in 2026**:
- `2023Q3 - 2025Q4`: $N = 15$ events across 10 quarters
- `2026Q1 - 2026Q3`: $N = 353$ events (95.9% of all events)

Under Section 3 & Section 13 of the Mandatory Temporal Replication Gate ([AGENTS.md](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/AGENTS.md)):
- A strategy dominated by a single year cannot be certified for production.
- Any observed edge must independently survive across multiple years, multiple periods, and multiple market episodes.
- Confluence testing (combining SUE with `TECHNICAL` or `FUNDAMENTAL`) on 2026 data alone would risk fitting a 2026-specific feature interaction.

---

## 4. Frozen Invariants & Research Directives

The following remain 100% untouched and frozen:
- **Pre-Event Quality Gate**:
  - $\text{ROCE}_{5Y} \ge 15.0\%$
  - $\text{Sales CAGR}_{5Y} \ge 10.0\%$
  - $\text{D/E} \le 0.50$
  - $\text{CFO/PAT}_{5Y} \ge 0.80$
- **Signal Calculation**:
  - Exactly 9 contiguous historical quarters ($t-8$ through $t$).
  - YoY SUE = `(EPS_t - EPS_{t-4}) / std(prior 4 YoY surprises)`
  - `STRONG_BEAT`: $\text{SUE} \ge +1.5$
  - Next-session $T+1$ Open execution
  - 5.0 bps round-trip friction
- **Zero Production Routing**:
  - `EARNINGS_SURPRISE_QUALITY_V2` remains strictly locked in `UNDER_CERTIFICATION (ZERO PRODUCTION ALERTS)`.
  - Zero live alerts, zero scheduling, zero confluence deployment on 2026 alone.

---

## 5. Mandatory Backfill Acceptance Criteria (10 Gates)

Before the multi-year study can be evaluated, the historical backfill must prove:
1. **Genuinely Point-in-Time (PIT)**: Historical filings accurately represent data known at filing time.
2. **Filing Availability Timestamp**: Official filing timestamp / date is strictly respected.
3. **Zero Lookahead Leakage**: No subsequent disclosures or announcements leak backward.
4. **Zero Synthetic Quarterly Values**: 100% authentic filing numbers; zero synthetic interpolation or imputation.
5. **Contiguous 9-Quarter Chain**: Each candidate event has an uninterrupted 9-quarter sequence ($t-8$ to $t$).
6. **Restatement Isolation**: Subsequent financial restatements do not overwrite historically available knowledge.
7. **Deterministic Trading Calendar**: Historical $T+1$ sessions cleanly skip exchange holidays and weekends.
8. **Reconstructed Price Provenance**: Every historical price is sourced directly from native Upstox historical candles.
9. **Dataset Versioning & Hash**: Complete dataset fingerprinting (SHA256) and version isolation.
10. **Independent Cell Partitioning**: Event counts and metrics must be reported separately across all 4 temporal cells.

---

## 6. Authoritative 4-Cell Replication Matrix Target

| Temporal Cell | Period | Events ($N$) | STRONG_BEAT ($N$) | 60D Hold Return | MFE | MAE | OOS Result | Bootstrap Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Cell 1** | 2016–2018 | — | — | — | — | — | — | — | PENDING BACKFILL |
| **Cell 2** | 2019–2021 | — | — | — | — | — | — | — | PENDING BACKFILL |
| **Cell 3** | 2022–2024 | — | — | — | — | — | — | — | PENDING BACKFILL |
| **Cell 4** | 2025–2026 | 368* | 136* | -0.29%* | +8.88%* | -10.79%* | Current | Current | OBSERVED (CONCENTRATED) |

*\* Current sample figures from existing 2026-dominant study.*

---

## 7. Next Milestone

```text
2016–2024 PIT QUARTERLY BACKFILL COMPLETE → 4-CELL TEMPORAL REPLICATION
```
Only after all 4 temporal cells are calculated and adjusted for multiple testing will `EARNINGS_SURPRISE_QUALITY_V2` be considered for promotion as a validated research feature or permanently decommissioned.
