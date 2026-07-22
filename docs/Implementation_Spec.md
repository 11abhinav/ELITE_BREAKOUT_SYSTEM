# ELITE BREAKOUT SYSTEM — IMPLEMENTATION SPECIFICATION (MARKDOWN)

> **Canonical Implementation Contract**  
> **Source of Truth**: Reconstructed directly from implementation (`app/`)  
> **Documentation Version**: 8.1  
> **Format**: GitHub Flavored Markdown (AI-Optimized)  

---

## 1. Module Inventory & Public Interfaces

### 1.1 `app/main.py`
- **Purpose**: Master process orchestrator, watchdog, and scheduled job runner.
- **Public Functions**:
  - `run_system_scheduler()`: Main infinite loop executing wall-clock scheduled jobs.
  - `safe_run_daily_builder()`: Invokes daily builder with memory profiling.
  - `run_evening_scanners()`: Executes EOD, Pullback, Reversal, and Multi-TF scanners sequentially.
- **Dependencies**: `app.database`, `app.forensics`, `app.daily_builder`, `app.eod_scanner`, `app.pullback_pipeline`, `app.reversal_scanner`, `app.wealth_engine`.

---

### 1.2 `app/sl_target_helper.py`
- **Purpose**: Unified SL and Target engine supporting V7 structural calculation and V2 institutional mode.
- **Public Classes**:
  - `CandidateGenerator`: Generates structural target candidates (`SwingHigh`, `R1`, `R2`, `FibRetrace`).
  - `ClusterEngine`: Groups target candidates into confluence price clusters using $1.0\times ATR$ window.
  - `BaseRiskEngine`: Institutional V2 adapter computing position size and Kelly fraction.
- **Public Functions**:
  - `compute_sl_and_target(entry_price, atr, candle_range, mode, engine_version="v1.0", **kwargs)`: Mode-dispatching SL/Target calculation entrypoint.
- **Business Rules Implemented**:
  - **Structural Stop**: Places stop loss $0.5\times ATR$ below nearest structural support (`SwingLow`, `S1`, `EMA20`).
  - **Risk Cap**: Caps stop loss at maximum 8.0% distance from entry price (`entry - 0.08 * entry`).
  - **Target Confluence**: Selects $T_1$ from winning cluster with natural $RR \ge 1.5$. On missing resistance, synthesizes fallback target $T_1 = \text{entry} + (2.5 \times \text{risk})$.

---

### 1.3 `app/daily_builder.py`
- **Purpose**: Fundamental screening, data fetcher, and watchlist parquet file generator.
- **Public Functions**:
  - `build_daily_watchlist()`: Fetches Nifty 500 fundamentals, applies V5 screening rules, writes `data/elite_fundamental_watchlist.parquet`.
  - `_score_nonfin(...)`: Scores non-financial equities up to 185 points across YoY/QoQ sales, profit, ROE, OPM, D/E, and sector tailwinds.
  - `_score_fin(...)`: Scores banking/NBFC equities using ROA, NIM, NPA, and CAR metrics.
- **Business Rules Implemented**:
  - **YoY Sales Growth Gate**: $+20$ pts for $\ge 20\%$, $+10$ pts for $\ge 10\%$.
  - **YoY Profit Growth Gate**: $+25$ pts for $\ge 25\%$, $+12$ pts for $\ge 10\%$.
  - **Debt/Equity Gate**: $+10$ pts for $D/E \le 0.1$, $+7$ pts for $D/E \le 0.5$, $+3$ pts for $D/E \le 1.0$.

---

### 1.4 `app/eod_scanner.py`
- **Purpose**: EOD Breakout Scanner identifying price breakouts with volume expansion.
- **Public Functions**:
  - `start(force: bool = False)`: Main execution entrypoint for EOD scanner.
- **Business Rules Implemented**:
  - **Price Breakout**: Current Close $\ge 20$-day high or 52-week high.
  - **Volume Expansion**: Volume $\ge 1.5\times$ 20-day SMA volume.
  - **Minimum Score Gate**: Score $\ge 65$ (Bull), Score $\ge 75$ (Bear).

---

### 1.5 `app/pullback_pipeline.py`
- **Purpose**: Pullback Pipeline Engine detecting orderly retracements into support zones.
- **Public Functions**:
  - `run_pullback_pipeline()`: Main execution entrypoint for Pullback scanner.
- **Business Rules Implemented**:
  - **Impulse Leg Requirement**: Preceding upleg must show $Gain \ge 8.0\%$ and $ATR\_Multiple \ge 3.0$.
  - **Retracement Depth**: Depth must be between $3.0\%$ and $15.0\%$ of impulse high.
  - **Duration**: Retracement duration must be between 3 and 20 trading bars.
  - **Volume Contraction**: Retracement volume median must be $\le 0.75\times$ impulse median volume.
  - **Resumption Trigger**: Bullish candle with $Close\_Location \ge 0.60$ and $Volume\_Ratio \ge 1.3\times$.

---

### 1.6 `app/reversal_scanner.py`
- **Purpose**: Reversal & Mean-Reversion Scanner for oversold bounce setups.
- **Public Functions**:
  - `start(force: bool = False)`: Main execution entrypoint for Reversal scanner.
- **Business Rules Implemented**:
  - **RSI Oversold**: RSI(14) $< 35.0$.
  - **Bollinger Band Dip**: Close $\le$ Lower Bollinger Band ($2.0\sigma$).
  - **Mean Reversion Target**: $T_1$ set to Middle Bollinger Band (20 SMA) or 50 SMA.

---

### 1.7 `app/wealth_engine.py`
- **Purpose**: Long-term wealth portfolio screening and rebalancing engine.
- **Public Functions**:
  - `run_wealth_scan(is_test_mode=False)`: Main execution entrypoint for Wealth engine.
  - `fetch_nifty_macro_state()`: Returns Nifty 6-month return and distance from 52-week high.
- **Business Rules Implemented**:
  - **Macro Bear Gate**: Activates defensive cash posture if Nifty 52-week distance $> 15.0\%$.
  - **Piotroski Score Gate**: Requires Piotroski F-Score $\ge 6$.

---

### 1.8 `app/dashboard_server.py`
- **Purpose**: Flask REST API, authentication portal, and version release server.
- **Public Routes**:
  - `GET /health`: Railway health check endpoint.
  - `GET /version`, `GET /api/version`: Release engineering metadata endpoint returning `git_commit`, `architecture_version`, `tests_passed`.
  - `GET /api/shortlist`: Returns current fundamental watchlist JSON.
  - `GET /api/summary`: Returns system performance summary.

---

## 2. Configuration Matrix (`app/config.py`)

| Constant | Default Value | Description |
|---|---|---|
| `MAX_RISK_PCT` | `8.0` | Maximum stop loss distance percentage from entry |
| `MIN_NATURAL_RR` | `1.5` | Minimum reward-to-risk ratio for valid alerts |
| `PULLBACK_MIN_DEPTH` | `3.0` | Minimum pullback retracement percentage |
| `PULLBACK_MAX_DEPTH` | `15.0` | Maximum pullback retracement percentage |
| `MIN_IMPULSE_GAIN` | `8.0` | Minimum percentage gain of preceding impulse leg |
| `TRIGGER_VOL_MULT` | `1.3` | Minimum volume multiplier on pullback trigger bar |
| `MIN_CLOSE_LOCATION` | `0.60` | Minimum close location relative to candle high-low range |

---

## 3. Database Specification (`app/database.py`)

```sql
-- Alerts Table Definition
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
