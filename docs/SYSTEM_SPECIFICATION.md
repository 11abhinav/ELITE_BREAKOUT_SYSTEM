# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & IMPLEMENTATION CONTRACT

> **Regeneration Notice**: This document is generated directly from source code implementation. Do not edit manually. Regenerate after architectural or behavioral changes. The implementation under `app/` remains the ultimate source of truth.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Detailed Implementation Contract ("Exactly how is it implemented?") |
| **Git Commit Hash** | `252aa7633ae099f400a59691b7e3f5b090100915` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | Direct AST & Source Code Inspection (`app/`) |

---

## 1. Module Implementation Specifications

### 1.1 `app/sl_target_helper.py`
- **Purpose**: Unified Stop Loss & Target calculation engine.
- **Source File**: [`app/sl_target_helper.py`](../app/sl_target_helper.py)
- **Primary Function**: `compute_sl_and_target(entry_price, atr, candle_range, mode, engine_version="v1.0", **kwargs)`
- **Configuration Consumed**:
  - `MAX_RISK_PCT` (Default: `8.0`) — [`app/config.py`](../app/config.py)
  - `MIN_NATURAL_RR` (Default: `1.5`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **Structural Stop**: Places stop loss $0.5\times ATR$ below nearest structural support (`SwingLow`, `S1`, `EMA20`).
  - **Risk Distance Cap**:
    - *Implementation Threshold*: `8.0%`
    - *Source*: [`app/sl_target_helper.py`](../app/sl_target_helper.py)
    - *Configuration Constant*: `MAX_RISK_PCT`
    - *Code Path*:
      ```python
      min_allowed_sl = entry_price - (0.08 * entry_price)
      raw_sl = max(raw_sl, min_allowed_sl)
      ```
  - **Target Cluster Selection**: Clusters target candidates (`SwingHigh`, `R1`, `R2`, `Fib1.618`) within $1.0\times ATR$ window. If natural cluster $RR \ge 1.5$ exists, assigns cluster price to $T_1$.
  - **Fallback Path**: If no natural resistance cluster exists, synthesizes $T_1 = \text{entry\_price} + (2.5 \times \text{risk})$.
- **Unit Tests**: [`tests/test_v7_target_engine.py`](../tests/test_v7_target_engine.py), [`tests/test_component_sl_target.py`](../tests/test_component_sl_target.py)

---

### 1.2 `app/pullback_pipeline.py` & `app/swing_utils.py`
- **Purpose**: Orderly 3–15 bar retracement scanner and resumption trigger engine.
- **Source Files**: [`app/pullback_pipeline.py`](../app/pullback_pipeline.py), [`app/swing_utils.py`](../app/swing_utils.py)
- **Primary Functions**: `run_pullback_pipeline()`, `measure_pullback()`, `detect_resumption_trigger()`
- **Configuration Consumed**:
  - `MIN_IMPULSE_GAIN_PCT` (Default: `8.0`) — [`app/config.py`](../app/config.py)
  - `PULLBACK_MIN_DEPTH` (Default: `3.0`), `PULLBACK_MAX_DEPTH` (Default: `15.0`) — [`app/config.py`](../app/config.py)
  - `TRIGGER_VOL_MULT` (Default: `1.3`), `MIN_CLOSE_LOCATION` (Default: `0.60`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **Impulse Upleg**: `gain_pct = (pivot_price - min_price) / min_price * 100` $\ge 8.0\%$
  - **Pullback Retracement Depth**:
    - *Implementation Formula*:
      ```python
      depth_pct = ((impulse_end_price - min_pullback_low) / impulse_end_price) * 100
      assert 3.0 <= depth_pct <= 15.0
      ```
  - **Resumption Trigger Candle**:
    - *Implementation Formula*:
      ```python
      close_loc = (t_close - t_low) / range_ if range_ > 0 else 0
      upper_wick_ratio = upper_wick / range_ if range_ > 0 else 0
      assert close_loc >= 0.60 and volume_mult >= 1.3
      ```
- **Unit Tests**: [`tests/test_pullback_pipeline.py`](../tests/test_pullback_pipeline.py), [`tests/test_fort_knox_pullback.py`](../tests/test_fort_knox_pullback.py)

---

### 1.3 `app/daily_builder.py`
- **Purpose**: Fundamental screening, YoY/QoQ scoring, and watchlist parquet generation.
- **Source File**: [`app/daily_builder.py`](../app/daily_builder.py)
- **Primary Function**: `build_daily_watchlist()`
- **Implementation & Business Rules**:
  - `_score_nonfin(...)` — [`app/daily_builder.py`](../app/daily_builder.py):
    - `yoy_sales >= 20%`: $+20$ pts | `yoy_sales >= 10%`: $+10$ pts
    - `yoy_profit >= 25%`: $+25$ pts | `yoy_profit >= 10%`: $+12$ pts
    - `debt_equity <= 0.1`: $+10$ pts | `debt_equity <= 0.5`: $+7$ pts | `debt_equity <= 1.0`: $+3$ pts
    - `diamond_hold`: $+20$ pts
- **Unit Tests**: [`tests/test_component_daily_builder.py`](../tests/test_component_daily_builder.py), [`tests/test_v5_financial.py`](../tests/test_v5_financial.py)

---

### 1.4 `app/eod_scanner.py`
- **Purpose**: EOD price breakout & volume expansion scanner.
- **Source File**: [`app/eod_scanner.py`](../app/eod_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Implementation & Business Rules**:
  - **Price Breakout Threshold**: `Close >= 20-day High` or `Close >= 52-week High`
  - **Volume Expansion Threshold**:
    - *Implementation Threshold*: `1.5`
    - *Source File*: [`app/eod_scanner.py`](../app/eod_scanner.py)
    - *Formula*: `Volume >= 1.5 * SMA(Volume, 20)`
  - **Score Threshold**: `Score >= 65` (Bull Market), `Score >= 75` (Bear Market)
- **Unit Tests**: [`tests/test_component_scanner.py`](../tests/test_component_scanner.py)

---

### 1.5 `app/reversal_scanner.py`
- **Purpose**: Reversal and mean-reversion scanner.
- **Source File**: [`app/reversal_scanner.py`](../app/reversal_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Implementation & Business Rules**:
  - `RSI(14) < 35.0` or `Close <= BB_Lower(20, 2.0)`
  - `_is_symbol_in_reversal_cooldown(symbol, cooldown_days=5)`
- **Unit Tests**: [`tests/test_scanner_smoke.py`](../tests/test_scanner_smoke.py)

---

### 1.6 `app/wealth_engine.py`
- **Purpose**: Long-term wealth portfolio screening engine.
- **Source File**: [`app/wealth_engine.py`](../app/wealth_engine.py)
- **Primary Function**: `run_wealth_scan(is_test_mode=False)`
- **Implementation & Business Rules**:
  - `Nifty 52-week Distance > 15.0%` triggers Bear Cash Defense Posture.
  - `Piotroski F-Score >= 6`.
- **Unit Tests**: [`tests/test_wealth_engine.py`](../tests/test_wealth_engine.py)

---

### 1.7 `app/dashboard_server.py`
- **Purpose**: Flask HTTP REST API and version release server.
- **Source File**: [`app/dashboard_server.py`](../app/dashboard_server.py)
- **Discovered Endpoints**:
  - `GET /health` — Railway health check.
  - `GET /version`, `GET /api/version` — Returns JSON metadata (`git_commit`, `architecture_version`, `tests_passed`).
- **Unit Tests**: [`tests/test_api.py`](../tests/test_api.py), [`tests/test_production_deployment_gates.py`](../tests/test_production_deployment_gates.py)

---

## 2. 13 Production Deployment Verification Gates

- **Source File**: [`tests/test_production_deployment_gates.py`](../tests/test_production_deployment_gates.py)

```python
class TestProductionDeploymentGates:
    def test_gate1_cold_start_execution(self): ...
    def test_gate2_import_all_modules(self): ...
    def test_gate3_smoke_test(self): ...
    def test_gate4_ast_method_signature_audit(self): ...
    def test_gate5_runtime_railway_integration(self): ...
    def test_gate6_production_readiness_checklist(self): ...
    def test_gate7_dependency_reproducibility(self): ...
    def test_gate8_scheduled_execution_simulation(self): ...
    def test_gate9_memory_regression_budget(self): ...        # RSS < 450 MB
    def test_gate10_alert_contract_regression(self): ...      # PullbackCandidate DTO
    def test_gate11_all_scanners_execution(self): ...         # 6 Scanner Entrypoints
    def test_gate12_database_contract(self): ...               # DAO contract checks
    def test_gate13_version_endpoint(self): ...                # GET /version
```

---

## 3. Out of Scope

The following capabilities are intentionally outside the scope of this implementation contract:
- **Broker Order Execution APIs**: Real-time order placement, trailing stop order management, or broker margin calculations.
- **Multi-Asset Class Support**: Options pricing models, Futures implied volatility skew, or Commodity trading contracts.
- **GUI Desktop Client**: Native Windows/macOS desktop application wrappers.

---

## 4. Implementation Verification Summary

| Attribute | Value |
|---|---|
| **Modules Discovered During Generation** | All Python modules in `app/` and `app/core/` |
| **Test Suite Verification Basis** | Verified against test suite at commit `252aa7633ae099f400a59691b7e3f5b090100915` |
| **Verified Against Commit** | `252aa7633ae099f400a59691b7e3f5b090100915` |
