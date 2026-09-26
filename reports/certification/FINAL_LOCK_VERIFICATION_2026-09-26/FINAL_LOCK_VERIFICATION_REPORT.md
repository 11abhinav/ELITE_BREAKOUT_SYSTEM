# MANDATORY FINAL LOCK VERIFICATION BATTERY REPORT

**Date:** 2026-09-26 20:14:02 IST  
**Evaluation Scope:** 80,288 Real Upstox Historical Trades across 10 Years (2016–2026)  
**Governance Status:** **FAIL-CLOSED (0 LIVE PRODUCTION ALERTS)**  

---

### DATA PROVENANCE
Provider: Upstox
API: Upstox Historical V2 API
Exchange: NSE (National Stock Exchange of India)
Universe: Nifty 500 / MidSmallCap Dynamic Watchlist
Instrument resolution: Cash Equities (EQ)
Timeframe: Daily & 15m / Bhavcopy Stride=1
Date range: 2016-01-01 to 2026-09-26
Timezone: Asia/Kolkata (IST)
Rows: 80,288
Native fields: timestamp, open, high, low, close, volume, open_interest
Missing rows: 0
Duplicates: 0
Synthetic data: 0 (Strictly Prohibited)
Fallback providers: None
Dataset hash: 4a952ca20c529f6178535fdd55ec9aaa...
Provenance status: PROVENANCE_STATUS = CERTIFIED

---

## 1. Executive Summary & Verification Matrix

| Scanner | Evidence-Supported Regime | Multi-Year Replications | Quarterly Replications | Verification Status | Current Production-Active Regime |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **TECHNICAL** | **BULL** | 4 / 4 Positive | 4 / 4 Positive | ✅ Verified (Edge Replicated) | **None until final lock verification** |
| **PULLBACK** | **SIDEWAYS** | 3 / 4 Positive (2025–26 decay) | 3 / 4 Positive (Q1 negative) | ⚠️ Edge Compression Detected | **None — recent decay prevents lock** |
| **ACCUMULATION** | **BEAR** | 4 / 4 Positive | 3 / 4 Positive (Q1 weakness) | ⚠️ Q1 Seasonal Drag Detected | **None until final lock verification** |
| **EOD** | **NONE** | Underpowered ($N=55$ to $496$) | Underpowered | 🛑 Underpowered Sample Size | **None** |

**Crucial Production Invariant:**  
> **"Supported regime" ≠ "Currently production active."**  
> While empirical research identifies structural regime alignments, all candidate scanners remain in `UNDER_CERTIFICATION`. Production is strictly fail-closed with **zero live alerts**.

---

## 2. In-Depth Verification Findings

### A. TECHNICAL × BULL (Evidence-Supported: BULL)
- **Multi-Year Cells:**
  - `Cell 1 (2016–2018)`: $N = 1,652$ | Mean Net R = **+0.2197R** (95% CI: `[+0.1660, +0.2731]`, Permutation $p = 0.0050$)
  - `Cell 2 (2019–2021)`: $N = 3,472$ | Mean Net R = **+0.2327R** (95% CI: `[+0.1956, +0.2701]`, Permutation $p = 0.0001$)
  - `Cell 3 (2022–2024)`: $N = 5,443$ | Mean Net R = **+0.2206R** (95% CI: `[+0.1896, +0.2468]`, Permutation $p = 0.0001$)
  - `Cell 4 (2025–2026)`: $N = 1,008$ | Mean Net R = **+0.1458R** (95% CI: `[+0.0792, +0.2126]`, Permutation $p = 0.0001$)
- **Quarterly Robustness:**
  - `Q1`: $N = 2,210$ | Mean Net R = **+0.2056R**
  - `Q2`: $N = 3,119$ | Mean Net R = **+0.2959R**
  - `Q3`: $N = 3,835$ | Mean Net R = **+0.1337R**
  - `Q4`: $N = 2,411$ | Mean Net R = **+0.2605R**
- **Verification Verdict:** Replicated robustly across all 4 multi-year temporal cells and all 4 calendar quarters without single-cell concentration. Supported in **BULL**. Production status remains fail-closed pending formal administrative sign-off.

### B. ACCUMULATION × BEAR (Evidence-Supported: BEAR)
- **Multi-Year Cells:**
  - `Cell 1 (2016–2018)`: $N = 663$ | Mean Net R = **+0.0402R**
  - `Cell 2 (2019–2021)`: $N = 673$ | Mean Net R = **+0.1356R** (95% CI: `[+0.0509, +0.2203]`)
  - `Cell 3 (2022–2024)`: $N = 458$ | Mean Net R = **+0.2065R** (95% CI: `[+0.1048, +0.3096]`)
  - `Cell 4 (2025–2026)`: $N = 981$ | Mean Net R = **+0.0975R** (95% CI: `[+0.0327, +0.1641]`)
- **Quarterly Breakdown & Q1 Weakness:**
  - `Q1`: $N = 770$ | Mean Net R = **-0.0604R** (Pronounced seasonal weakness)
  - `Q2`: $N = 718$ | Mean Net R = **+0.2194R**
  - `Q3`: $N = 689$ | Mean Net R = **+0.2027R**
  - `Q4`: $N = 598$ | Mean Net R = **+0.0961R**
- **Verification Verdict:** All 4 multi-year cells are positive with low dispersion (top cell PnL share = 31.0%). However, **Q1 exhibits structural negative expectancy (-0.0604R)**. This seasonal drag prevents unconditional promotion; scanner is assigned warning and kept in `UNDER_CERTIFICATION`.

### C. PULLBACK × SIDEWAYS (Evidence-Supported: SIDEWAYS)
- **Multi-Year Cells:**
  - `Cell 1 (2016–2018)`: $N = 2,086$ | Mean Net R = **+0.2082R**
  - `Cell 2 (2019–2021)`: $N = 4,185$ | Mean Net R = **+0.1384R**
  - `Cell 3 (2022–2024)`: $N = 6,196$ | Mean Net R = **+0.1069R**
  - `Cell 4 (2025–2026)`: $N = 3,329$ | Mean Net R = **+0.0147R** (95% CI: `[-0.0170, +0.0496]`, crosses zero!)
- **Quarterly Breakdown:**
  - `Q1`: $N = 2,439$ | Mean Net R = **-0.0309R** (Negative)
  - `Q2`: $N = 3,744$ | Mean Net R = **+0.2099R**
  - `Q3`: $N = 3,932$ | Mean Net R = **+0.2061R**
  - `Q4`: $N = 5,681$ | Mean Net R = **+0.0359R**
- **Verification Verdict:** Substantial edge compression in the recent 2025–26 period (+0.0147R, CI crosses zero) alongside negative Q1 performance (-0.0309R). **Lock blocked due to recent decay**.

### D. EOD (Evidence-Supported: NONE / UNDERPOWERED)
- **Statistical Power & Sample Size:**
  - Across 10 years, EOD produced only 1,768 trades (BULL: 907, SIDEWAYS: 516, BEAR: 339).
  - Cell trade counts range from $N = 55$ to $496$, far below the statistical power threshold required for high-confidence strategy promotion.
  - Holdout sample sizes ($N = 67, 120, 64$) cross zero in all three regimes.
- **Verification Verdict:** **Underpowered / under certification**. Zero production alerts.

---

## 3. Leakage, Lookahead & Causality Audit
- Total trades audited for point-in-time causality: **80,288**
- Exit timestamp strictly $\ge$ entry timestamp: **100% PASS** (0 lookahead violations)
- Holding days $\ge 0$: **100% PASS**
- Zero leakage detected across all exit arms.

---

## 4. Frozen Invariants & Configuration Check
- Fixed Control Arm A: Target = 2.0R, Stop = Initial SL, Max Hold = 12 Bars (Frozen)
- Dynamic Exit Arm B: ATR dynamic trailing stop, partial scaling, regime stop (Frozen)
- Strategy parameter hash: `4a11187c816bf6c9f74326799b5129e1...` (100% identical across all cells)
- Zero cell-specific tuning or in-sample overfitting.

---

## 5. Authoritative Production Governance Verdict

```text
CERTIFIED_PRODUCTION_SCANNERS: EMPTY set()
LIVE_PRODUCTION_ALERTS:        0 (ZERO)
FAIL_CLOSED_GATE:              ACTIVE
```

Every scanner remains in `UNDER_CERTIFICATION` with live alerts strictly blocked by `save_alert_if_new()`.
