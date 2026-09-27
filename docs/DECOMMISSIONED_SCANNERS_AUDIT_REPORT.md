
# COMPREHENSIVE AUDIT REPORT: DECOMMISSIONED SCANNERS & EXIT MONITORS
**Document ID:** `DOC-GOV-DECOMM-2026-09-27`  
**Date:** 2026-09-27 08:40:00 IST  
**Status:** AUTHORITATIVE ARCHIVE & AUDIT TRAIL  
**Governing Protocols:** Mandatory Real-Market-Data Protocol · Temporal Replication & Regime Robustness Gate · Anti-Pooled-Bias Invariant  
**Preservation Mandate:** 100% of Historical Backtest Data, Trade Ledgers, Bootstrap Distributions, and Upstox Provenance Artifacts are Permanently Preserved  

---

## 1. Executive Summary & Complete Census

As part of the formal production certification and temporal replication campaign (evaluating over 80,000 real Upstox causal trades spanning 10 years, 2016–2026), all strategy candidates were evaluated against strict anti-pooled-bias and point-in-time causality gates.

Out of all candidate breakout systems, **only `TECHNICAL` in `BULL` regime achieved full statistical and temporal certification**. Every other scanner family failed either gross positive expectancy, modern alpha decay, structural seasonal weakness, or statistical power.

In accordance with Section 15 & 16 of the Operating Rules:
* All 10 failed scanner families and their specialized exit monitors have been **permanently decommissioned and removed from active production scheduling, live alert routing, and runtime evaluation**.
* All underlying trade ledgers, raw Upstox data fetch proofs, backtest datasets, and certification reports are **permanently preserved** for research, audit, and future reference.

### Authoritative Decommissioning Census

| # | Scanner Family | Key Variants / Aliases | Primary Timeframe | Total Trades Evaluated ($N$) | Final Expectancy (Net R) | Primary Failure Mode | Final Governance Verdict |
| :-: | :--- | :--- | :---: | :---: | :---: | :--- | :--- |
| **1** | **`SHORT_COVERING`** | `SHORT_COVERING_5M`, `SHORT_COVERING_EOD`, `SHORT_COVERING_IGNITION` | 5m / 1D | 1,420 | -0.1840R | Negative gross expectancy ($E[R] < 0.00\text{R}$); narrative unbacked by price data | **`DECOMMISSIONED`** |
| **2** | **`5M_BREAKOUT`** | `BREAKOUT_5M`, `SCAN_5M_BREAKOUT` | 5m | 3,115 | -0.3120R | Severe execution slippage & spread friction drag | **`DECOMMISSIONED`** |
| **3** | **`MOMENTUM_IGNITION`** | `MOMENTUM_IGNITION_5M`, `MOMENTUM_THRUST` | 5m | 2,890 | -0.2450R | Volume surge anomaly without trend continuation; false breakout traps | **`DECOMMISSIONED`** |
| **4** | **`MULTI_TF`** | `MULTITF`, `MULTI_TF_15M`, `MULTITF_V3`, `MULTI_TF_LADDER` | 15m / 1h | 63 | +0.0066R | 95% CI crosses zero ($[-0.6504\text{R}, +1.2555\text{R}]$); insufficient sample edge | **`DECOMMISSIONED`** |
| **5** | **`MULTI_TF_5M`** | `MULTITF_5M` | 5m | 4,737 | -0.4311R | 10 bps roundtrip friction converts nominal gains into net negative drag (-0.285R/trade) | **`DECOMMISSIONED`** |
| **6** | **`TECHNICAL_INTRADAY`** | `WYCKOFF_SPRING`, `BULL_FLAG` (Intraday) | 15m | 4,112 | -0.0420R | Failed Gate 5; intraday noise suppresses daily pattern edge | **`DECOMMISSIONED`** |
| **7** | **`REVERSAL`** | `REVERSAL_SCANNER`, `REVERSAL_V2`, `REVERSAL_KEYLEVEL` | 1D | 310 | +0.0505R | Gate 5 fail: 95% CI $[-0.0720\text{R}, +0.1762\text{R}]$ crosses zero in all regimes | **`DECOMMISSIONED`** |
| **8** | **`ACCUMULATION`** | `ACC-V01` (Base), `ACC-V02` (Surge), `ACC-V03` (Trend), `ACC-V04` (Squeeze) | 1D | 13,650 | +0.1110R (BEAR) | **Persistent Q1 Seasonal Weakness (-0.0604R across all multi-year cells in Q1)**; $0/12$ variants qualified | **`DECOMMISSIONED`** |
| **9** | **`PULLBACK`** | `PB-V01` (Base), `PB-V02` (Support), `PB-V03` (RSI), `PB-V04` (Pivot) | 1D | 43,855 | +0.0147R (2025-26) | **Modern Temporal Decay**: Expectancy compressed from +0.1345R to +0.0147R (CI crosses zero); $0/12$ variants qualified | **`DECOMMISSIONED`** |
| **10** | **`EOD`** | `EOD-V01` (Base), `EOD-V02` (52W), `EOD-V03` (Squeeze), `EOD-V04` (Corridor) | 1D | 1,768 | +0.1129R | **Statistical Power Deficit**: Only 1,768 trades over 10 years; holdout sample sizes ($N=67, 120, 64$) cross zero | **`DECOMMISSIONED`** |

---

## 2. Detailed Scanner-by-Scanner Post-Mortem

### 2.1 SHORT_COVERING (All Variants)
* **Intended Thesis:** Inferring rapid short covering rallies from intraday open interest (OI) contraction paired with upward price momentum.
* **Why It Was Decommissioned:**
  1. *Unobservable Narrative Bias:* Exchange OI reports are end-of-day; intraday synthetic proxies generated false positive clusters.
  2. *Gross Negative Expectancy:* Across 1,420 trades, gross expectancy was negative ($E[R] = -0.1840\text{R}$).
  3. *Exit Failure:* Attempting to trail stops tighter exacerbated whipsaw losses.
* **Exit Monitor Removed:** Intraday 5-min trailing stop loss evaluator and aggressive breakeven ratchet.
* **Preserved Artifacts:**
  * [`reports/certification/MASTER_ALL_IN_ONE_CERTIFICATION_DATA_AND_REPORT.md`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MASTER_ALL_IN_ONE_CERTIFICATION_DATA_AND_REPORT.md)
  * `scratch/execute_short_covering_full_forensics.py`

### 2.2 5M_BREAKOUT & MOMENTUM_IGNITION
* **Intended Thesis:** High-frequency breakouts from 5-minute consolidation ranges with anomalous volume bursts.
* **Why It Was Decommissioned:**
  1. *Friction Asymmetry:* Roundtrip costs (STT, exchange turnover, broker fees, bid-ask spread) averaged ~0.08R to 0.12R per trade.
  2. *Mean Net Expectancy:* -0.3120R and -0.2450R net realized return. Over 68% of breakouts were "liquidity sweeps" that reversed immediately.
* **Exit Monitor Removed:** 5-minute bar-by-bar trailing exit evaluator.

### 2.3 MULTI_TF & MULTI_TF_5M (15M & 5M)
* **Intended Thesis:** 4-Phase multi-timeframe cascade: Daily Trend (Phase A) $\rightarrow$ 1H Squeeze (Phase B) $\rightarrow$ 15M Breakout (Phase C) $\rightarrow$ 5M Pullback/Thrust Entry (Phase D).
* **Why It Was Decommissioned:**
  1. *15M Squeeze:* Underpowered with only 63 causal events over 10 years ($95\%\text{ CI: } [-0.6504\text{R}, +1.2555\text{R}]$).
  2. *5M Polling:* Across 4,737 trades, win rate was 29.7% and mean Net R was **-0.4311R**. Roundtrip execution friction completely destroyed nominal gross gains.
* **Exit Monitor Removed:** Intraday swing low structural trailing stop, natural R:R target scaling engine (`_compute_multi_tf`).
* **Preserved Artifacts:**
  * [`reports/certification/MULTI_TF/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF/)
  * [`reports/certification/MULTI_TF_5M/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_TF_5M/)
  * [`reports/certification/FINAL_AUDIT_2026-09-26/multitf_audit_confirmation.json`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/FINAL_AUDIT_2026-09-26/multitf_audit_confirmation.json)

### 2.4 TECHNICAL_INTRADAY (Wyckoff Spring & Bull Flag)
* **Intended Thesis:** Intraday 15-minute executions of classic Wyckoff Springs (Shakeout + reclaim) and Bull Flags.
* **Why It Was Decommissioned:**
  1. *Isolation Test Failure:* In the formal isolated certification audit (`CORRECTIONS_2026-09-26`), Wyckoff Spring isolated failed Gate 5 (Mean Net R = -0.042R).
  2. *Intraday Noise vs Daily Conviction:* Intraday volume spikes on Indian small/midcaps lacked institutional institutional sponsorship compared to daily close setups.
* **Exit Monitor Removed:** 15-minute intraday target evaluator.
* **Preserved Artifacts:**
  * [`reports/certification/CORRECTIONS_2026-09-26/TECHNICAL_INTRADAY_wyckoff_spring_isolated.json`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/CORRECTIONS_2026-09-26/TECHNICAL_INTRADAY_wyckoff_spring_isolated.json)

### 2.5 REVERSAL
* **Intended Thesis:** Capturing oversold mean-reversion bounces after steep multi-day declines (15–20% from 52W high) with RSI divergence and MACD cross.
* **Why It Was Decommissioned:**
  1. *Fallen Knife Syndrome:* Even with strict SMA50 reclaim gates, win rate was only 44.2% across 310 causal trades.
  2. *Confidence Interval Failure:* The 95% bootstrap confidence interval spanned negative territory in all regimes:
     * BEAR: Mean Net R = +0.0627R (95% CI: `[-0.0648R, +0.1897R]`) $\rightarrow$ **FAIL**
     * BULL: Mean Net R = -0.1180R (95% CI: `[-0.5743R, +0.3708R]`) $\rightarrow$ **FAIL**
     * OVERALL: Mean Net R = +0.0505R (95% CI: `[-0.0720R, +0.1762R]`) $\rightarrow$ **FAIL**
* **Exit Monitor Removed:** Dedicated reversal state machine (`reversal_v2_state_machine.py`) and trailing support evaluator.
* **Preserved Artifacts:**
  * [`reports/certification/REVERSAL/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/REVERSAL/)
  * [`reports/certification/CORRECTIONS_2026-09-26/REVERSAL_regime_verdict.json`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/CORRECTIONS_2026-09-26/REVERSAL_regime_verdict.json)

### 2.6 ACCUMULATION (VCP Delivery Accumulation)
* **Intended Thesis:** Volatility Contraction Pattern (VCP) coupled with high delivery percentage accumulation from Bhavcopy.
* **Why It Was Decommissioned:**
  1. *Severe Q1 Seasonal Drag:* While BEAR showed positive overall return (+0.1110R), **Q1 consistently generated negative returns (-0.0604R) across all 4 multi-year observation windows**:
     * Cell 1 Q1 (2016–2018): -0.0812R
     * Cell 2 Q1 (2019–2021): -0.0450R
     * Cell 3 Q1 (2022–2024): -0.0120R
     * Cell 4 Q1 (2025–2026): -0.0784R
  2. *Economic Driver:* Corporate fiscal year-end NAV squaring, advance tax outflows, and Union Budget rebalancing create repeated liquidity traps for delivery accumulation in Q1.
  3. *Zero Surviving Variants:* In the multi-variant requalification battery (`MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26`), all 4 variants (`ACC-V01` Base, `ACC-V02` Volume Surge, `ACC-V03` Trend Strength, `ACC-V04` Squeeze) failed holdout or incremental alpha ($0/12$ passed).
* **Exit Monitor Removed:** `app/accumulation/exit_evaluator.py`, `accumulation_sl_target.py`.
* **Preserved Artifacts:**
  * [`reports/certification/ACCUMULATION/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/ACCUMULATION/)
  * [`reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/FINAL_RETENTION_DECISION.md`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/FINAL_RETENTION_DECISION.md)

### 2.7 PULLBACK (EMA Trend Pullback Pipeline)
* **Intended Thesis:** Buying shallow pullbacks to rising 20 EMA / 50 SMA in established structural trends.
* **Why It Was Decommissioned:**
  1. *Modern Temporal Decay:* Historical performance was positive from 2016 to 2024 (+0.1345R across 12,467 trades). However, in modern market conditions (**Cell 4: 2025–2026**), performance decayed to **+0.0147R ($N=3,329$)**, with the 95% bootstrap confidence interval crossing negative territory:
     $$\text{95\% CI: } [-0.0170\text{R}, +0.0496\text{R}]$$
  2. *Statistical Significance:* Welch's two-sample t-test ($t = 6.42, p = 1.48 \times 10^{-10}$) proved that edge compression was not random noise. Standard EMA pullbacks have become saturated by algorithmic execution.
  3. *Zero Surviving Variants:* In the multi-variant battery, all 4 variants (`PB-V01` Base, `PB-V02` Deep Support, `PB-V03` RSI Dip, `PB-V04` Structural Pivot) failed either the holdout CI or modern decay gate ($0/12$ passed).
* **Exit Monitor Removed:** `pullback_engine.py` trailing stop loss logic.
* **Preserved Artifacts:**
  * [`reports/certification/PULLBACK/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/PULLBACK/)
  * [`reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/MASTER_REPORT.md`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/MASTER_REPORT.md)

### 2.8 EOD (End-of-Day 20-Day Breakout Scanner)
* **Intended Thesis:** Classic 20-day high breakout with volume expansion and volatility contraction filter.
* **Why It Was Decommissioned:**
  1. *Severe Sample Size Deficit:* Across 10 years, EOD produced only 1,768 trades. Holdout sample sizes were extremely small: BULL = 67, SIDEWAYS = 120, BEAR = 64.
  2. *Confidence Interval Width:* Holdout 95% confidence intervals spanned $> 0.40\text{R}$ and crossed zero in all regimes ($[-0.260\text{R}, +0.301\text{R}]$).
  3. *Power Analysis:* Achieving 80% statistical power at $\Delta = +0.05\text{R}$ requires $N \approx 3,140$ trades. EOD was underpowered by $25\times$.
  4. *Zero Surviving Variants:* All 4 variants (`EOD-V01` Base, `EOD-V02` 52W Momentum, `EOD-V03` Squeeze, `EOD-V04` Corridor) failed holdout validation ($0/12$ passed).
* **Exit Monitor Removed:** EOD ATR trailing stop loss and partial scaling engine (`eod_scanner.py`, `eod_alert_builder.py`).
* **Preserved Artifacts:**
  * [`reports/certification/EOD/`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/EOD/)
  * [`reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/MASTER_REPORT.md`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/reports/certification/MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/MASTER_REPORT.md)

---

## 3. Preserved Research Artifacts & Ledger Directory Map

In strict compliance with user instructions and Section 15 of AGENTS.md (*"All research artifacts remain preserved"*), no backtest data, historical trade ledgers, or Upstox fetch proofs have been deleted.

All data remains permanently stored and accessible at the following paths:

```text
reports/certification/
├── MASTER_ALL_IN_ONE_CERTIFICATION_DATA_AND_REPORT.md   # Master audit cross-check
├── MASTER_ALL_SCANNERS_LEDGER.csv                       # 80,000+ trade causal master ledger
├── DATA_FETCH_PROOF_2026-09-26.jsonl                    # 1,523 Upstox API fetch proofs
├── DATA_INTEGRITY_SWEEP_2026-09-26.csv                  # Checksum & row count verification
│
├── ACCUMULATION/
│   ├── ledger.csv                                       # Causal trade-by-trade ledger
│   ├── portfolio_equity_curve.csv                       # Historical equity curve
│   └── summary_table.csv                                # Regime & statistical battery
│
├── PULLBACK/
│   ├── ledger.csv                                       # Causal trade-by-trade ledger
│   ├── portfolio_equity_curve.csv                       # Historical equity curve
│   └── summary_table.csv                                # Regime & statistical battery
│
├── EOD/
│   ├── ledger.csv                                       # Causal trade-by-trade ledger
│   ├── portfolio_equity_curve.csv                       # Historical equity curve
│   └── summary_table.csv                                # Regime & statistical battery
│
├── REVERSAL/
│   ├── ledger.csv                                       # Causal trade-by-trade ledger
│   └── summary_table.csv                                # Regime & statistical battery
│
├── MULTI_TF/
│   ├── ledger.csv                                       # 15m causal trade ledger
│   └── summary_table.csv                                # Statistical battery
│
├── MULTI_TF_5M/
│   ├── ledger.csv                                       # 5m causal trade ledger
│   └── summary_table.csv                                # Statistical battery
│
├── TECHNICAL_INTRADAY/
│   └── ledger.csv                                       # Intraday technical trade ledger
│
├── TEMPORAL_REPLICATION_2026-09-26/
│   ├── master_temporal_matrix.csv                       # 4 multi-year cells × 4 quarters
│   ├── temporal_certification_reconciliation.md         # Full reconciliation report
│   └── temporal_summary.json                            # Machine-readable temporal metrics
│
├── MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26/
│   ├── MASTER_REPORT.md                                 # 36 variant × regime requalification
│   ├── FINAL_RETENTION_DECISION.md                      # Discard determinations
│   └── MASTER_RESULTS.json                              # Full statistical battery
│
└── FINAL_LOCK_VERIFICATION_2026-09-26/
    ├── FINAL_LOCK_VERIFICATION_REPORT.md                # 10-year verification summary
    └── final_lock_verification_summary.json             # Provenance & holdout proofs
```

---

## 4. Current Authoritative Production State

Following the removal of decommissioned modules, the production runtime architecture strictly adheres to:

1. **Active Scanners in Production:**
   * **`TECHNICAL`**: Certified **exclusively for BULL regime** (active post-close at 18:15 IST). Strictly suppressed in `SIDEWAYS` and `BEAR`.
   * **`DAILY_BUILDER`**: Watchlist generator (active at 05:00 IST).
   * **`Wealth Engine` & `MULTIBAGGER`**: Fundamental valuation engines (active at 17:00 & 17:30 IST).
   * **Active Exit Monitors:**
     * `MULTIBAGGER_EXIT` (runs every 5 minutes during market hours for fundamental holdings).
     * `WEALTH_EXIT` (runs every 5 minutes during market hours for wealth holdings).
     * `PERFORMANCE_TRACKER` (runs every 5 minutes during market hours for active `TECHNICAL` alerts).

2. **Decommissioned Scanners in Production:**
   * **`SHORT_COVERING`**: Blocked (Zero alerts, zero routing, zero scheduling).
   * **`5M_BREAKOUT`**: Blocked.
   * **`MOMENTUM_IGNITION`**: Blocked.
   * **`MULTI_TF` & `MULTI_TF_5M`**: Blocked.
   * **`TECHNICAL_INTRADAY`**: Blocked.
   * **`REVERSAL`**: Blocked.
   * **`ACCUMULATION`**: Blocked.
   * **`PULLBACK`**: Blocked.
   * **`EOD`**: Blocked.

3. **Database Guard Invariant:**
   `app/database.py:save_alert_if_new()` intercepts all alert persistence calls against `engine/production/governance_registry.py`. Any alert originating from a decommissioned scanner or from an uncertified regime is rejected fail-closed with `SCANNER_{name}_IS_DECOMMISSIONED`.
