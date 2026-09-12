# SHORT COVERING 10-VARIANT TOURNAMENT — MASTER REPORT

**Generated**: 2026-09-12 10:51 IST  
**Data**: Real BSE/NSE 1d OHLCV from `data/history/1d/` (68 symbols)  
**Period**: 2025-10-01 → 2026-09-11 (246 trading days)  
**Evaluation Frequency**: 1-in-2 trading days (123 evaluation days)  
**Regime Classifier**: NIFTY 50 20-SMA (min 15 bars) + 5d return, BULL threshold ±0.2%/+0.5%  
**Regimes**: BULL=78 | BEAR=82 | NEUTRAL=86  

---

## 1. TOURNAMENT WINNER

```
  WINNER : V9_LIBERAL_EOD_NOCAP
  Name   : V9 — Liberal EOD + No Cap
  Desc   : EOD score≥35, no cap: maximum recall test. Evaluates opportunity cost of prod filters

  vs Production Baseline (V1):
  ─────────────────────────────────────────────────────────
  Metric          V1 (Prod)   V9_LIBERAL_EOD_NOCAP Delta
  ─────────────────────────────────────────────────────────
  Total Alerts          180                   268     +88
  Win Rate %           96.7                  97.4    +0.7
  Expectancy(R)      +0.894                +0.934  +0.040
  Profit Factor       27.82                 36.75   +8.93
  Max Drawdown         1.00                  1.00   +0.00
  SL Rate %             3.3                   2.6    -0.7
  Rate ≥3R              5.6                   5.2    -0.3
  Rate ≥5R              1.1                   1.1    +0.0
```

---

## 2. FULL VARIANT RANKINGS

| Rank | Variant ID | Alerts | WR% | E[R] | PF | MaxDD | Composite |
|------|-----------|--------|-----|------|----|-------|-----------|
| 1 | V9_LIBERAL_EOD_NOCAP | 268 | 97.4% | +0.934 | 36.75 | 1.00 | 26.3971 |
| 2 | V10_COMPOSITE_OPTIMIZER | 237 | 97.5% | +0.909 | 36.92 | 1.00 | 25.8333 |
| 3 | V8_LIBERAL_EOD | 254 | 97.2% | +0.926 | 34.62 | 1.00 | 24.6720 |
| 4 | V7_NOCAP_RELAXED_OI | 190 | 96.8% | +0.880 | 28.86 | 1.00 | 19.5358 |
| 5 | V6_EOD_NO_CAP | 182 | 96.7% | +0.897 | 28.20 | 1.00 | 19.4473 |
| 6 | V2_RELAXED_OI_GATE | 188 | 96.8% | +0.877 | 28.49 | 1.00 | 19.2243 |
| 7 | V1_PROD_BASELINE | 180 | 96.7% | +0.894 | 27.82 | 1.00 | 19.1323 |
| 8 | V3_STRICT_5M_SCORE | 180 | 96.7% | +0.894 | 27.82 | 1.00 | 19.1323 |
| 9 | V4_PERMISSIVE_5M_SCORE | 180 | 96.7% | +0.894 | 27.82 | 1.00 | 19.1323 |
| 10 | V5_HIGH_CONVICTION_ONLY | 180 | 96.7% | +0.894 | 27.82 | 1.00 | 19.1323 |

---

## 3. REGIME BREAKDOWN (All Variants)

| Variant | Regime | Alerts | WR% | E[R] | PF |
|---------|--------|--------|-----|------|----|
| V1_PROD_BASELINE | BULL | 34 | 94.1% | +0.876 | 15.89 |
| V1_PROD_BASELINE | BEAR | 80 | 97.5% | +0.844 | 34.77 |
| V1_PROD_BASELINE | NEUTRAL | 66 | 97.0% | +0.964 | 32.81 |
| V2_RELAXED_OI_GATE | BULL | 37 | 94.6% | +0.837 | 16.48 |
| V2_RELAXED_OI_GATE | BEAR | 82 | 97.6% | +0.841 | 35.47 |
| V2_RELAXED_OI_GATE | NEUTRAL | 69 | 97.1% | +0.942 | 33.51 |
| V3_STRICT_5M_SCORE | BULL | 34 | 94.1% | +0.876 | 15.89 |
| V3_STRICT_5M_SCORE | BEAR | 80 | 97.5% | +0.844 | 34.77 |
| V3_STRICT_5M_SCORE | NEUTRAL | 66 | 97.0% | +0.964 | 32.81 |
| V4_PERMISSIVE_5M_SCORE | BULL | 34 | 94.1% | +0.876 | 15.89 |
| V4_PERMISSIVE_5M_SCORE | BEAR | 80 | 97.5% | +0.844 | 34.77 |
| V4_PERMISSIVE_5M_SCORE | NEUTRAL | 66 | 97.0% | +0.964 | 32.81 |
| V5_HIGH_CONVICTION_ONLY | BULL | 34 | 94.1% | +0.876 | 15.89 |
| V5_HIGH_CONVICTION_ONLY | BEAR | 80 | 97.5% | +0.844 | 34.77 |
| V5_HIGH_CONVICTION_ONLY | NEUTRAL | 66 | 97.0% | +0.964 | 32.81 |
| V6_EOD_NO_CAP | BULL | 34 | 94.1% | +0.876 | 15.89 |
| V6_EOD_NO_CAP | BEAR | 82 | 97.6% | +0.851 | 35.89 |
| V6_EOD_NO_CAP | NEUTRAL | 66 | 97.0% | +0.964 | 32.81 |
| V7_NOCAP_RELAXED_OI | BULL | 37 | 94.6% | +0.837 | 16.48 |
| V7_NOCAP_RELAXED_OI | BEAR | 84 | 97.6% | +0.848 | 36.59 |
| V7_NOCAP_RELAXED_OI | NEUTRAL | 69 | 97.1% | +0.942 | 33.51 |
| V8_LIBERAL_EOD | BULL | 53 | 96.2% | +0.977 | 26.89 |
| V8_LIBERAL_EOD | BEAR | 106 | 97.2% | +0.859 | 31.36 |
| V8_LIBERAL_EOD | NEUTRAL | 95 | 97.9% | +0.974 | 47.24 |
| V9_LIBERAL_EOD_NOCAP | BULL | 53 | 96.2% | +0.977 | 26.89 |
| V9_LIBERAL_EOD_NOCAP | BEAR | 117 | 97.4% | +0.876 | 35.18 |
| V9_LIBERAL_EOD_NOCAP | NEUTRAL | 98 | 98.0% | +0.979 | 48.97 |
| V10_COMPOSITE_OPTIMIZER | BULL | 47 | 95.7% | +0.907 | 22.33 |
| V10_COMPOSITE_OPTIMIZER | BEAR | 100 | 98.0% | +0.869 | 44.45 |
| V10_COMPOSITE_OPTIMIZER | NEUTRAL | 90 | 97.8% | +0.956 | 44.00 |

---

## 4. ANNUAL PERFORMANCE

| Variant | Year | Alerts | WR% | E[R] | Total R |
|---------|------|--------|-----|------|---------|
| V1_PROD_BASELINE | 2025 | 37 | 97.3% | +0.974 | +36.0 |
| V2_RELAXED_OI_GATE | 2025 | 39 | 97.4% | +0.949 | +37.0 |
| V3_STRICT_5M_SCORE | 2025 | 37 | 97.3% | +0.974 | +36.0 |
| V4_PERMISSIVE_5M_SCORE | 2025 | 37 | 97.3% | +0.974 | +36.0 |
| V5_HIGH_CONVICTION_ONLY | 2025 | 37 | 97.3% | +0.974 | +36.0 |
| V6_EOD_NO_CAP | 2025 | 37 | 97.3% | +0.974 | +36.0 |
| V7_NOCAP_RELAXED_OI | 2025 | 39 | 97.4% | +0.949 | +37.0 |
| V8_LIBERAL_EOD | 2025 | 61 | 98.4% | +0.980 | +59.8 |
| V9_LIBERAL_EOD_NOCAP | 2025 | 64 | 98.4% | +0.988 | +63.3 |
| V10_COMPOSITE_OPTIMIZER | 2025 | 56 | 98.2% | +0.931 | +52.1 |

---

## 5. EOD 35-STOCK CAP IMPACT ANALYSIS

This section isolates the impact of limiting the EOD watchlist to 35 stocks.

| Metric | V1 (cap=35) | V6 (uncapped) | V8 (score≥35, cap=35) | V9 (score≥35, uncapped) |
|--------|------------|---------------|----------------------|------------------------|
| Alerts | 180 | 182 | 254 | 268 |
| WR%    | 96.7 | 96.7 | 97.2 | 97.4 |
| E[R]   | +0.894 | +0.897 | +0.926 | +0.934 |
| PF     | 27.82 | 28.20 | 34.62 | 36.75 |
| MaxDD  | 1.00 | 1.00 | 1.00 | 1.00 |

**Cap Verdict (V1 vs V6)**:
- Additional alerts unlocked by removing cap: **+2**
- E[R] delta (uncapped vs capped): **+0.0026R**
- **→ Cap impact is marginal. The 35-cap neither meaningfully helps nor hurts.**

---

## 6. RECOMMENDATION

**Deploy Variant**: `V9_LIBERAL_EOD_NOCAP`  
**EOD Parameters**: score≥35.0, cap=NONE  
**5m Parameters**: score≥65.0, OI≤-0.5%  

**Files**:
- DB: `data/sc_10variant_tournament.db`
- Events CSV: `reports/sc_10variant_events.csv`
- Regime CSV: `reports/sc_10variant_regime_breakdown.csv`
- JSON: `reports/sc_10variant_results.json`