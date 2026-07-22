# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & IMPLEMENTATION CONTRACT

> **Canonical System Contract**: This document defines the exact operational contract, parameter rationales (RULE 10), and technical specifications for the core architectural modules of the **Elite Breakout System** as of commit `cc36f704`.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Detailed Implementation Contract for Core Architectural Modules |
| **Git Commit Hash** | `cc36f704` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | 299 Passing Pytest System Tests (`app/`) |



---

## 1. Core Module Implementation Specifications

### 1.1 `app/sl_target_helper.py`
- **Purpose**: Unified Stop Loss & Target calculation engine (V7 Structural + V2 Institutional).
- **Source File**: [`app/sl_target_helper.py`](../app/sl_target_helper.py)
- **Primary Function**: `compute_sl_and_target(entry_price, atr, candle_range, mode, engine_version="v1.0", **kwargs)`
- **Configuration Consumed**:
  - `MAX_SL_DISTANCE_PCT` (Default: `8.0`) — Hard stop-loss distance cap — [`app/config.py`](../app/config.py)
  - `ACCOUNT_RISK_BUDGET_PCT` (Default: `1.0`) — Max portfolio equity risk per position — [`app/config.py`](../app/config.py)
  - `MIN_NATURAL_RR` (Default: `1.5`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **V7 Structural Stop Validation**: `_compute_structural_stop(...)` ranks support anchors (`SwingLow`, `S1`, `S2`, `EMA20`). If no valid anchor exists or if structural stop violates minimum noise buffer, returns `is_valid: False` and explicitly REJECTS the setup (`NO_VALID_STRUCTURAL_STOP`).
  - **V2 Institutional Position Sizing**:
    ```python
    raw_position_size = round(account_risk_pct / (risk_pct / 100.0), 2)
    position_size_pct = min(100.0, raw_position_size)
    ```
    This separates account risk budget (`ACCOUNT_RISK_BUDGET_PCT = 1.0%`) from stop distance cap (`MAX_SL_DISTANCE_PCT = 8.0%`) and caps total equity allocation at `100.0%` max per trade.
  - **Target Cluster Selection**: Clusters target candidates (`SwingHigh`, `R1`, `R2`, `Fib1.618`) within $1.0\times ATR$ window. If a natural cluster exists with $RR \ge 1.5$, assigns cluster price to $T_1$. If a natural cluster exists with $RR < 1.5$, explicitly REJECTS the candidate (`REJ_LOW_RR`).

---

### 1.2 `app/pullback_pipeline.py` & `app/swing_utils.py`
- **Purpose**: Orderly 3–20 bar retracement scanner and resumption trigger engine.
- **Source Files**: [`app/pullback_pipeline.py`](../app/pullback_pipeline.py), [`app/swing_utils.py`](../app/swing_utils.py)
- **Primary Functions**: `run_pullback_pipeline()`, `measure_pullback()`, `detect_resumption_trigger()`
- **Implementation & Business Rules**:
  - **Impulse Upleg**: `gain_pct = (pivot_price - min_price) / min_price * 100` $\ge 8.0\%$ within $\le 20$ bars (`MAX_IMPULSE_BARS`).
  - **Pullback Retracement Depth**: `depth_pct = ((impulse_end_price - min_pullback_low) / impulse_end_price) * 100` ($3.0\% \le \text{depth\_pct} \le 15.0\%$).
  - **Resumption Trigger Candle**: `MIN_CLOSE_LOCATION = 0.75` and `MAX_UPPER_WICK = 0.25` confirm strong demand into session close. Zero-range candles evaluate `close_loc = 1.0` ONLY if `t_close > prev_close` (Upper Circuit lock).

---

### 1.3 `app/daily_builder.py` (Feature F-05 Banking Scorer)
- **Purpose**: Fundamental screening, YoY/QoQ scoring, and pure watchlist generation.
- **Source File**: [`app/daily_builder.py`](../app/daily_builder.py)
- **Primary Functions**: `build_daily_watchlist()`, `_score_fin()`, `_score_nonfin()`
- **Implementation & Business Rules**:
  - **Banking Ratios (`_score_fin`)**: Evaluates Net NPA level & trend, Banded NIM (`3.0% <= NIM <= 6.0%` +15 pts; `NIM > 7.0%` MFI caution = 0 pts), CAR (`>= 15.0%` +15 pts), and CASA ratio (`>= 40.0%` +10 pts).
  - **Pure Fundamental Guarantee**: `FM_Score` contains **100% purely fundamental metrics** (momentum bonuses sit exclusively in technical scanners).

---

### 1.4 `app/macro_utils.py` (Features F-03 & F-07)
- **Purpose**: Macro regime detection, 63-day Relative Strength vs Nifty 50, and 14 NSE Sector Regime Engine.
- **Source File**: [`app/macro_utils.py`](../app/macro_utils.py)
- **Primary Functions**: `compute_nifty_rs_rating()`, `compute_sector_regime_rankings()`
- **Implementation & Business Rules**:
  - **Feature F-03 (Relative Strength)**: Computes 63-day stock return vs Nifty 50 return over active scan universe (~500–700 equities).
  - **Feature F-07 (Sector Regime)**: Computes blended 63d (70%) + 21d (30%) return across 14 NSE sector indices and enforces a **3-Session Hysteresis Rule** (counter defaults to 0; must hold Top 3 for 3 consecutive days for `TAILWIND`).

---

### 1.5 `app/confluence_engine.py` (Feature F-04)
- **Purpose**: Cross-scanner 3-signal golden confluence evaluation.
- **Source File**: [`app/confluence_engine.py`](../app/confluence_engine.py)
- **Primary Function**: `evaluate_confluence_shortlist()`
- **Implementation & Business Rules**:
  - **Execution Timing**: Runs at `04:30 PM IST` after Daily Builder (`01:00 AM`) and Technical Scanners (`03:45–04:15 PM`).
  - **Confluence Rule**: Requires $\text{FM\_Score} \ge 70.0 \quad \text{AND} \quad \text{Any Active Technical Signal} \quad \text{AND} \quad \text{rs\_percentile} \ge 80.0$ (`PHASE3_CONFLUENCE_AND_TELEMETRY_v1.0`).
  - ~~Legacy FM_Score >= 75.0~~ *(Recalibrated on 2026-07-22 following fundamental purification to prevent golden alert starvation)*.
  - Promotes candidate to `ELITE_CONFLUENCE_ALERT` (Score 95+).

### 1.6 `app/outcome_tracker.py` & `app/near_miss_tracker.py` (Features F-01 & Near-Miss Tracker)
- **Purpose**: Post-market outcome, daily running excursion, corporate action adjustments, and near-miss opportunity-cost tracking worker.
- **Source Files**: [`app/outcome_tracker.py`](../app/outcome_tracker.py), [`app/near_miss_tracker.py`](../app/near_miss_tracker.py)
- **Primary Function**: `run_outcome_tracker()`, `log_near_miss()`
- **Implementation & Business Rules**:
  - **Daily Running MFE/MAE**: Accumulates running excursion daily while trade is OPEN.
  - **Same-Bar SL Collision**: Records `AMBIGUOUS_SL_HIT` (-1.0R loss) for P&L, but excludes `AMBIGUOUS_SL_HIT` from triggering 30-day reversal loss cooldowns (`PHASE3_CONFLUENCE_AND_TELEMETRY_v1.0`).
  - **Score-Upgrade Dedup Override**: Allows re-alerting within cooldown if `new_score >= old_score + 5`.
  - **Near-Miss Opportunity-Cost Tracker**: Logs candidates rejected within 10% of gate thresholds to PostgreSQL table `near_misses` for empirical expectancy tracking.

---

### 1.8 `app/outcome_tracker.py` (Advanced Outcome Analytics & Attribution F-13)
- **Purpose**: Computes dual confidence levels, feature attribution breakdowns, score band expectancies, execution capture efficiency, and rolling performance validation.
- **Source File**: [`app/outcome_tracker.py`](../app/outcome_tracker.py)
- **Primary Function**: `compute_advanced_outcome_analytics()`
- **API Route**: `GET /api/v1/analytics/outcomes/advanced` — [`app/dashboard_server.py`](../app/dashboard_server.py)
- **Implementation & Business Rules**:
  - **Overall & Per-Metric Dual Confidence**: `LOW` ($<100$ total / $<20$ metric), `MEDIUM` ($100\text{--}300$ total / $20\text{--}50$ metric), `HIGH` ($>300$ total / $>50$ metric).
  - **Capture Efficiency**: Computes $\frac{\text{Avg Realized } R}{\text{Avg MFE } R} \times 100\%$.
  - **Feature Attribution**: Compares $RS \ge 80$ vs $<80$, `sector_bonus > 0` vs $0$, and `BULL` vs `OTHER` regime.
  - **Configurable Score Bands**: Reads `SCORE_BANDS` from [`app/config.py`](../app/config.py).

### 1.9 Feature F-06 & F-14: Earnings Calendar Warning Integration & Quality Trajectory Engine

#### Architecture Overview
1. **Earnings Calendar Subsystem (`app/earnings_calendar.py`)**:
   - `EarningsProvider` abstract base class with `YahooEarningsProvider` concrete implementation.
   - Caches upcoming earnings dates in PostgreSQL table `earnings_calendar(symbol, earnings_date, date_status, updated_at)`.
   - Daily 08:00 AM IST scheduled refresh (`refresh_earnings_calendar()`) decouples network fetch calls from scan execution.
   - Non-blocking DB lookup (`get_earnings_info()`) classifies graded earnings risk severity:
     - `HIGH_TODAY` (🔴 Results Today)
     - `HIGH_SOON` (🟠 Results in 1-2 days)
     - `MEDIUM_WEEK` (🟡 Results in 3-5 days)
     - ~~`NONE` (🟢 No Warning)~~ *(Deprecated 2026-07-22: Missing/unverified dates returned false-safety green NONE)*
     - `UNVERIFIED` (⚠️ Missing/Unverified earnings date data in database — `PHASE2_EARNINGS_UNVERIFIED_v1.0`)
   - Injects gap-risk warning banner into Telegram and WebPush payloads.
2. **Quality Trajectory Scorer (`app/quality_trajectory.py`)**:
   - Computes multi-quarter level and trend slope across 6 fundamental pillars (0-20 pts): ROCE, ROE, OPM, Debt/Equity reduction, Interest Coverage expansion, and graduated CFO/PAT ratio (`PHASE2_TRAJECTORY_RECALIB_v1.0`).
   - ~~Legacy pure slope scoring penalized flat 35% ROCE elite companies~~ $\rightarrow$ `pillar_score = max(level_score, trend_score)` for ROCE, ROE, and OPM, giving flat 35% ROCE full Grade A credit. Uses 4-quarter TTM rolling averages to eliminate quarterly seasonality noise.
   - Maps total score to `Trajectory_Grade`: **A** (18-20), **B** (15-17), **C** (10-14), **D** (<10).
   - Stores detailed component breakdown JSON (`trajectory_details`).
3. **F-13 Granular Attribution**:
   - Extends `compute_advanced_outcome_analytics()` to track trade counts, win rate %, average $R$, expectancy, and capture efficiency across 6 granular risk buckets: `results_today`, `one_day_before`, `two_days_before`, `three_to_five_before`, `one_day_after`, and `normal_trades`.
   - Filter Capture Efficiency calculation to trades with $MFE \ge 0.5R$ (`PHASE2_ANALYTICS_ROBUSTNESS_v1.0`) and pin overall confidence levels to explicit half-open ranges: `LOW [0,100)`, `MEDIUM [100,300]`, `HIGH (300,∞)`.

### 1.10 Feature F-15: Forensic Risk Engine & Dynamic Growth Investment Mode

#### Architecture Overview
1. **Forensic Risk Evaluator (`app/forensic_engine.py`)**:
   - ~~Legacy Binary FCF < 0 Hard Filter~~ *(Replaced on 2026-07-22 by 3Y Cumulative CFO / PAT < 0.6 Primary Hard Gate)*.
   - Primary hard gate: `3-Year Cumulative CFO / PAT < 0.6` $\rightarrow$ `REJECT`.
   - 0–100 Weighted Growth Investment Score: `Capex / Sales` (40%), `Revenue CAGR 3Y` (30%), `ROCE` (30%).
   - ~~Legacy Score >= 60 alone activated Growth Mode~~ *(Deprecated 2026-07-22 due to circularity where capex reduced capex penalty)*.
   - Growth Mode Preconditions: Requires `Revenue CAGR 3Y >= 12%` AND `ROCE >= 15%` as mandatory hard preconditions before `Growth_Investment_Mode = TRUE` (`PHASE2_GROWTH_MODE_FIX_v1.0`).
   - Scaled FCF Penalty: Capped/reduced in Growth Mode ($-3$, $-5$ pts) vs Normal Mode ($-10$, $-20$ pts).
   - Supports explicit `UNKNOWN` risk tier for missing fundamental data.

### 1.11 Data Provider & Memory Infrastructure Upgrades
1. **Fyers Symbol Degradation Cache (`app/data_provider.py`)**:
   - Maintains 24-hour module-level degradation cache (`_fyers_degradation_cache`) for symbols failing quality validation on Fyers (`FYERS_DEGRADATION_CACHE_v1.0`), bypassing redundant Fyers API calls.
2. **Memory Profiler Recalibration (`app/memory_profiler.py` & `app/price_cache.py`)**:
   - ~~Legacy 300-400 MB thresholds triggered false alarm breach logs during normal operations~~ $\rightarrow$ Recalibrated `@profile_function(budget_mb=500.0)` and `TARGET_THRESHOLDS = [500, 600, 700, 800, 900]` to match post-boot steady-state process RSS (`MEMORY_RECALIBRATION_v1.0`).
3. **ScanFailure Dataclass & Database Synchronization (`app/core_models.py`)**:
   - Aligned `ScanFailure` dataclass fields (`symbol, scanner_name, provider, failure_reason, failed_at, scan_id, stage`) with PostgreSQL `scan_failures` table schema (`SCAN_FAILURE_SCHEMA_FIX_v1.0`).

2. **Scanner Policy Deciders**:
   - `ForensicEngine` operates as a pure evaluator returning `{score, tier, flags, details}`. Scanners check `Forensic_Risk_Tier != 'REJECT'` policy gate.
3. **F-13 Performance Attribution**:
   - Extends `compute_advanced_outcome_analytics()` to track trade counts, win rate %, average $R$, expectancy, and capture efficiency across forensic risk tiers (`low_risk`, `medium_risk`, `high_risk`, `unknown_risk`) and Growth Mode (`growth_mode_active` vs `growth_mode_inactive`).

---

## 2. Configuration Reference Appendix & Parameter Rationales (RULE 10)

Per **RULE 10 (Documented Parameter Rationale)**, every configuration parameter in [`app/config.py`](../app/config.py) is documented with its technical purpose, baseline origin, evaluated alternatives, and behavioral impact:

### 2.1 Risk Management & Position Sizing Configuration
| Constant Name | Value | Purpose | Baseline Origin | Evaluated Alternatives | Behavioral Impact |
|---|---|---|---|---|---|
| `MAX_SL_DISTANCE_PCT` | `8.0%` | Hard stop-loss distance cap | NSE swing volatility limit | `5.0%` (too tight), `12.0%` (excessive drawdown) | Rejects setups where risk distance exceeds structural norms |
| `ACCOUNT_RISK_BUDGET_PCT` | `1.0%` | Max equity risk budget per trade | Institutional Kelly fraction | `0.5%` (under-allocated), `2.0%` (excessive variance) | Determines dynamic position sizing equity allocation ($\le 100\%$) |
| `MIN_NATURAL_RR` | `1.5` | Min reward-to-risk gate | Confluence target engine baseline | `1.2` (sub-optimal), `2.0` (filters valid setups) | Rejects trades where structural resistance blocks $T_1$ before $1.5R$ |

### 2.2 Momentum Bonus Configuration (F-03 & F-07)
| Constant Name | Value | Purpose | Baseline Origin | Evaluated Alternatives | Behavioral Impact |
|---|---|---|---|---|---|
| `RS_BONUS` | `10` | RS leadership bonus points | Top 20% RS rating vs Nifty | `15` (clips sector bonus), `5` (insufficient weight) | Awards bonus points to stocks outperforming Nifty 50 |
| `SECTOR_BONUS` | `8` | Sector tailwind bonus points | Top 3 sector regime status | `12` (clips to cap), `5` (underweighted) | Awards bonus points to stocks in strong sector tailwinds |
| `MAX_MOMENTUM_BONUS` | `15` | Combined momentum bonus cap | Quant audit optimization | `10` (dead code risk), `25` (distorts base score) | Ensures both RS and Sector bonuses can co-exist without clipping Sector to zero |

---

## 3. Traceability Matrix

| Business Capability | Core Module | Primary Test Suite |
|---|---|---|
| **Daily Watchlist Generation** | [`app/daily_builder.py`](../app/daily_builder.py) | [`tests/test_component_daily_builder.py`](../tests/test_component_daily_builder.py) |
| **Breakout Detection** | [`app/eod_scanner.py`](../app/eod_scanner.py) | [`tests/test_component_scanner.py`](../tests/test_component_scanner.py) |
| **Pullback Retracement Detection** | [`app/pullback_pipeline.py`](../app/pullback_pipeline.py) | [`tests/test_pullback_pipeline.py`](../tests/test_pullback_pipeline.py) |
| **Outcome & Excursion Tracking** | [`app/outcome_tracker.py`](../app/outcome_tracker.py) | [`tests/test_f01_to_f07_quant_engine.py`](../tests/test_f01_to_f07_quant_engine.py) |
| **Advanced Outcome Analytics & Attribution (F-13)** | [`app/outcome_tracker.py`](../app/outcome_tracker.py) | [`tests/test_f13_advanced_analytics.py`](../tests/test_f13_advanced_analytics.py) |
| **Earnings Calendar Integration (F-06)** | [`app/earnings_calendar.py`](../app/earnings_calendar.py) | [`tests/test_earnings_calendar.py`](../tests/test_earnings_calendar.py) |
| **Quality Trajectory Scorer (F-14)** | [`app/quality_trajectory.py`](../app/quality_trajectory.py) | [`tests/test_quality_trajectory.py`](../tests/test_quality_trajectory.py) |
| **Forensic Risk Engine (F-15)** | [`app/forensic_engine.py`](../app/forensic_engine.py) | [`tests/test_forensic_engine.py`](../tests/test_forensic_engine.py) |
| **Cross-Scanner Confluence Engine** | [`app/confluence_engine.py`](../app/confluence_engine.py) | [`tests/test_f01_to_f07_quant_engine.py`](../tests/test_f01_to_f07_quant_engine.py) |
| **SL / Target Confluence Engine** | [`app/sl_target_helper.py`](../app/sl_target_helper.py) | [`tests/test_v7_target_engine.py`](../tests/test_v7_target_engine.py) |
| **Dashboard REST API & Analytics** | [`app/dashboard_server.py`](../app/dashboard_server.py) | [`tests/test_api.py`](../tests/test_api.py) |
| **Deployment Release Gates** | [`app/main.py`](../app/main.py) / [`app/dashboard_server.py`](../app/dashboard_server.py) | [`tests/test_production_deployment_gates.py`](../tests/test_production_deployment_gates.py) |



