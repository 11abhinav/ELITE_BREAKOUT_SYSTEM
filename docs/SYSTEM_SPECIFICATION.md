# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & IMPLEMENTATION CONTRACT

> **Regeneration Notice**: This document is generated directly from source code implementation at commit `920de35e7eedd09231a93740b47b3f08e1548cdc`. Do not edit manually. Regenerate after architectural or behavioral changes. The source implementation under `app/` remains the ultimate source of truth.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Detailed Implementation Contract for Core Architectural Modules |
| **Scope Definition** | Implementation contract covering core modules: `main.py`, `daily_builder.py`, `eod_scanner.py`, `pullback_pipeline.py`, `reversal_scanner.py`, `multi_tf_scanner.py`, `wealth_engine.py`, `sl_target_helper.py`, `database.py`, `dashboard_server.py`, `forensics.py`, `core/models.py`. |
| **Git Commit Hash** | `920de35e7eedd09231a93740b47b3f08e1548cdc` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | AST Analysis & Source Code Inspection (`app/`) |

---

## 1. Core Module Implementation Specifications

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

## 2. Configuration Reference Appendix (`app/config.py`)

| Constant Name | Default Value | Target Subsystem | Architectural Purpose |
|---|---|---|---|
| `MAX_RISK_PCT` | `8.0` | `sl_target_helper.py` | Caps maximum allowed stop loss distance from entry |
| `MIN_NATURAL_RR` | `1.5` | `sl_target_helper.py` | Minimum natural reward-to-risk threshold for alerts |
| `PULLBACK_MIN_DEPTH` | `3.0` | `pullback_pipeline.py` | Minimum pullback retracement percentage |
| `PULLBACK_MAX_DEPTH` | `15.0` | `pullback_pipeline.py` | Maximum pullback retracement percentage |
| `MIN_IMPULSE_GAIN_PCT` | `8.0` | `swing_utils.py` | Minimum impulse upleg gain percentage |
| `TRIGGER_VOL_MULT` | `1.3` | `swing_utils.py` | Resumption trigger bar volume multiplier threshold |
| `MIN_CLOSE_LOCATION` | `0.60` | `swing_utils.py` | Minimum trigger candle close location |
| `DB_MIN_CONN` | `2` | `database.py` | Minimum PostgreSQL connection pool size |
| `DB_MAX_CONN` | `30` | `database.py` | Maximum PostgreSQL connection pool size |

---

## 3. REST API Reference Appendix (`app/dashboard_server.py`)

| Endpoint Path | HTTP Method | Auth Required | Purpose | Response Schema |
|---|---|---|---|---|
| `/health` | `GET` | No | Railway container health check | `{"status": "ok"}` |
| `/version` | `GET` | No | Build release metadata | `{"git_commit": "...", "status": "RELEASE_GATE_APPROVED"}` |
| `/api/version` | `GET` | No | Alias API release metadata | `{"git_commit": "...", "tests_passed": 271}` |
| `/api/shortlist` | `GET` | Yes | Returns current fundamental watchlist | JSON array of symbol candidate objects |
| `/api/summary` | `GET` | Yes | Returns system alert summary metrics | JSON summary stats object |

---

## 4. Database Operations & Schema Appendix (`app/database.py`)

- **Primary Schema**:
  ```sql
  CREATE TABLE IF NOT EXISTS alerts (
      id SERIAL PRIMARY KEY,
      dedup_key VARCHAR(255) UNIQUE NOT NULL,
      symbol VARCHAR(50) NOT NULL,
      scanner_name VARCHAR(50) NOT NULL,
      strategy_version VARCHAR(20) NOT NULL,
      category VARCHAR(50) NOT NULL,
      entry DECIMAL(12, 2) NOT NULL,
      stop_loss DECIMAL(12, 2) NOT NULL,
      target DECIMAL(12, 2) NOT NULL,
      reward_percent DECIMAL(8, 2),
      risk_percent DECIMAL(8, 2),
      rr_ratio DECIMAL(6, 2) NOT NULL,
      confidence DECIMAL(5, 2),
      score INT NOT NULL,
      status VARCHAR(20) DEFAULT 'ACTIVE',
      metadata JSONB,
      created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
      updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
  );
  ```
- **Indexes & Unique Constraints**: `UNIQUE (dedup_key)` enforces single active alert per setup.
- **UPSERT Logic**:
  ```sql
  INSERT INTO alerts (dedup_key, symbol, scanner_name, ...)
  VALUES (%s, %s, %s, ...)
  ON CONFLICT (dedup_key)
  DO UPDATE SET score = EXCLUDED.score, updated_at = NOW();
  ```

---

## 5. System Glossary

- **Impulse Leg**: A strong directional price move exceeding $8.0\%$ gain and $3.0\times ATR$ expansion forming the anchor for pullback detection.
- **Pullback Retracement**: An orderly $3.0\% - 15.0\%$ price decline lasting 3–20 bars following an impulse leg.
- **Resumption Trigger**: A bullish candle closing in the top $40\%$ of its high-low range ($Close\_Location \ge 0.60$) with volume expansion ($\ge 1.3\times$).
- **Natural Target**: A structural resistance target derived from swing highs, pivot resistance, or Fibonacci confluence levels.
- **Synthetic Target**: A fallback target generated when no structural resistance exists, calculated as $\text{entry} + (2.5 \times \text{risk})$.
- **Cooldown**: A safety restriction preventing identical alerts for the same symbol within a 5-day window.
- **Diamond Hold**: Fundamental equity classification scoring high YoY sales ($\ge 20\%$) and YoY profit ($\ge 25\%$) with $D/E \le 0.1$.

---

## 6. Implementation Verification Summary & Governance

| Attribute | Value |
|---|---|
| **Discovered Core Subsystems** | Core architectural modules discovered under `app/` during AST analysis |
| **Verification Basis** | Verified against test suite present at commit `920de35e7eedd09231a93740b47b3f08e1548cdc` |
| **Verified Against Commit** | `920de35e7eedd09231a93740b47b3f08e1548cdc` |
| **Limitations** | Reconstructed from implementation at commit `920de35e7eedd09231a93740b47b3f08e1548cdc`. Future code changes may invalidate portions of this specification. The source implementation under `app/` remains the ultimate source of truth. |
