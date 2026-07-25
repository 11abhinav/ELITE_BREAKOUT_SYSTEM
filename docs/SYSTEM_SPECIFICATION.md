# ELITE BREAKOUT SYSTEM — SYSTEM SPECIFICATION & QUANTITATIVE BLUEPRINT

> **Document Class:** Technical Specification & Quantitative Blueprint
> **Status:** Canonical Master Specification for system reconstruction.
> **Target File:** `docs/SYSTEM_SPECIFICATION.md`
> **Last Synchronized:** 2026-07-25 (v8.4.2+)

---

# 1. MATHEMATICAL FORMULAS & QUANTITATIVE ALGORITHMS

## 1.1 Technical Indicator Calculations (`app/price_cache.py`, `app/indicator_manager.py`)

All technical indicators are vectorized using Pandas/NumPy over chronological OHLCV Series.

### Relative Strength Index (RSI - 14 Period)
Let $\Delta P_t = \text{Close}_t - \text{Close}_{t-1}$.
$$\text{Gain}_t = \max(\Delta P_t, 0), \quad \text{Loss}_t = \max(-\Delta P_t, 0)$$
Using Wilder's Exponential Smoothing over period $N = 14$:
$$\text{AvgGain}_t = \frac{\text{AvgGain}_{t-1} \times 13 + \text{Gain}_t}{14}, \quad \text{AvgLoss}_t = \frac{\text{AvgLoss}_{t-1} \times 13 + \text{Loss}_t}{14}$$
$$\text{RS}_t = \frac{\text{AvgGain}_t}{\text{AvgLoss}_t}, \quad \text{RSI}_t = 100 - \frac{100}{1 + \text{RS}_t}$$

### Average Directional Index (ADX - 14 Period)
Let True Range $\text{TR}_t = \max(\text{High}_t - \text{Low}_t, |\text{High}_t - \text{Close}_{t-1}|, |\text{Low}_t - \text{Close}_{t-1}|)$.
$$\text{+DM}_t = \text{High}_t - \text{High}_{t-1} \quad \text{if } \text{High}_t - \text{High}_{t-1} > \text{Low}_{t-1} - \text{Low}_t \text{ else } 0$$
$$\text{-DM}_t = \text{Low}_{t-1} - \text{Low}_t \quad \text{if } \text{Low}_{t-1} - \text{Low}_t > \text{High}_t - \text{High}_{t-1} \text{ else } 0$$
$$\text{+DI}_{14} = 100 \times \frac{\text{WilderSmooth}(\text{+DM}, 14)}{\text{WilderSmooth}(\text{TR}, 14)}, \quad \text{-DI}_{14} = 100 \times \frac{\text{WilderSmooth}(\text{-DM}, 14)}{\text{WilderSmooth}(\text{TR}, 14)}$$
$$\text{DX}_t = 100 \times \frac{|\text{+DI}_t - \text{-DI}_t|}{\text{+DI}_t + \text{-DI}_t}, \quad \text{ADX}_{14} = \text{WilderSmooth}(\text{DX}, 14)$$

### Exponential Moving Average (EMA)
$$\alpha = \frac{2}{N + 1}, \quad \text{EMA}_t = (\text{Close}_t \times \alpha) + (\text{EMA}_{t-1} \times (1 - \alpha))$$

### Average True Range (ATR - 20 Period)
$$\text{ATR}_{20} = \frac{1}{20} \sum_{i=0}^{19} \text{TR}_{t-i}$$

---

## 1.2 Fundamental Quality Scoring (`app/scoring_engine.py`, `app/wealth_engine.py`)

### Profitability Gate Classification (Rule 27)
- **Financial Sector (Banks & NBFCs):**
  $$\text{Pass}_{\text{Financial}} = (\text{ROE} \ge 15.0\%) \land (\text{Debt/Equity} \le 3.0) \land (\text{YoY Revenue Growth} \ge 10.0\%)$$
- **Non-Financial Sector:**
  $$\text{Pass}_{\text{NonFinancial}} = (\text{ROCE} \ge 15.0\%) \land (\text{Debt/Equity} \le 1.0) \land (\text{YoY Revenue Growth} \ge 10.0\%)$$

### Fundamental Quality Score (`FM_Score`)
$$\text{FM\_Score} = \text{BasePoints}(40) + \min(\text{ROE}, 30) \times 1.0 + \min(\text{YoY Revenue Growth}, 30) \times 0.5 - (\text{PromoterPledgePct} \times 2.0)$$

---

## 1.3 Centralized Candidate Scoring Engine (`app/scoring_engine.py`)

For any candidate passing technical eligibility, `calculate_score()` derives a normalized score $S \in [0, 100]$:

$$S = \text{max}\left(0, \text{min}\left(100, S_{\text{Base}} + S_{\text{Regime}} + S_{\text{Bayesian}} - P_{\text{Penalties}}\right)\right)$$

### Base Score Components ($S_{\text{Base}}$)
1. **Category Tier Base (0–30 pts)**:
   - `DEBT_FREE_CASH_GENERATOR` / `TOP_BANK`: 30 pts
   - `WEALTH_COMPOUNDER`: 25 pts
   - `BLUE_CHIP`: 20 pts
   - `MIDCAP_GROWTH`: 18 pts
   - `RECOVERY_PLAY`: 8 pts
2. **Candle Quality (0–15 pts)**:
   - Body Ratio $\frac{|\text{Close} - \text{Open}|}{\text{High} - \text{Low}} \ge 0.60$: +5 pts
   - Close Position $\frac{\text{Close} - \text{Low}}{\text{High} - \text{Low}} \ge 0.70$: +5 pts
   - Upper Wick Ratio $\frac{\text{High} - \text{Close}}{\text{High} - \text{Low}} \le 0.20$: +5 pts
3. **Volume Expansion (0–20 pts)**:
   - $\text{VolumeRatio} \ge 4.0\text{x}$: 20 pts | $\ge 3.0\text{x}$: 15 pts | $\ge 2.5\text{x}$: 12 pts | $\ge 2.0\text{x}$: 7 pts | $\ge 1.5\text{x}$: 3 pts
4. **Trend Alignment (0–15 pts)**:
   - $\text{Close} > \text{EMA}_{20}$: +3 pts | $\text{Close} > \text{SMA}_{50}$: +3 pts | $\text{SMA}_{50} > \text{SMA}_{200}$: +4 pts | $\text{ADX}_{14} \ge 30$: +5 pts
5. **RSI Location (0–10 pts)**:
   - $55 \le \text{RSI} \le 68$: 10 pts | $50 \le \text{RSI} < 55$ or $68 < \text{RSI} \le 75$: 5 pts
6. **Delivery & Institutional Bonuses (0–10 pts)**:
   - Delivery $\% \ge 50\%$: +5 pts | Institutional Block Deal Footprint: +5 pts

### Regime & Momentum Injections ($S_{\text{Regime}}$)
- **RS Rating Bonus**: $+5$ pts if 63-day Nifty Relative Strength Percentile $\ge 80th$.
- **Sector Tailwind Bonus**: $+5$ pts if Sector Status is `TAILWIND` (Top 3 sector ranking).
- **Max Momentum Bonus Cap**: $\min(15, \text{RS\_Bonus} + \text{Sector\_Bonus})$.

### Penalties ($P_{\text{Penalties}}$)
- **Extended Breakout Penalty**: $-\min(20, ((\text{Close} - \text{Prior20DHigh}) / \text{ATR}_{20} - 1.5) \times 10)$ if extension $> 1.5\text{x}$.
- **OBV Divergence Penalty**: $-5$ pts if $\text{OBV Slope} \le 0$.
- **Promoter Pledge Penalty**: $-(\text{PledgePct} \times 1.5)$ if $\text{PledgePct} > 10\%$.

---

## 1.4 Dynamic Stop-Loss & Target Engine (`app/sl_target_helper.py`)

`compute_sl_and_target()` dynamically calculates stops and targets based on scanner mode (`EOD`, `MULTI_TF`, `REVERSAL`, `INTRADAY`, `PULLBACK`).

### Anti-Trap Buffer Equations
- **EOD Mode**: $\text{Buffer} = \max(0.80 \times \text{ATR}_{20}, 0.0075 \times \text{Entry})$
- **Multi-TF Mode**: $\text{Buffer} = \max(0.50 \times \text{ATR}_{20}, 0.0050 \times \text{Entry})$
- **Reversal Mode**: $\text{Buffer} = \max(1.00 \times \text{ATR}_{20}, 0.0100 \times \text{Entry})$

### Structural Support Selection (`_pick_support`)
$$\text{Support} = \min(\text{SwingLow}_{10}, \text{S1 Pivot}, \text{VWAP}, \text{Candle Low})$$
$$\text{Raw SL} = \text{Support} - \text{Buffer}$$
$$\text{Cap Guard}: \text{StopLoss} = \max(\text{Raw SL}, \text{Entry} - (3.0 \times \text{ATR}_{20}))$$

### Target Level Multipliers
- $\text{Target}_1 = \text{Entry} + 1.5 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_2 = \text{Entry} + 2.5 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_3 = \text{Entry} + 4.0 \times (\text{Entry} - \text{StopLoss})$
- $\text{Target}_4 = \text{Entry} + 6.0 \times (\text{Entry} - \text{StopLoss})$

### TradeStructureValidator Invariants
Every trade structure MUST satisfy:
1. $\text{Entry} > 0 \land \text{StopLoss} > 0 \land \text{Target}_1 > 0$
2. $\text{StopLoss} < \text{Entry}$ (Strictly below entry)
3. $\text{Entry} \le \text{Target}_1 \le \text{Target}_2 \le \text{Target}_3$ (Strict target ordering)
4. $\text{Risk-Reward Ratio } (R:R) = \frac{\text{Target}_1 - \text{Entry}}{\text{Entry} - \text{StopLoss}} \ge \text{MinRR}$ ($\ge 2.0$ for EOD/Reversal, $\ge 1.5$ for Multi-TF).

---

# 2. SCANNER FILTER CASCADES & WORKFLOWS

## 2.1 EOD Breakout Scanner (`app/eod_scanner.py`)
```text
Universe (watchlist.parquet)
  │
  ▼
Phase A: Eligibility & Bar History Check (len(df) >= 200, Close >= 20.0)
  │
  ▼
Phase B: Technical Indicators (EMA20, SMA50, SMA200, RSI, ADX, ATR20, BB_WIDTH_PCTILE)
  │
  ▼
Phase C: Structural Breakout (Close > PRIOR_20D_HIGH & Close > SMA50 & Close > EMA20)
  │
  ▼
Phase D: Candle Quality (Body >= 60%, ClosePos >= 70%, UpperWick <= 20%, VolRatio >= 2.5x)
  │
  ▼
Phase E: Extension & Compression Gates (ATR Extension <= 1.5x, BB_WIDTH_PCTILE <= 0.80)
  │
  ▼
Phase F: Forensic Risk Check (Forensic Tier != REJECT)
  │
  ▼
Phase G: Scoring Engine (calculate_score() >= 82)
  │
  ▼
Phase H: SL & Target Calculation (compute_sl_and_target(), R:R >= 2.0)
  │
  ▼
Phase I: Full-Universe Accumulation & Global Max Alerts Truncation (Top 10 by score)
  │
  ▼
Phase J: Persistence & Un-nested Health Status Reporting (save_alert_if_new(), upsert_scanner_health())
```

## 2.2 Reversal Scanner (`app/reversal_scanner.py`)
```text
Universe (watchlist.parquet)
  │
  ▼
Phase A: Drop Band Gate (Price 15% - 45% below 52W High)
  │
  ▼
Phase B: SMA50 Reclaim Gate (Close >= SMA50, or Close within 3% holding EMA20)
  │
  ▼
Phase C: Oversold Momentum Curl (RSI <= 40, curling >= 35 + MACD Bullish Cross within 10 bars)
  │
  ▼
Phase D: Volume & Liquidity (VolumeRatio >= 1.5x, 20D Avg Vol >= 100k shares)
  │
  ▼
Phase E: Cooldown Check ((symbol, "REVERSAL") not in cooldown_alerts)
  │
  ▼
Phase F: Scoring Engine (score >= 62) -> SL Calculation -> Global Top 10 Truncation -> DB Persist
```

## 2.3 Pullback Pipeline (`app/pullback_pipeline.py`)
```text
Regime Check (MarketRegime != STRONG_BEAR)
  │
  ▼
Stage 1: Uptrend Gate (Close > SMA50 > SMA200)
  │
  ▼
Stage 2: Pivot Detection & Impulse Wave Selection (Swing high/low lookback)
  │
  ▼
Stage 3: Pullback Structure (Depth 23.6% - 61.8% of impulse, volume contraction)
  │
  ▼
Stage 4: Resumption Trigger (PREVIOUS_HIGH, PREVIOUS_OPEN, INSIDE_BAR bullish close)
  │
  ▼
Stage 5: Evidence Bonus (+3 EOD, +2 Multibagger/Multi-TF) -> Score >= 75 -> Risk Engine -> Persist
```

## 2.4 Multi-Timeframe Scanner (`app/multi_tf_scanner.py`)
```text
Single-Pass Bulk Pre-Fetch (Fetch entire 295-symbol universe in 1 call)
  │
  ▼
Phase A (1H Trend): 3-month history period (~437 bars), EMA9 > EMA20 > EMA50, Close > EMA200, ADX >= 20
  │
  ▼
Phase B (30m Alignment) & Phase C (15m Alignment)
  │
  ▼
Phase D (5m Trigger): Decoupled thrust / pullback rejection, VWAP fallback to EMA20/Close
  │
  ▼
ProviderResult Safety -> Candidate Collection -> R:R Key Evaluation -> Persist Top 10
```

---

# 3. DATABASE SCHEMAS (POSTGRESQL DDLs)

Below are the 6 primary operational tables in PostgreSQL (`app/database.py`).

```sql
-- 1. Alerts Table (Primary Signal Record)
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    breakout_type TEXT NOT NULL,
    alert_time TEXT NOT NULL,
    scanner TEXT NOT NULL DEFAULT 'EOD',
    category TEXT,
    entry_price REAL,
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    target_3 REAL,
    score INTEGER,
    signals TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    initial_stop_loss REAL,
    target_price REAL,
    context JSONB,
    model_version TEXT,
    bayesian_regime TEXT,
    bayesian_weights JSONB,
    structural_failure_stop REAL,
    target_quality_score REAL,
    base_score INTEGER,
    rs_bonus INTEGER,
    sector_bonus INTEGER,
    rs_percentile REAL,
    sector_name TEXT,
    regime_score REAL,
    is_rejected BOOLEAN DEFAULT FALSE,
    exit_reason TEXT,
    alert_date DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT alerts_dedup_idx UNIQUE (symbol, breakout_type, scanner, alert_date),
    CONSTRAINT chk_alerts_status CHECK (status IN ('OPEN', 'WIN', 'LOSS', 'TRAILING', 'EXPIRED', 'PARTIAL_WIN_1', 'PARTIAL_WIN_2', 'NEUTRAL'))
);

-- 2. Scanner Health Table
CREATE TABLE IF NOT EXISTS scanner_health (
    scanner_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'IDLE',
    last_success TEXT,
    today_alerts INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    is_acknowledged BOOLEAN DEFAULT TRUE,
    updated_at TEXT NOT NULL,
    processed_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0.0,
    outcome TEXT DEFAULT 'SUCCESS',
    provider_stats JSONB
);

-- 3. Symbol Mappings Table (BSE Fallback Cache)
CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT PRIMARY KEY,
    bse_symbol TEXT NOT NULL,
    mapping_state TEXT NOT NULL DEFAULT 'ACTIVE',
    failure_count INTEGER DEFAULT 0,
    retry_after TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Funnel Telemetry Table
CREATE TABLE IF NOT EXISTS funnel_telemetry (
    id SERIAL PRIMARY KEY,
    scanner TEXT NOT NULL,
    run_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stage TEXT NOT NULL,
    gate TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    observed_value REAL,
    threshold_value REAL,
    comparator TEXT,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Parquet Cache Table (Database Persistence Backup)
CREATE TABLE IF NOT EXISTS parquet_cache (
    name TEXT NOT NULL,
    date DATE NOT NULL,
    data BYTEA NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (name, date)
);

-- 6. Breakout Watchlist Table (Multi-TF State Tracking)
CREATE TABLE IF NOT EXISTS breakout_watchlist (
    symbol TEXT PRIMARY KEY,
    category TEXT,
    current_state TEXT,
    h1_status TEXT,
    m30_status TEXT,
    m15_status TEXT,
    m5_status TEXT,
    breakout_level REAL,
    support_level REAL,
    invalidated_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    session_date TEXT,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);
```

---

# 4. REST API SPECIFICATIONS (`app/dashboard_server.py`)

| Endpoint | Method | Auth Level | Description | Response JSON Schema |
| :--- | :--- | :--- | :--- | :--- |
| `/api/scanner_status` | `GET` | Public | Real-time health status of all 6 scanners. | `{"status": "ok", "scanners": [{"scanner_name": "EOD", "status": "OK", "today_alerts": 3, "duration_seconds": 12.5}]}` |
| `/api/trigger-scanner` | `POST` | Admin | Manual async trigger for a scanner. | `{"status": "success", "message": "Scanner EOD triggered"}` |
| `/api/lock-stats` | `GET` | Admin | Mutex lock contention telemetry. | `{"acquisitions": 142, "max_wait_sec": 0.12, "contention_events": 0}` |
| `/api/wealth_data` | `GET` | Public | Wealth Engine portfolio & exit monitoring. | `{"status": "ok", "data": [{"Stock": "RELIANCE", "CMP": 2450.0, "HoldScore": 88}]}` |
| `/version` | `GET` | Public | Build metadata & deployment approval. | `{"architecture_version": "8.1", "git_commit": "7d802ec0", "status": "RELEASE_GATE_APPROVED"}` |

---
*End of Master Technical Specification & Quantitative Blueprint — `docs/SYSTEM_SPECIFICATION.md`*
