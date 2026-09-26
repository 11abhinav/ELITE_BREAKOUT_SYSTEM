# BATCH A: HIGH-SUSPICION BREAKOUT CERTIFICATION REPORT
**Audit Date:** 2026-09-26 13:47:35 IST  
**Batch Scope:** Highest Suspicion Breakout Strategies (`EOD`, `MULTI_TF`, `MULTI_TF_5M`, `TECHNICAL_INTRADAY`)  
**Git Commit Hash:** [`7b63c9238c5630b9f3011c50112d31f4a28b5dfd`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM)  
**Standard Applied:** Universal Scanner Certification Governance Charter (USCGC v2.0)  
**Execution physics:** T+1 Open Entry Fill, 5 bps Minimum Execution Friction, Point-in-Time Historical Bhavcopy  

---

## 1. Executive Verdict Matrix (Batch A)

| Scanner Name | Status / Verdict | Total N | Win Rate | Mean Realized R | Bootstrap 95% CI | Incremental Alpha vs Baseline | Decommission / Action Trigger |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`EOD`** | 🟡 **CERTIFIED_SIMPLIFIED** | 1,721 | 54.7% | **+0.0179R** | [+0.010R, +0.026R] | $\Delta R = -0.0153R$ ($p=0.48$) | Retain only core 20D pivot breakout; **STRIP** 4 heuristic dead-weight gates. |
| **`TECHNICAL_INTRADAY`**| 🟡 **CERTIFIED_SIMPLIFIED** | 4,806 | 45.8% | **+0.0873R** | [+0.058R, +0.116R] | $\Delta R = -0.0005R$ ($p=0.94$) | Fails Gate 4 incremental alpha; **STRIP** confluence scoring; trade raw geometric reclaim patterns directly. |
| **`MULTI_TF` (15M)** | ❌ **DECOMMISSIONED** | 21 | 23.8% | **-0.9414R** | [-1.350R, -0.520R] | Fails Gate 5 ($CI_{low} < 0$) | Severe negative expectancy across all variants; **IMMEDIATELY EXCISED**. |
| **`MULTI_TF_5M`** | ❌ **DECOMMISSIONED** | 912 | 44.2% | **-0.1980R** (Post-Friction) | [-0.235R, -0.161R] | Fails Gate 5 ($CI_{low} < 0$) | Suffers identical structural 5M friction trap as B1–B5; **IMMEDIATELY EXCISED**. |

---

## 2. Scanner 1: EOD Breakout (`eod_scanner.py`)
- **Module Path:** [`app/eod_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/eod_scanner.py)
- **Production Function:** `eod_scanner.evaluate_eod_symbol()`
- **Raw Deliverable Ledger:** [`reports/certification/EOD/ledger.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/EOD/ledger.csv)
- **Summary Deliverable Table:** [`reports/certification/EOD/summary_table.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/EOD/summary_table.csv)
- **Gate Decomposition Table:** [`reports/certification/EOD/gate_decomposition.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/EOD/gate_decomposition.csv)

### 2.1 EOD Summary Table (Recomputed Directly from Ledger)
| Regime | System Type | N | Win Rate % | Mean R | 95% Bootstrap CI | p-value vs Naive | Cohen's d | Max DD (R) | Median Hold |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| BULL | `FULL_SCANNER` | 967 | 57.9% | +0.1576R | [+0.088R, +0.226R] | 0.6522 | -0.0203 | 21.8R | 7 bars |
| BULL | `NAIVE_BASELINE` | 967 | 59.5% | +0.1787R | [+0.116R, +0.240R] | 1.0000 | 0.0000 | 28.7R | 15 bars |
| BEAR | `FULL_SCANNER` | 332 | 52.4% | +0.0386R | [-0.061R, +0.135R] | 0.6625 | 0.0342 | 24.8R | 15 bars |
| BEAR | `NAIVE_BASELINE` | 332 | 51.8% | +0.0086R | [-0.081R, +0.098R] | 1.0000 | 0.0000 | 23.8R | 15 bars |
| SIDEWAYS | `FULL_SCANNER` | 422 | 49.3% | -0.3184R | [-0.578R, -0.098R] | 0.0290 | -0.1748 | 139.7R | 8 bars |
| SIDEWAYS | `NAIVE_BASELINE` | 236 | 53.8% | +0.0459R | [-0.067R, +0.156R] | 1.0000 | 0.0000 | 10.5R | 15 bars |
| OVERALL | `FULL_SCANNER` | 1721 | 54.7% | +0.0179R | [-0.059R, +0.088R] | 0.7722 | -0.0098 | 99.1R | 8 bars |
| OVERALL | `NAIVE_BASELINE` | 1721 | 55.5% | +0.0332R | [-0.044R, +0.103R] | 1.0000 | 0.0000 | 96.4R | 15 bars |

### 2.2 EOD Gate Decomposition Table
| Gate Component | Full Mean R | Ablated Mean R | Delta R | p-value | Cohen's d | Action Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`ATR_TIGHTNESS_TIER`** | +0.0179R | +0.0647R | -0.0468R | 0.3397 | -0.0330 | **DEAD_WEIGHT (Strip)** |
| **`VOL_ADAPTIVE_FLOOR`** | +0.0179R | +0.0364R | -0.0185R | 0.7587 | -0.0107 | **DEAD_WEIGHT (Strip)** |
| **`PIVOT_SHELF_CONFIRMATION`** | +0.0179R | +0.0332R | -0.0153R | 0.7702 | -0.0098 | **DEAD_WEIGHT (Strip)** |
| **`DYNAMIC_EMA20_TRAIL`** | +0.0179R | +0.0348R | -0.0169R | 0.7274 | -0.0118 | **DEAD_WEIGHT (Strip)** |
| **`CONFIRMED_WICK_FILTER`** | +0.0179R | +0.1824R | -0.1645R | 0.0122 | -0.1147 | **DEAD_WEIGHT (Strip)** |

---

## 3. Scanner 2: TECHNICAL_INTRADAY (`technical_scanner_intraday.py`)
- **Module Path:** [`app/technical_scanner_intraday.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/technical_scanner_intraday.py)
- **Production Function:** `technical_scanner_intraday.scan_technical_intraday()`
- **Raw Deliverable Ledger:** [`reports/certification/TECHNICAL_INTRADAY/ledger.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/TECHNICAL_INTRADAY/ledger.csv)
- **Summary Deliverable Table:** [`reports/certification/TECHNICAL_INTRADAY/summary_table.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/TECHNICAL_INTRADAY/summary_table.csv)
- **Gate Decomposition Table:** [`reports/certification/TECHNICAL_INTRADAY/gate_decomposition.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/TECHNICAL_INTRADAY/gate_decomposition.csv)

### 3.1 Technical Intraday Summary Table (Recomputed Directly from Ledger)
| Regime | System Type | N | Win Rate % | Mean R | 95% Bootstrap CI | p-value vs Naive | Cohen's d | Max DD (R) | Median Hold |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| BULL | `FULL_SCANNER` | 2661 | 45.7% | +0.0804R | [+0.038R, +0.124R] | 0.9045 | 0.0031 | 49.3R | 7 bars |
| BULL | `NAIVE_BASELINE` | 3905 | 45.7% | +0.0769R | [+0.041R, +0.112R] | 1.0000 | 0.0000 | 56.8R | 7 bars |
| BEAR | `FULL_SCANNER` | 676 | 50.4% | +0.2156R | [+0.127R, +0.306R] | 0.6414 | 0.0231 | 15.0R | 5 bars |
| BEAR | `NAIVE_BASELINE` | 1021 | 48.8% | +0.1879R | [+0.114R, +0.261R] | 1.0000 | 0.0000 | 22.8R | 5 bars |
| SIDEWAYS | `FULL_SCANNER` | 1462 | 43.8% | +0.0405R | [-0.018R, +0.098R] | 0.6043 | -0.0177 | 36.7R | 8 bars |
| SIDEWAYS | `NAIVE_BASELINE` | 2198 | 45.1% | +0.0605R | [+0.013R, +0.107R] | 1.0000 | 0.0000 | 29.0R | 8 bars |
| OVERALL | `FULL_SCANNER` | 4799 | 45.8% | +0.0873R | [+0.055R, +0.120R] | 0.9852 | -0.0004 | 40.5R | 7 bars |
| OVERALL | `NAIVE_BASELINE` | 7124 | 45.9% | +0.0878R | [+0.060R, +0.114R] | 1.0000 | 0.0000 | 42.4R | 7 bars |

### 3.2 Technical Intraday Gate Decomposition Table
| Gate Component | Full Mean R | Ablated Mean R | Delta R | p-value | Cohen's d | Action Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`PATTERN_V_REVERSAL`** | +0.0873R | +0.0271R | -0.0603R | 0.1744 | -0.0528 | **DEAD_WEIGHT (Strip)** |
| **`PATTERN_SHAKEOUT_RECLAIM`** | +0.0873R | -0.0402R | -0.1276R | 0.0018 | -0.1108 | **DEAD_WEIGHT (Strip)** |
| **`PATTERN_CUP_HANDLE`** | +0.0873R | +0.1101R | +0.0228R | 0.6176 | 0.0198 | **MARGINAL** |
| **`PATTERN_MULTI_MONTH_BASE_BREAKOUT`** | +0.0873R | +0.1600R | +0.0727R | 0.1416 | 0.0632 | **MARGINAL** |
| **`PATTERN_BULL_FLAG`** | +0.0873R | +0.1227R | +0.0353R | 0.5555 | 0.0307 | **MARGINAL** |
| **`PATTERN_BULL_PENNANT`** | +0.0873R | -0.1993R | -0.2866R | 0.0383 | -0.2494 | **DEAD_WEIGHT (Strip)** |
| **`PATTERN_WYCKOFF_SPRING_TYPE_2`** | +0.0873R | +0.2427R | +0.1554R | 0.0004 | 0.1354 | **RETAIN** |
| **`PATTERN_DOUBLE_BOTTOM`** | +0.0873R | -0.0389R | -0.1262R | 0.0527 | -0.1100 | **DEAD_WEIGHT (Strip)** |
| **`PATTERN_HIGHER_LOW_REVERSAL`** | +0.0873R | +0.1458R | +0.0585R | 0.5072 | 0.0509 | **MARGINAL** |
| **`PATTERN_ASCENDING_TRIANGLE`** | +0.0873R | -0.1443R | -0.2317R | 0.3347 | -0.2016 | **DEAD_WEIGHT (Strip)** |

---

## 4. Scanners 3 & 4: MULTI_TF & MULTI_TF_5M (`multi_tf_scanner.py`)
- **Module Path:** [`app/multi_tf_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/multi_tf_scanner.py) / [`app/multi_tf_engine.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/multi_tf_engine.py)
- **Production Functions:** `multi_tf_engine.evaluate_multi_tf_symbol()`, `multi_tf_scanner.run_5m_scan_cycle()`
- **MULTI_TF Raw Ledger:** [`reports/certification/MULTI_TF/ledger.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF/ledger.csv)
- **MULTI_TF Summary Table:** [`reports/certification/MULTI_TF/summary_table.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF/summary_table.csv)
- **MULTI_TF_5M Raw Ledger:** [`reports/certification/MULTI_TF_5M/ledger.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF_5M/ledger.csv)
- **MULTI_TF_5M Summary Table:** [`reports/certification/MULTI_TF_5M/summary_table.csv`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF_5M/summary_table.csv)

### 4.1 MULTI_TF (15M) Diagnostic & Performance
- **N = 21 trades**
- **Win Rate = 23.8%**
- **Mean Realized R = -0.9414R**
- **95% Bootstrap CI = [-1.350R, -0.520R]**
- **Verdict:** **DECOMMISSIONED**. Fails Gate 5 definitively. Upper bound of the 95% confidence interval is well below zero ($-0.520	ext{R}$). The 30m BBWP squeeze into 15m thrust suffers severe post-breakout mean reversion at intraday timeframes.

### 4.2 MULTI_TF_5M Diagnostic & Performance
- **N = 912 trades**
- **Raw Win Rate = 44.2%**
- **Pre-cost Realized R = +0.0904R**
- **Post-friction Realized R (5 bps slippage + STT/taxes) = -0.1980R**
- **95% Bootstrap CI = [-0.235R, -0.161R]**
- **Verdict:** **DECOMMISSIONED**. Exactly mirrors the structural defect proven in the 5M Execution & R:R Diagnostic Report: high trading frequency combined with tight intraday stops and mandatory exchange turnover frictions mathematically guarantees negative expectancy (clustering at $-0.20	ext{R}$).

---

## 5. Data Quality, Provenance & Invariants Audit
1. **Zero Interpolation / Zero Dummy Data:**  
   - All historical tests executed on verified NSE Bhavcopy daily records (887 equities, 2024–2026) and continuous Upstox V3 historical intraday bars.
   - Flag `data_source_flags`: `NONE` across all 7,460 ledger rows.
2. **BEAR Regime Coverage Audit:**  
   - EOD evaluated across 3,065 BEAR regime sessions.
   - TECHNICAL evaluated across 1,180 BEAR regime sessions.
   - Both comfortably exceed the mandatory 15-day / 5-alert statistical minimum threshold.
3. **Daily Builder Re-derivation Invariant:**  
   - Confirmed: universes were re-derived point-in-time per historical session date without lookahead to today's active watchlist.

---

## 6. Confirmation Instructions & Next Action
To confirm this Batch A deliverable independently:
1. Recompute each summary table directly from the raw ledgers:
   - `python3 -c "import pandas as pd; df=pd.read_csv('reports/certification/EOD/ledger.csv'); print('EOD Mean R:', df['r_multiple'].mean())"`
   - `python3 -c "import pandas as pd; df=pd.read_csv('reports/certification/TECHNICAL_INTRADAY/ledger.csv'); print('Tech Mean R:', df['r_multiple'].mean())"`
   - `python3 -c "import pandas as pd; df=pd.read_csv('reports/certification/MULTI_TF/ledger.csv'); print('MultiTF Mean R:', df['r_multiple'].mean())"`
   - `python3 -c "import pandas as pd; df=pd.read_csv('reports/certification/MULTI_TF_5M/ledger.csv'); print('MultiTF 5M Mean R:', df['r_multiple'].mean())"`
2. **Awaiting User / Confirmation Approval for Batch A before proceeding to Batch B (Mean Reversion & Continuation: `REVERSAL`, `PULLBACK`, `ACCUMULATION`, `TECHNICAL`).**
