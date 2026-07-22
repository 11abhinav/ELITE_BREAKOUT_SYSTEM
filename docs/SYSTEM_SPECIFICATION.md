# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & IMPLEMENTATION CONTRACT

> **Regeneration Notice**: This document is generated directly from source code implementation at commit `214f19ae`. Do not edit manually. Regenerate after architectural or behavioral changes. The source implementation under `app/` remains the ultimate source of truth.
>
> **Scope Notice**: This specification covers the core architectural modules that define the system's behavior. Supporting utility modules, helper libraries, and generated artifacts are intentionally omitted except where they materially affect system behavior.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Detailed Implementation Contract for Core Architectural Modules |
| **Git Commit Hash** | `214f19aedbb592aad2a561edbdccba542519d4b8` |
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
  - `MAX_RISK_PCT` (Default: `8.0`) — [`app/config.py`](../app/config.py)
  - `MIN_NATURAL_RR` (Default: `1.5`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **V7 Structural Stop Validation**: `_compute_structural_stop(...)` ranks support anchors (`SwingLow`, `S1`, `S2`, `EMA20`). If no valid anchor exists or if structural stop violates minimum noise buffer, returns `is_valid: False` and explicitly REJECTS the setup (`NO_VALID_STRUCTURAL_STOP`).
  - **V2 Institutional Sizing**: For valid structural stops, `calculate_position_size` dynamically calculates position size:
    ```python
    raw_position_size = round(max_risk_pct / (risk_pct / 100.0), 2)
    position_size_pct = min(100.0, raw_position_size)
    ```
    This caps total equity allocation at `100.0%` max per trade to prevent un-capped leverage on tight stop loss setups.
  - **Target Cluster Selection**: Clusters target candidates (`SwingHigh`, `R1`, `R2`, `Fib1.618`) within $1.0\times ATR$ window. If a natural cluster exists with $RR \ge 1.5$, assigns cluster price to $T_1$. If a natural cluster exists with $RR < 1.5$, explicitly REJECTS the candidate (`REJ_LOW_RR`).
  - **Fallback Path**: If NO natural resistance cluster exists, synthesizes $T_1 = \text{entry\_price} + (2.5 \times \text{risk})$.
- **Unit Tests**: [`tests/test_v7_target_engine.py`](../tests/test_v7_target_engine.py), [`tests/test_component_sl_target.py`](../tests/test_component_sl_target.py)

---

### 1.2 `app/pullback_pipeline.py` & `app/swing_utils.py`
- **Purpose**: Orderly 3–20 bar retracement scanner and resumption trigger engine.
- **Source Files**: [`app/pullback_pipeline.py`](../app/pullback_pipeline.py), [`app/swing_utils.py`](../app/swing_utils.py)
- **Primary Functions**: `run_pullback_pipeline()`, `measure_pullback()`, `detect_resumption_trigger()`
- **Configuration Consumed**:
  - `MIN_IMPULSE_GAIN_PCT` (Default: `8.0`) — [`app/config.py`](../app/config.py)
  - `PULLBACK_MIN_DEPTH` (Default: `3.0`), `PULLBACK_MAX_DEPTH` (Default: `15.0`) — [`app/config.py`](../app/config.py)
  - `TRIGGER_VOL_MULT` (Default: `1.3`), `MIN_CLOSE_LOCATION` (Default: `0.60`), `MAX_UPPER_WICK` (Default: `0.25`) — [`app/config.py`](../app/config.py)
- **Implementation & Business Rules**:
  - **Impulse Upleg**: `gain_pct = (pivot_price - min_price) / min_price * 100` $\ge 8.0\%$
  - **Pullback Retracement Depth**:
    - *Implementation Formula*:
      ```python
      depth_pct = ((impulse_end_price - min_pullback_low) / impulse_end_price) * 100
      assert 3.0 <= depth_pct <= 15.0
      ```
    - *Structure Preservation Floor*: `min_pullback_low >= impulse_start_price` (rejects full breakdown setups).
  - **Resumption Trigger Candle**:
    - *Implementation Formula*:
      ```python
      close_loc = (t_close - t_low) / range_ if range_ > 0 else (1.0 if t_close >= t_open else 0.0)
      upper_wick_ratio = upper_wick / range_ if range_ > 0 else 0.0
      assert close_loc >= 0.60 and volume_mult >= 1.3 and upper_wick_ratio <= 0.25
      ```
- **Unit Tests**: [`tests/test_pullback_pipeline.py`](../tests/test_pullback_pipeline.py), [`tests/test_fort_knox_pullback.py`](../tests/test_fort_knox_pullback.py)

---

### 1.3 `app/daily_builder.py`
- **Purpose**: Fundamental screening, YoY/QoQ scoring, and watchlist parquet generation.
- **Source File**: [`app/daily_builder.py`](../app/daily_builder.py)
- **Primary Function**: `build_daily_watchlist()`
- **Implementation & Business Rules**:
  - `_score_nonfin(...)` — 180+ point fundamental scoring model:
    - **YoY Growth**: `YoY Sales >= 20%` (+20 pts) / `>= 10%` (+10 pts) | `YoY Profit >= 25%` (+25 pts) / `>= 10%` (+12 pts)
    - **QoQ Growth**: `QoQ Sales >= 10%` (+8 pts) / `>= 5%` (+4 pts) | `QoQ Profit >= 10%` (+12 pts) / `>= 5%` (+6 pts)
    - **Return & Margins**: `ROE >= 25%` (+15 pts) / `>= 20%` (+10 pts) / `>= 15%` (+5 pts) | `OPM >= 20%` (+10 pts) / `>= 15%` (+7 pts) | YoY Margin (+5 pts), QoQ Margin (+3 pts)
    - **Balance Sheet & Solvency**: `Debt/Equity <= 0.1` (+10 pts) / `<= 0.5` (+7 pts) / `<= 1.0` (+3 pts)
    - **Sector & Qualitative**: Sector Tailwinds (+12 / +6 pts), Mature Quality (+10 pts), Elite Compounder (+5 pts), Turnaround (+3 pts)
    - **Long-Term & FCF**: Diamond Hold (+20 pts), 5Y Rev Growth $\ge 15\%$ (+5 pts), 5Y EPS Growth $\ge 15\%$ (+5 pts), FCF Margin $\ge 15\%$ (+15 pts) / $\ge 8\%$ (+10 pts) / $\ge 3\%$ (+5 pts)
- **Unit Tests**: [`tests/test_component_daily_builder.py`](../tests/test_component_daily_builder.py), [`tests/test_v5_financial.py`](../tests/test_v5_financial.py)

---

### 1.4 `app/eod_scanner.py`
- **Purpose**: EOD price breakout & volume expansion scanner.
- **Source File**: [`app/eod_scanner.py`](../app/eod_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Implementation & Business Rules**:
  - **Price Breakout Threshold**: `Close >= 20-day High` or `Close >= 52-week High`
  - **Volume Expansion Threshold**: `Volume >= 1.8 * SMA(Volume, 20)`
  - **Score Threshold & Regime Modifiers**:
    - Base Score Threshold: `82` (1d)
    - `STRONG_BULL` / `BULL`: `82` (modifier = 0)
    - `SIDEWAYS`: `90` (modifier = +8)
    - `BEAR`: `87` (modifier = +5)
    - `STRONG_BEAR`: `92` (modifier = +10)
- **Unit Tests**: [`tests/test_component_scanner.py`](../tests/test_component_scanner.py)

---

### 1.5 `app/reversal_scanner.py`
- **Purpose**: Reversal and mean-reversion scanner.
- **Source File**: [`app/reversal_scanner.py`](../app/reversal_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Implementation & Business Rules**:
  - `RSI(14) < 45.0` or `Close <= BB_Lower(20, 2.0)`
  - `_is_symbol_in_reversal_cooldown(symbol, cooldown_days=30)` (Outcome-aware cooldown on failed reversals)
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

### 2.1 Risk Management & Target Configuration
| Constant Name | Default Value | Target Subsystem | Architectural Purpose |
|---|---|---|---|
| `MAX_RISK_PCT` | `8.0` | `sl_target_helper.py` | Caps maximum allowed stop loss distance from entry |
| `MIN_NATURAL_RR` | `1.5` | `sl_target_helper.py` | Minimum natural reward-to-risk threshold for alerts |

### 2.2 Scanner & Trigger Thresholds
| Constant Name | Default Value | Target Subsystem | Architectural Purpose |
|---|---|---|---|
| `PULLBACK_MIN_DEPTH` | `3.0` | `pullback_pipeline.py` | Minimum pullback retracement percentage |
| `PULLBACK_MAX_DEPTH` | `15.0` | `pullback_pipeline.py` | Maximum pullback retracement percentage |
| `MIN_IMPULSE_GAIN_PCT` | `8.0` | `swing_utils.py` | Minimum impulse upleg gain percentage |
| `TRIGGER_VOL_MULT` | `1.3` | `swing_utils.py` | Resumption trigger bar volume multiplier threshold |
| `MIN_CLOSE_LOCATION` | `0.60` | `swing_utils.py` | Minimum trigger candle close location |
| `MAX_UPPER_WICK` | `0.25` | `swing_utils.py` | Maximum upper wick ratio threshold |

### 2.3 Database & Connection Pooling
| Constant Name | Default Value | Target Subsystem | Architectural Purpose |
|---|---|---|---|
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

## 4. Traceability Matrix

This matrix establishes direct traceability from key system capabilities to source implementation and unit verification suites:

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

---

## 5. System Glossary

- **Impulse Leg**: A strong directional price move exceeding $8.0\%$ gain and $3.0\times ATR$ expansion forming the anchor for pullback detection.
- **Pullback Retracement**: An orderly $3.0\% - 15.0\%$ price decline lasting 3–20 bars following an impulse leg.
- **Resumption Trigger**: A bullish candle closing in the top $40\%$ of its high-low range ($Close\_Location \ge 0.60$) with volume expansion ($\ge 1.3\times$) and upper wick ratio $\le 0.25$. Circuit-locked candles (`High == Low == Close`) evaluate $Close\_Location = 1.0$.
- **Natural Target**: A structural resistance target derived from swing highs, pivot resistance, or Fibonacci confluence levels. If a natural cluster exists with $RR < 1.5$, the setup is rejected (`REJ_LOW_RR`).
- **Synthetic Target**: A fallback target generated ONLY when no structural resistance cluster exists, calculated as $\text{entry} + (2.5 \times \text{risk})$.
- **Per-Scanner Cooldown**: A safety restriction preventing identical alerts for the same symbol within a scanner-specific window (EOD: 4 days, Reversal: 4 days, Pullback: 1 day), scoped by `(symbol, scanner_name)` composite key.
- **Diamond Hold**: Fundamental equity classification scoring high YoY sales ($\ge 20\%$) and YoY profit ($\ge 25\%$) with $D/E \le 0.1$.

---

## 6. Implementation Verification Summary & Governance

| Attribute | Value |
|---|---|
| **Discovered Core Subsystems** | Core architectural modules discovered under `app/` during AST analysis |
| **Verification Basis** | Verified against test suite present at commit `214f19aedbb592aad2a561edbdccba542519d4b8` |
| **Verified Against Commit** | `214f19aedbb592aad2a561edbdccba542519d4b8` |
| **Limitations** | Reconstructed from implementation at commit `214f19ae`. Future code changes may invalidate portions of this specification. The source implementation under `app/` remains the ultimate source of truth. |
