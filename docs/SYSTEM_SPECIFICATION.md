# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & IMPLEMENTATION CONTRACT

> **Regeneration Notice**: This document is generated directly from source code implementation at commit `60eff85e`. Do not edit manually. Regenerate after architectural or behavioral changes. The source implementation under `app/` remains the ultimate source of truth.
>
> **Scope Notice**: This specification covers the core architectural modules that define the system's behavior. Supporting utility modules, helper libraries, and generated artifacts are intentionally omitted except where they materially affect system behavior.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Detailed Implementation Contract for Core Architectural Modules |
| **Git Commit Hash** | `60eff85e` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | AST Analysis & Source Code Inspection (`app/`) |

---

## 1. Core Module Implementation Specifications

### 1.1 `app/sl_target_helper.py`
- **Purpose**: Unified Stop Loss & Target calculation engine (V7 Structural + V2 Institutional).
- **Source File**: [`app/sl_target_helper.py`](../app/sl_target_helper.py)
- **Primary Function**: `compute_sl_and_target(entry_price, atr, candle_range, mode, engine_version="v1.0", **kwargs)`
- **Configuration Consumed**:
  - `MAX_SL_DISTANCE_PCT` (Default: `8.0`) — Maximum stop loss distance allowed — [`app/config.py`](../app/config.py)
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
  - **Fallback Path**: If NO natural resistance cluster exists, synthesizes $T_1 = \text{entry\_price} + (2.5 \times \text{risk})$.

---

### 1.2 `app/pullback_pipeline.py` & `app/swing_utils.py`
- **Purpose**: Orderly 3–20 bar retracement scanner and resumption trigger engine.
- **Source Files**: [`app/pullback_pipeline.py`](../app/pullback_pipeline.py), [`app/swing_utils.py`](../app/swing_utils.py)
- **Primary Functions**: `run_pullback_pipeline()`, `measure_pullback()`, `detect_resumption_trigger()`
- **Configuration Consumed**:
  - `MIN_IMPULSE_GAIN_PCT` (Default: `8.0`), `MAX_IMPULSE_BARS` (Default: `20`) — [`app/config.py`](../app/config.py)
  - `PULLBACK_MIN_DEPTH` (Default: `3.0`), `PULLBACK_MAX_DEPTH` (Default: `15.0`) — [`app/config.py`](../app/config.py)
  - `TRIGGER_VOL_MULT` (Default: `1.3`), `MIN_CLOSE_LOCATION` (Default: `0.75`), `MAX_UPPER_WICK` (Default: `0.25`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **Impulse Upleg**: `gain_pct = (pivot_price - min_price) / min_price * 100` $\ge 8.0\%$ within $\le 20$ bars (`MAX_IMPULSE_BARS`).
  - **Pullback Retracement Depth**: `depth_pct = ((impulse_end_price - min_pullback_low) / impulse_end_price) * 100` ($3.0\% \le \text{depth\_pct} \le 15.0\%$).
  - **Resumption Trigger Candle**:
    ```python
    close_loc = (t_close - t_low) / range_ if range_ > 0 else (1.0 if t_close > prev_close else 0.0)
    upper_wick_ratio = upper_wick / range_ if range_ > 0 else 0.0
    assert close_loc >= 0.75 and volume_mult >= 1.3 and upper_wick_ratio <= 0.25
    ```
    `MIN_CLOSE_LOCATION = 0.75` and `MAX_UPPER_WICK = 0.25` are 100% aligned to demand that bullish trigger candles close in the top $25\%$ of their high-low range. Zero-range candles (`High == Low == Close`) evaluate `close_loc = 1.0` ONLY if `t_close > prev_close` (Upper Circuit lock above prior close), protecting against lower circuit crashes.

---

### 1.3 `app/daily_builder.py`
- **Purpose**: Fundamental screening, YoY/QoQ scoring, and watchlist parquet generation.
- **Source File**: [`app/daily_builder.py`](../app/daily_builder.py)
- **Primary Function**: `build_daily_watchlist()`
- **Implementation & Business Rules**:
  - `_score_nonfin(...)` — 180+ point fundamental scoring model.
  - `compute_safe_growth_rate(current, prior)` — Centralized growth helper handling zero denominators, negative-to-positive turnarounds, and missing values safely.

---

## 2. Configuration Reference Appendix & Parameter Rationales (RULE 10)

Per **RULE 10 (Documented Parameter Rationale)**, every configuration parameter in [`app/config.py`](../app/config.py) is documented with its technical purpose, baseline origin, evaluated alternatives, and behavioral impact:

### 2.1 Risk Management & Position Sizing Configuration
| Constant Name | Value | Purpose | Baseline Origin | Evaluated Alternatives | Behavioral Impact |
|---|---|---|---|---|---|
| `MAX_SL_DISTANCE_PCT` | `8.0%` | Hard stop-loss distance cap | NSE swing volatility limit | `5.0%` (too tight for pullbacks), `12.0%` (excessive drawdown) | Rejects wide, loose setups where risk distance exceeds structural norms |
| `ACCOUNT_RISK_BUDGET_PCT` | `1.0%` | Max equity risk budget per trade | Institutional Kelly fraction (1.0% portfolio risk) | `0.5%` (under-allocated), `2.0%` (excessive portfolio variance) | Determines dynamic position sizing equity allocation ($\le 100\%$) |
| `MIN_NATURAL_RR` | `1.5` | Min reward-to-risk gate | Confluence target engine baseline | `1.2` (sub-optimal expectancy), `2.0` (filters valid setups) | Rejects trades where natural structural resistance blocks $T_1$ before $1.5R$ |

### 2.2 Scanner & Trigger Thresholds
| Constant Name | Value | Purpose | Baseline Origin | Evaluated Alternatives | Behavioral Impact |
|---|---|---|---|---|---|
| `MIN_IMPULSE_GAIN_PCT` | `8.0%` | Min upleg gain for pullback anchor | Momentum impulse threshold | `5.0%` (choppy noise), `12.0%` (misses early trends) | Establishes strong institutional buyer footprint |
| `MAX_IMPULSE_BARS` | `20` | Max duration for impulse leg | 1 trading month lookback | `10` (too strict for momentum), `40` (includes slow grinds) | Prevents slow 60-day grinds from qualifying as explosive impulses |
| `PULLBACK_MIN_DEPTH` | `3.0%` | Min retracement depth | Minor noise filter | `1.0%` (intraday noise), `5.0%` (misses shallow bases) | Filters out 1-bar pause candles |
| `PULLBACK_MAX_DEPTH` | `15.0%` | Max retracement depth | Consolidation breakdown threshold | `10.0%` (too tight), `20.0%` (structural failure) | Rejects setups experiencing deep structural breakdown |
| `TRIGGER_VOL_MULT` | `1.3x` | Resumption volume expansion | Median pullback volume multiplier | `1.1x` (weak volume), `1.8x` (unreachable on pullbacks) | Ensures institutional volume resumption on trigger candle |
| `MIN_CLOSE_LOCATION` | `0.75` | Min trigger close location | Top 25% candle range threshold | `0.60` (redundant with upper wick), `0.85` (too restrictive) | Confirms strong buying pressure into session close |
| `MAX_UPPER_WICK` | `0.25` | Max upper wick ratio | Supply rejection threshold | `0.15` (over-filters), `0.40` (permits heavy overhead supply) | Filters out candles experiencing heavy intra-day profit taking |

---

## 3. Traceability Matrix

| Business Capability | Core Module | Primary Test Suite |
|---|---|---|
| **Daily Watchlist Generation** | [`app/daily_builder.py`](../app/daily_builder.py) | [`tests/test_component_daily_builder.py`](../tests/test_component_daily_builder.py) |
| **Breakout Detection** | [`app/eod_scanner.py`](../app/eod_scanner.py) | [`tests/test_component_scanner.py`](../tests/test_component_scanner.py) |
| **Pullback Retracement Detection** | [`app/pullback_pipeline.py`](../app/pullback_pipeline.py) | [`tests/test_pullback_pipeline.py`](../tests/test_pullback_pipeline.py) |
| **Mean-Reversion Detection** | [`app/reversal_scanner.py`](../app/reversal_scanner.py) | [`tests/test_scanner_smoke.py`](../tests/test_scanner_smoke.py) |
| **SL / Target Confluence Engine** | [`app/sl_target_helper.py`](../app/sl_target_helper.py) | [`tests/test_v7_target_engine.py`](../tests/test_v7_target_engine.py) |
| **Long-Term Wealth Screening** | [`app/wealth_engine.py`](../app/wealth_engine.py) | [`tests/test_wealth_engine.py`](../tests/test_wealth_engine.py) |
| **Dashboard REST API** | [`app/dashboard_server.py`](../app/dashboard_server.py) | [`tests/test_api.py`](../tests/test_api.py) |
| **Deployment Release Gates** | [`app/main.py`](../app/main.py) / [`app/dashboard_server.py`](../app/dashboard_server.py) | [`tests/test_production_deployment_gates.py`](../tests/test_production_deployment_gates.py) |
