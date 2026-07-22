# ELITE BREAKOUT SYSTEM — MACHINE-VERIFIABLE IMPLEMENTATION SPECIFICATION

| Metadata Field | Value |
|---|---|
| **Git Commit Hash** | `e54f3ad3fa86698707928b497c0ddbed81a78274` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | Direct AST & Source Code Inspection (`app/`) |

---

## 1. Scanner Implementation Specifications

### 1.1 Pullback Pipeline (`app/pullback_pipeline.py`)

- **Purpose**: Detects 3-15 bar orderly retracements following a strong impulse upleg.
- **Source File**: [`app/pullback_pipeline.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/pullback_pipeline.py)
- **Primary Function**: `run_pullback_pipeline()`
- **Discovered Business Rules & Exact Formulas**:
  1. **Impulse Leg Rule**: Preceding gain $\ge 8.0\%$, $ATR\_Multiple \ge 3.0$
     - *Formula*: `gain_pct = (pivot_price - min_price) / min_price * 100` — [`app/swing_utils.py:L134`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/swing_utils.py#L134)
  2. **Retracement Depth Rule**:
     - *Formula*: `depth_pct = ((impulse_end_price - min_pullback_low) / impulse_end_price) * 100` — [`app/swing_utils.py:L242`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/swing_utils.py#L242)
     - *Configuration*: `MIN_DEPTH_PCT = 3.0`, `MAX_DEPTH_PCT = 15.0`
  3. **Resumption Trigger Bar**:
     - *Formula*: `close_loc = (t_close - t_low) / (t_high - t_low) if (t_high - t_low) > 0 else 0` — [`app/swing_utils.py:L352`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/swing_utils.py#L352)
     - *Gate*: `close_loc >= 0.60` and `volume_mult >= 1.3`
- **Unit Tests**: `tests/test_pullback_pipeline.py`, `tests/test_fort_knox_pullback.py`
- **Verification**: ✅ High (Commit: `e54f3ad3fa86698707928b497c0ddbed81a78274`)

---

### 1.2 EOD Breakout Scanner (`app/eod_scanner.py`)

- **Purpose**: Scans EOD price breakouts with volume expansion.
- **Source File**: [`app/eod_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/eod_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Discovered Business Rules & Exact Formulas**:
  1. **Price Breakout**: `Close >= 20-day High` or `Close >= 52-week High`
  2. **Volume Expansion**: `Volume >= 1.5 * SMA(Volume, 20)`
  3. **Score Threshold**: `Score >= 65` (Bull Market), `Score >= 75` (Bear Market)
- **Unit Tests**: `tests/test_component_scanner.py`
- **Verification**: ✅ High (Commit: `e54f3ad3fa86698707928b497c0ddbed81a78274`)

---

### 1.3 Reversal & Mean-Reversion Scanner (`app/reversal_scanner.py`)

- **Purpose**: Scans mean-reversion setups on oversold dip conditions.
- **Source File**: [`app/reversal_scanner.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/reversal_scanner.py)
- **Primary Function**: `start(force: bool = False)`
- **Discovered Business Rules & Exact Formulas**:
  1. **RSI Oversold**: `RSI(14) < 35.0`
  2. **Bollinger Dip**: `Close <= BB_Lower(20, 2.0)`
  3. **Cooldown Gate**: `_is_symbol_in_reversal_cooldown(symbol, cooldown_days=5)`
- **Unit Tests**: `tests/test_scanner_smoke.py`
- **Verification**: ✅ High (Commit: `e54f3ad3fa86698707928b497c0ddbed81a78274`)

---

### 1.4 Daily Watchlist & Fundamental Builder (`app/daily_builder.py`)

- **Purpose**: Fetches Nifty 500 fundamentals and builds fundamental parquet watchlist.
- **Source File**: [`app/daily_builder.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/daily_builder.py)
- **Primary Function**: `build_daily_watchlist()`
- **Discovered Business Rules & Scoring Formulas**:
  - `_score_nonfin(...)` — [`app/daily_builder.py:L687`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/daily_builder.py#L687):
    - `yoy_sales >= 20%`: $+20$ pts | `yoy_sales >= 10%`: $+10$ pts
    - `yoy_profit >= 25%`: $+25$ pts | `yoy_profit >= 10%`: $+12$ pts
    - `debt_equity <= 0.1`: $+10$ pts | `debt_equity <= 0.5`: $+7$ pts | `debt_equity <= 1.0`: $+3$ pts
    - `diamond_hold`: $+20$ pts
- **Unit Tests**: `tests/test_component_daily_builder.py`, `tests/test_v5_financial.py`
- **Verification**: ✅ High (Commit: `e54f3ad3fa86698707928b497c0ddbed81a78274`)

---

## 2. Configuration Inventory (`app/config.py`)

- **Source File**: [`app/config.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/config.py)
- **Discovered Constants**:

| Constant | Source Line | Current Value | Used By | Purpose |
|---|---|---|---|---|
| `MAX_RISK_PCT` | L45 | `8.0` | `sl_target_helper.py` | Caps maximum stop loss distance % |
| `MIN_NATURAL_RR` | L48 | `1.5` | `sl_target_helper.py` | Minimum natural reward-to-risk threshold |
| `PULLBACK_MIN_DEPTH` | L112 | `3.0` | `pullback_pipeline.py` | Minimum pullback depth percentage |
| `PULLBACK_MAX_DEPTH` | L113 | `15.0` | `pullback_pipeline.py` | Maximum pullback depth percentage |
| `MIN_IMPULSE_GAIN_PCT` | L120 | `8.0` | `swing_utils.py` | Minimum impulse upleg gain % |
| `TRIGGER_VOL_MULT` | L166 | `1.3` | `swing_utils.py` | Trigger bar volume multiplier threshold |
| `MIN_CLOSE_LOCATION` | L166 | `0.60` | `swing_utils.py` | Minimum trigger candle close location |

---

## 3. Database DAO & Schema Contract (`app/database.py`)

- **Source File**: [`app/database.py`](file:///Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py)
- **Functions Discovered**: `init_db()`, `save_alert_if_new()`, `upsert_scanner_health()`, `get_connection()`
- **Schema Trace**:
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
- **Verification**: ✅ High (Commit: `e54f3ad3fa86698707928b497c0ddbed81a78274`)
