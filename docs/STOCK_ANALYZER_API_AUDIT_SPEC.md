# STOCK ANALYZER & WATCHLIST SYSTEM — API AUDIT SPECIFICATION

> **Document Class:** Technical API & System Architecture Specification for External Audit  
> **Status:** Canonical Production API Reference  
> **Target File:** `docs/STOCK_ANALYZER_API_AUDIT_SPEC.md`  
> **Last Synchronized:** 2026-07-26 (v8.5.0 Production Release)  

---

## 1. EXECUTIVE SYSTEM OVERVIEW

The **Stock Analyzer & Personal Watchlist System** is an enterprise-grade diagnostic engine embedded within the **Elite Breakout System**. It allows users and system administrators to run real-time, dry-run evaluations of any listed NSE or BSE equity ticker across all 7 quantitative scanner pipelines without triggering unauthorized orders or alerts.

### Core System Objectives
1. **7-Stage Diagnostic Funnel**: Evaluates stock candidates against all 7 specialized scanner engines (**Daily Builder**, **EOD Breakout**, **Multi-TF Intraday**, **Reversal Oversold**, **Pullback Pipeline**, **Wealth Engine**, and **Multibagger Engine**).
2. **0ms Client-Side Autocomplete**: Pre-loads the master universe of 2,389+ equities into browser memory for sub-millisecond client-side ticker filtering.
3. **Strict 5-Stage Ticker Validation Cascade**: Blocks invalid, delisted, or spoofed ticker inputs before hitting database queries or external market data APIs.
4. **Isolated Personal Watchlists**: Multi-tenant database model enforcing 100% data privacy per user via PostgreSQL `user_id::text` casting.
5. **Inline Main Screen UI**: Renders diagnostic breakdowns directly on the dashboard main screen (`#stock-diagnostic-main-container`) without modal window overflow.

---

## 2. API ENDPOINT AUDIT SPECIFICATIONS

Below is the complete specification for all 9 REST API endpoints powering the Stock Analyzer and Watchlist engine.

---

### 2.1 Analyze Stock (`GET /api/v1/analyze_stock`)

Runs full 7-stage dry-run quantitative evaluation for a given ticker symbol.

- **HTTP Method**: `GET`
- **Authentication**: Required (`@login_required` session cookie / CSRF header)
- **Query Parameters**:
  - `symbol` (string, required): Ticker symbol (e.g. `TATAMOTORS`, `RELIANCE`, `THANGAMAYL`, `500400.BO`).
  - `is_deep_analysis` (boolean, optional, default: `false`): Enables deep execution checks and manual alert promotion eligibility.
  - `force_refresh` (boolean, optional, default: `false`): Bypasses pre-scanned `stock_analysis_master` repository cache to force fresh calculation.

#### Request Example
```http
GET /api/v1/analyze_stock?symbol=THANGAMAYL&is_deep_analysis=true HTTP/1.1
Host: system.elitebreakout.in
Cookie: session=.eJ...
```

#### Processing & Validation Flow
1. **Sanitization**: Converts `symbol` to uppercase, removes `.NS`/`.BO` suffixes for lookup.
2. **Master Cache Check**: If `force_refresh=false`, queries PostgreSQL table `stock_analysis_master` for a pre-scanned report generated within the last 24 hours.
3. **Price & Fundamental Fetching**: Loads OHLCV historical price data (minimum 50 bars required) and fundamental metrics (`ROCE`, `ROE`, `Debt/Equity`, `YoY Revenue`, `YoY Profit`, `Piotroski F-Score`, `Promoter Pledge`).
4. **7-Stage Funnel Evaluation**: Evaluates Stage 1 (Daily Builder) through Stage 7 (Multibagger Engine) in sequence.
5. **Watchlist State Check**: Queries `user_watchlists` for current session `user_id` to compute `is_in_watchlist`. Note: Searching does NOT automatically alter user watchlist records.
6. **Deficit Aggregation**: Collects up to 4 primary quantitative deficit explanations for parameter gaps holding the stock back.

#### Success Response (200 OK)
```json
{
  "symbol": "THANGAMAYL",
  "company_name": "Thangamayil Jewellery Limited",
  "sector": "CONSUMER DURABLES",
  "success": true,
  "is_in_watchlist": false,
  "is_deep_analysis": true,
  "watchlist_status": "QUALIFIED (WEALTH, MULTIBAGGER)",
  "close_price": 6906.50,
  "volume_ratio": 0.77,
  "rsi": 63.6,
  "overall_health_score": 88.0,
  "technical_score": 70.0,
  "fundamental_score": 100.0,
  "rs_percentile": 85.4,
  "deficits": [
    "🔊 Volume Surge Deficit: Current Volume Ratio is 0.77x (lacks +1.03x for 1.8x EOD threshold).",
    "🕯️ Upper Wick Deficit: Upper Wick is 39.7% of candle range (needs ≤35% for clean breakout close)."
  ],
  "funnel": {
    "daily_builder": {
      "status": "CORE MET",
      "reasons": ["Price ₹6906.50 ≥ ₹100.0 | Avg Turnover ₹183.9Cr ≥ ₹1.0Cr | Bars 251 ≥ 50"]
    },
    "eod_breakout": {
      "status": "NO",
      "reasons": ["Close ₹6906.50 ≤ Prior 20D High ₹7145.01", "Volume Ratio 0.77x < 1.8x threshold", "Upper Wick 39.7% > 35% max"]
    },
    "multi_tf": {
      "status": "NO",
      "reasons": ["Intraday 15-minute volume explosion spike required during market hours (09:30–14:45 IST)"]
    },
    "reversal": {
      "status": "NO",
      "reasons": ["Drop from 52W High 3.3% outside 15%–45% correction band", "Daily RSI 63.6 (requires RSI ≤38 or RSI curl ≥50 from oversold min 49.8)"]
    },
    "pullback": {
      "status": "NO",
      "reasons": ["Invalid pullback structure (Retracement 72.5%, Vol Ratio 0.48x)"]
    },
    "wealth_engine": {
      "status": "CORE MET",
      "reasons": ["Wealth Engine Qualified (Growth Multiplier) | Close ₹6906.50 > 200DMA ₹3885.22"]
    },
    "multibagger": {
      "status": "CORE MET (Prime)",
      "reasons": ["🚀 Prime Compounder: Piotroski 9/9 | Pledge 0.0% ≤ 10% | Strong Trend"]
    }
  }
}
```

#### Error Response (400 Bad Request)
```json
{
  "success": false,
  "error": "Symbol parameter is required."
}
```

---

### 2.2 Autocomplete Suggestions (`GET /api/v1/symbols/suggest`)

Returns real-time ticker suggestion matches for user search input.

- **HTTP Method**: `GET`
- **Authentication**: Required (`@login_required`)
- **Query Parameters**:
  - `q` (string, required): Search prefix or company name substring.

#### Request Example
```http
GET /api/v1/symbols/suggest?q=TATA HTTP/1.1
```

#### Success Response (200 OK)
```json
[
  {
    "symbol": "TATAMOTORS",
    "company_name": "Tata Motors Limited",
    "sector": "AUTOMOBILE",
    "exchange": "NSE"
  },
  {
    "symbol": "TATASTEEL",
    "company_name": "Tata Steel Limited",
    "sector": "METALS & MINING",
    "exchange": "NSE"
  }
]
```

---

### 2.3 Master Symbol List (`GET /api/v1/symbols/master_list`)

Returns all 2,389+ listed NSE & BSE equities for instant browser RAM caching.

- **HTTP Method**: `GET`
- **Authentication**: Required (`@login_required`)

#### Response Summary
JSON array of symbol objects pre-loaded into window memory (`window.MASTER_SYMBOLS_CLIENT_ARRAY`) on page load. Enables `<0.1ms` client-side instant search responses without network latency.

---

### 2.4 Get User Watchlist (`GET /api/v1/user_watchlist`)

Fetches all stocks saved in the logged-in user's personal monitored watchlist.

- **HTTP Method**: `GET`
- **Authentication**: Required (`@login_required`)
- **Session Identity**: Uses `session['user_id']` cast to string.

#### Success Response (200 OK)
```json
[
  {
    "symbol": "THANGAMAYL",
    "company_name": "Thangamayil Jewellery Limited",
    "added_at": "2026-07-26T14:20:00+05:30",
    "last_scanned_at": "2026-07-26T15:10:00+05:30",
    "last_health_score": 88.0,
    "last_status": "QUALIFIED (WEALTH, MULTIBAGGER)",
    "notes": "Monitored fundamental compounder",
    "last_deep_analysis_at": "2026-07-26T15:10:00+05:30",
    "deep_analysis_result": { ... }
  }
]
```

---

### 2.5 Add to Personal Watchlist (`POST /api/v1/user_watchlist/add`)

Saves a validated stock ticker to the user's personal monitored watchlist.

- **HTTP Method**: `POST`
- **Authentication**: Required (`@login_required`)
- **Request Body**:
```json
{
  "symbol": "NAVINFLUOR",
  "company_name": "Navin Fluorine International Limited",
  "health_score": 79.0,
  "status": "CANDIDATE",
  "notes": "Added from diagnostic search"
}
```

#### Validation & Guard Rules
1. Calls `validate_nse_bse_ticker(symbol)` through the 5-stage verification cascade.
2. If invalid/unrecognized, rejects with HTTP 400 (`❌ 'SYMBOL' is not a recognized active NSE/BSE stock ticker`).
3. Executes PostgreSQL `INSERT ... ON CONFLICT (user_id, symbol) DO UPDATE`.

#### Success Response (200 OK)
```json
{
  "success": true
}
```

---

### 2.6 Remove from Watchlist (`DELETE` / `POST /api/v1/user_watchlist/remove`)

Removes a stock ticker from the user's personal watchlist.

- **HTTP Method**: `DELETE` or `POST`
- **Authentication**: Required (`@login_required`)
- **Request Body**:
```json
{
  "symbol": "NAVINFLUOR"
}
```

#### Success Response (200 OK)
```json
{
  "success": true
}
```

---

### 2.7 Watchlist Deep Analysis Batch (`POST /api/v1/user_watchlist/deep_analysis`)

Executes full 7-stage deep diagnostic analysis on all stocks saved in the user's watchlist concurrently.

- **HTTP Method**: `POST`
- **Authentication**: Required (`@login_required`)

#### Success Response (200 OK)
```json
{
  "success": true,
  "count": 5,
  "message": "Successfully executed deep diagnostic analysis on 5 watchlist stock(s).",
  "items": [
    {
      "symbol": "THANGAMAYL",
      "company_name": "Thangamayil Jewellery Limited",
      "health_score": 88.0,
      "watchlist_status": "QUALIFIED (WEALTH, MULTIBAGGER)",
      "deficits": [...],
      "success": true
    }
  ]
}
```

---

### 2.8 Create Manual Alert (`POST /api/v1/create_manual_alert`)

Promotes a qualified diagnostic setup (`CORE MET` or `QUALIFIED`) to an active BUY alert in the database and dispatches notifications.

- **HTTP Method**: `POST`
- **Authentication**: Required (`@login_required`)
- **Request Body**:
```json
{
  "symbol": "THANGAMAYL",
  "scanner": "MULTIBAGGER"
}
```

#### Execution Steps
1. Re-evaluates `analyze_symbol(symbol, is_deep_analysis=True)`.
2. Computes ATR-based structural Stop Loss and Target levels (T1, T2, T3, T4).
3. Inserts record into database table `alerts` with `category = 'MULTIBAGGER (MANUAL)'`.
4. Triggers Telegram channel broadcast and Web Push notification dispatch.

#### Success Response (200 OK)
```json
{
  "success": true,
  "message": "Manual MULTIBAGGER alert created for #THANGAMAYL! Alert ID: 492",
  "alert_id": 492
}
```

---

### 2.9 Admin Refresh Master Symbols (`POST /api/v1/admin/master_symbols/refresh`)

Admin trigger to rebuild and resynchronize the database `master_symbols` registry from active exchanges and parquet files.

- **HTTP Method**: `POST`
- **Authentication**: Required (`@login_required` with admin role)

#### Success Response (200 OK)
```json
{
  "success": true,
  "message": "Successfully updated Master Symbol Registry with 2512 active NSE/BSE equities!",
  "count": 2512
}
```

---

## 3. STRICT 5-STAGE TICKER VALIDATION CASCADE

To prevent database pollution and API rate-limit drops, `validate_nse_bse_ticker(symbol)` runs a 5-stage verification cascade:

```
[User Ticker Input]
       │
       ▼
 ┌───────────┐  YES
 │  Stage 1  ├──────► [VALID TICKER] (Return True)
 └─────┬─────┘
       │ NO
       ▼
 ┌───────────┐  YES
 │  Stage 2  ├──────► [VALID TICKER] (Return True)
 └─────┬─────┘
       │ NO
       ▼
 ┌───────────┐  YES
 │  Stage 3  ├──────► [VALID TICKER] (Return True)
 └─────┬─────┘
       │ NO
       ▼
 ┌───────────┐  YES
 │  Stage 4  ├──────► [VALID TICKER] (Return True)
 └─────┬─────┘
       │ NO
       ▼
 ┌───────────┐  YES
 │  Stage 5  ├──────► [VALID TICKER] (Return True)
 └─────┬─────┘
       │ NO
       ▼
 [REJECT TICKER] (HTTP 400 Bad Request)
```

1. **Stage 1 (Master Symbol Dictionary)**: Checked against `_load_master_symbol_dictionary()` (RAM cache covering 2,389+ equities).
2. **Stage 2 (BSE Mapping Engine)**: Checked against `bse_mapping_utils.load_bse_mappings()` for security codes.
3. **Stage 3 (Database Mappings Table)**: Queries PostgreSQL `symbol_mappings` table.
4. **Stage 4 (Yahoo Search API Fallback)**: Queries `https://query2.finance.yahoo.com/v1/finance/search` for Indian exchange quotes (`.NS` / `.BO`).
5. **Stage 5 (Provider Price Verification)**: Attempts lightweight OHLCV fetch via `data_provider.get_fetcher().get_ohlcv()`.

If all 5 stages fail, the system returns HTTP 400 Bad Request with explicit corrective advice.

---

## 4. 7-STAGE SCANNER FUNNEL MATHEMATICAL FORMULAS

### Stage 1: Daily Builder (Universe Entry)
$$\text{Status} = \begin{cases} \text{CORE MET} & \text{if } P \ge 100.0 \land \bar{T}_{20D} \ge 1.0\text{ Cr} \land N_{\text{bars}} \ge 50 \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 2: EOD Breakout Scanner
$$\text{Status} = \begin{cases} \text{CORE MET} & \text{if } P > \max_{20D}(H) \land \frac{V}{\text{Med}_{20D}(V)} \ge 1.8 \land \frac{H - \max(C,O)}{H - L} \le 0.35 \land C > O \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 3: Multi-TF Intraday Scanner
$$\text{Status} = \begin{cases} \text{QUALIFIED} & \text{if } \text{Time} \in [09:30, 14:45] \land V_{15m} \ge 3.0 \times \bar{V}_{15m} \land \text{Trend}_{1H} \text{ Active} \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 4: Reversal Oversold Bounce
$$\text{Status} = \begin{cases} \text{CORE MET} & \text{if } 15\% \le \frac{H_{52W} - C}{H_{52W}} \le 45\% \land (\text{RSI}_{14} \le 38 \lor (\text{RSI}_{14} \ge 50 \land \min_{15D}(\text{RSI}) \le 38)) \land C > \text{EMA}_{20} \\ \text{WATCHLIST} & \text{if } 15\% \le \frac{H_{52W} - C}{H_{52W}} \le 45\% \land \text{RSI}_{14} \le 45 \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 5: Pullback Continuation Pipeline
$$\text{Status} = \begin{cases} \text{CORE MET} & \text{if } C > \text{SMA}_{50} > \text{SMA}_{200} \land 20\% \le \text{Depth}_{\text{Fib}} \le 60\% \land \text{Trigger}_{\text{Resumption}} = \text{True} \\ \text{WATCHLIST} & \text{if } C > \text{SMA}_{50} > \text{SMA}_{200} \land 20\% \le \text{Depth}_{\text{Fib}} \le 60\% \land \text{Trigger}_{\text{Resumption}} = \text{False} \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 6: Wealth Engine (4-Bucket Parity)
$$\text{Bucket}_{\text{Core}} = (\text{ROCE} \ge 20\% \land \text{ROE} \ge 15\% \land \text{D/E} \le 0.50)$$
$$\text{Bucket}_{\text{Growth}} = ((\text{YoY}_{\text{Rev}} \ge 20\% \lor \text{YoY}_{\text{Rev}}=0) \land (\text{YoY}_{\text{Prof}} \ge 20\% \lor \text{YoY}_{\text{Prof}}=0) \land \text{ROCE} \ge 15\%)$$
$$\text{Bucket}_{\text{Quality-Sale}} = (\text{ROCE} \ge 15\% \land \text{D/E} \le 1.0 \land \text{Drop}_{52W} \ge 15\%)$$
$$\text{Bucket}_{\text{Opportunistic}} = (\text{YoY}_{\text{Prof}} \ge 40\%)$$
$$\text{Status} = \begin{cases} \text{CORE MET} & \text{if } (\text{Any Bucket Met}) \land C > \text{SMA}_{200} \\ \text{WATCHLIST} & \text{if } (\text{Any Bucket Met}) \land C \le \text{SMA}_{200} \text{ or } (\text{ROCE} \ge 12\% \land \text{D/E} \le 1.0) \\ \text{NO} & \text{otherwise} \end{cases}$$

### Stage 7: Multibagger Engine (2-Tier Conviction Parity)
$$\text{Status} = \begin{cases} \text{CORE MET (Prime)} & \text{if } \text{Piotroski} \ge 7 \land \text{Pledge} \le 10\% \land C > \text{SMA}_{50} > \text{SMA}_{200} \\ \text{CORE MET (High Quality)} & \text{if } \text{Health Score} \ge 65.0 \land \text{Pledge} \le 15\% \land C > \text{SMA}_{50} > \text{SMA}_{200} \\ \text{WATCHLIST} & \text{if } \text{Health Score} \ge 50.0 \lor \text{Piotroski} \ge 5 \\ \text{NO} & \text{otherwise} \end{cases}$$

---

## 5. DATABASE DDL SCHEMAS & ISOLATION MODEL

### 5.1 Personal Watchlist Table (`user_watchlists`)
```sql
CREATE TABLE IF NOT EXISTS user_watchlists (
    user_id TEXT NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    company_name VARCHAR(255),
    added_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMPTZ,
    last_health_score NUMERIC(5,2),
    last_status VARCHAR(100),
    notes TEXT,
    last_deep_analysis_at TIMESTAMPTZ,
    deep_analysis_result JSONB,
    PRIMARY KEY (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_user_watchlists_user_id ON user_watchlists(user_id);
CREATE INDEX IF NOT EXISTS idx_user_watchlists_symbol ON user_watchlists(symbol);
```

### 5.2 Global Analysis Master Table (`stock_analysis_master`)
```sql
CREATE TABLE IF NOT EXISTS stock_analysis_master (
    symbol VARCHAR(30) PRIMARY KEY,
    health_score NUMERIC(5,2),
    status VARCHAR(100),
    deep_analysis_result JSONB,
    last_scanned_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_deep_analysis_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 Master Symbols Table (`master_symbols`)
```sql
CREATE TABLE IF NOT EXISTS master_symbols (
    symbol VARCHAR(30) PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    exchange VARCHAR(10) DEFAULT 'NSE',
    sector VARCHAR(100) DEFAULT 'EQUITY',
    is_active BOOLEAN DEFAULT TRUE,
    last_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### Security & Multi-Tenant Data Isolation
Every SQL query targeting `user_watchlists` enforces explicit user casting (`WHERE user_id::text = %s`). This prevents session token collisions between integer user IDs (e.g. `57880`) and string identifiers (e.g. `'admin'`, `'DEFAULT_USER'`), guaranteeing 100% data isolation across concurrent sessions.

---

## 6. FRONTEND INLINE DOM ARCHITECTURE

### Main Screen Container Rendering (`admin_dashboard.html`)
The Stock Analyzer diagnostic panel is embedded directly into the dashboard layout:

```html
<!-- Main Search Section -->
<div class="search-widget">
  <input type="text" id="stock-search-input" oninput="handleStockSearchInput(this.value)" />
  <div id="stock-autocomplete-dropdown"></div>
</div>

<!-- Inline Main Screen Diagnostic Display (No Modal Overlay) -->
<div id="stock-diagnostic-main-container" style="display:none; margin-bottom:24px;"></div>
```

When a user selects a ticker, `renderStockDiagnosticModal(data)` populates `#stock-diagnostic-main-container`, sets `display = 'block'`, and executes a smooth `scrollIntoView({ behavior: 'smooth', block: 'nearest' })`. Clicking `✕ Close Diagnostic View` hides the container cleanly.

---
*End of Stock Analyzer & Watchlist System API Audit Specification — `docs/STOCK_ANALYZER_API_AUDIT_SPEC.md`*
