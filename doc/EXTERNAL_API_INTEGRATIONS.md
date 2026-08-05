# External API Integrations & Data Caching Architecture

> Last Updated: 2026-08-04 | Derived from actual code — do NOT assume, verify against source files listed for each section.

---

## 1. Executive Summary & Data Routing Philosophy

**Rule**: Fetch Once → Compute Once → Cache Once → Reuse Many Times.

- **Timezone Standard**: Strict **IST (Asia/Kolkata)** across all storage, logs, and data contracts.
- **WAF/IP Ban Avoidance**: Mass OHLCV price fetching is routed through authorized broker APIs (Upstox/Fyers) to avoid Cloudflare/WAF IP bans on Railway/datacenter IPs that Yahoo Finance triggers.
- **Yahoo Finance**: Restricted to **Fundamentals & Earnings Calendar only** (not price history).
- **Scanner runtime rule**: Scanners only read from cache/DB at runtime. Network fetches happen in background workers.

---

## 2. Price History Data

**Source File**: `app/price_cache.py`

### 2A. Storage Layers (3 levels, highest priority first)

| Level | Storage | What is Stored | Access Speed |
|-------|---------|---------------|-------------|
| **L1** | `MarketDataSession` RAM | Immutable in-memory session serving all 6 scanners without network re-fetches | < 0.0001s |
| **L2** | In-process RAM (`_cache` dict) | Pandas DataFrames keyed by `(interval, period)` | < 0.001s |
| **L3** | Disk Parquet files (`data/history/{interval}/{symbol}.parquet`) | OHLCV + technical indicators per symbol per interval | < 0.01s |
| **L4** | PostgreSQL DB (`parquet_cache` table) | Compressed history bundle — uploaded asynchronously post-scan (`upload_history_bundle_to_db`) | < 0.5s |

### 2B. Cache Key Normalization

**Code ref**: `price_cache.py:268`

All `interval="1d"` requests regardless of `period` (`"1y"`, `"6mo"`, `"1mo"`, `"10d"`, `"3mo"`) are silently normalized to `period="2y"` internally:

```python
if interval == "1d" and period in ("6mo", "1mo", "10d", "3mo", "1y"):
    period = "2y"
```

This ensures EOD Scanner, Reversal Scanner, Pullback Scanner, Wealth Engine, and Multibagger all share **one single cache key** `("1d", "2y")` and do not trigger redundant downloads.

### 2C. RAM Cache TTL (Dynamic Cadence)

**Code ref**: `price_cache.py:180–257`

| Interval | Cache Expires At |
|----------|----------------|
| `1d` (daily) | Market close (15:30 IST) or 12h after close |
| `5m` | 10 minutes (50% of interval duration minimum) |
| `15m` | 20 minutes (50% of interval duration minimum) |
| `1h` | 30 minutes (50% of interval duration minimum) |

Outside market hours, all intervals cache until next market open (9:15 IST).

### 2D. Disk Cache Freshness Decision Tree

**Code ref**: `price_cache.py:597–700`

On every `fetch_watchlist_data()` call, for each symbol:

1. **Read disk parquet** (`data/history/{interval}/{symbol}.parquet`)
2. Check **last bar timestamp** vs expected latest closed bar
3. If `is_up_to_date AND is_long_enough AND meta_valid`:
   - **Return cached disk parquet directly** — **zero network calls**
4. If `is_up_to_date AND NOT is_long_enough`:
   - Force **FULL re-fetch** (period extension needed)
5. If `NOT is_up_to_date AND is_long_enough`:
   - **DELTA fetch only**: `range_from = last_ts - 1 day`, `range_to = today`
   - Appends delta to existing parquet and saves
6. If disk cache missing entirely:
   - Attempt **cold-start restore from PostgreSQL DB** (`restore_history_bundle_from_db`)
   - If DB also missing: **FULL fetch** from broker API

### 2E. Disk Parquet Metadata Validation

Each `{symbol}.parquet` file has a companion `{symbol}.meta.json` containing:
- `schema_version`: Must equal `3` (current `CACHE_SCHEMA_VERSION`)
- `indicator_version`: Must equal `"v5.2"` (current `INDICATOR_VERSION`)
- `ohlcv_hash`: SHA-256 of first/last OHLCV rows (detects data changes)
- `validation_score`: `0–100`; if `< 50` or `INVALID`, forces re-fetch

### 2F. Cold-Start DB Restore

**Code ref**: `price_cache.py:583–590`

If any symbol is missing its disk parquet on server startup, the system calls `restore_history_bundle_from_db(interval)` which bulk-downloads a compressed `.tar.gz` bundle of all parquets from the `parquet_cache` PostgreSQL table in a single operation (< 0.5s vs. ~60s individual API fetches).

---

## 3. Live Price (CMP) Data

**Source File**: `app/live_prices.py`

| Item | Detail |
|------|--------|
| **Provider** | Upstox API v3 LTP endpoint (`/v3/market-quote/ltp`) |
| **Batch Size** | Up to 500 instruments per single HTTP request |
| **Market Hours** | 09:15 – 15:30 IST only |
| **Where Saved** | **NOT saved to DB or disk**. In-memory only, consumed per scan run |
| **Who Uses It** | Wealth Engine (every 15 min), Portfolio Exit Monitor (every 5 min), Multi-TF Scanner (every 5 min), Performance Tracker (every 5 min) |

---

## 4. Upstox API v2 Integration (`UpstoxProvider`)

**Source File**: `app/data_providers/upstox_provider.py`

Primary provider for daily and intraday OHLCV historical data.

### Authentication
- **Token Type**: Long-lived Analytics Access Token (valid 1 year)
- **Environment Variable**: `UPSTOX_ACCESS_TOKEN`
- **Header**: `Authorization: Bearer {UPSTOX_ACCESS_TOKEN}`

### Key Endpoints

| Endpoint | Purpose | Rate Limit |
|---------|---------|-----------|
| `GET /v2/historical-candle/{instrument_key}/{interval}/{to}/{from}` | Historical OHLCV (1 symbol per call) | 100 req/10s |
| `GET /v2/market-quote/quotes?instrument_key=...` | Full market quotes with 5-level bid/ask depth | 500 symbols per call |
| `GET /v3/market-quote/ltp?instrument_key=...` | Ultra-fast live price (LTP only) | 500 symbols per call |

- **Parallelization**: `UpstoxProvider.fetch_batch_ohlcv()` uses `ThreadPoolExecutor(10 workers)` for historical candles.
- **Interval Mapping**: `1m→1minute`, `5m→5minute`, `15m→15minute`, `1h→60minute`, `1d→day`

---

## 5. Fyers API v3 Integration (`FyersProvider`)

**Source File**: `app/data_providers/fyers_fetcher.py`

Secondary fallback if Upstox is down or rate-limited.

### Authentication
- **Base URL**: `https://api-t1.fyers.in/api/v3`
- **Token**: OAuth 2.0 via SHA-256 hash of `client_id + ":" + secret_key`
- **Suffix Rule**: System tries both `-100` (Data) and `-200` (Trading) suffixes to avoid `invalid app id hash` errors.

### Historical Data Endpoint

```json
POST /api/v3/history
{
  "symbol": "NSE:RELIANCE-EQ",
  "resolution": "1D",
  "date_format": "1",
  "range_from": "YYYY-MM-DD",
  "range_to": "YYYY-MM-DD",
  "cont_flag": "1"
}
```

**Error Handling**: HTTP 403 or code `-403` triggers health penalty of `-20` and automatic failover to Yahoo Finance.

---

## 6. Yahoo Finance (`yfinance`) — Fundamentals & Earnings Only

**Source Files**: `app/fundamentals_cache.py`, `app/earnings_calendar.py`

> ⚠️ **Yahoo Finance is NEVER used for OHLCV price history** — it triggers Cloudflare/WAF IP bans on datacenter IPs.

### 6A. Fundamental Data (Piotroski F-Score, ROE, ROCE, Debt/Equity)

**Source File**: `app/fundamentals_cache.py`

#### What is Fetched
- `t.info`: ROE, ROCE (proxy via ROA×1.35), Debt/Equity, Gross Margins, Current Ratio, Operating Cash Flow, Market Cap
- `t.financials`: Revenue, Net Income (multi-year annual), for Piotroski scoring
- `t.balance_sheet`: Total Assets, Long-Term Debt, Shares Outstanding, for Piotroski scoring

#### Where It Is Saved
- **Local JSON file**: `data/fundamentals_cache.json`
- **PostgreSQL DB**: `parquet_cache` table (via `upload_parquet_to_db("fundamentals_cache", ...)`)
- **In-process RAM**: `DatasetRegistry` under key `"fundamentals_cache"`

#### Refresh Schedule (Tiered by Market Cap)

**Code ref**: `fundamentals_cache.py:21–25`

```python
FUNDAMENTAL_REFRESH_SCHEDULE = {
    "NIFTY_500":     7,    # days — market cap >= Rs. 20,000 Cr
    "NIFTY_MIDCAP":  14,   # days — market cap Rs. 5,000–20,000 Cr
    "SMALLCAP_TAIL": 30,   # days — market cap < Rs. 5,000 Cr
}
```

- **Failed/No-Data symbols**: Retried after **2 days** cooldown.
- **When trigger runs**: `refresh_fundamentals_tiered()` called from Multibagger Scanner and Saturday 06:00 AM scheduled run.
- **Worker concurrency**: `ThreadPoolExecutor(max_workers=1)` — serial fetching to avoid rate limits.
- **Inter-request delay**: `time.sleep(0.1)` between symbols (CPU yield for Flask health checks).

#### Read Path (Scanner Runtime)
```python
# app/fundamentals_cache.py:486-491
def get_fundamentals(symbol: str) -> dict:
    from data_registry import registry
    cache = registry.get("fundamentals_cache")  # RAM first
    if not cache:
        cache = load_cache()  # Fall back to disk JSON
    return cache.get(symbol) or {}
```

---

### 6B. Earnings Calendar

**Source File**: `app/earnings_calendar.py`

#### What is Fetched
- `t.calendar["Earnings Date"]`: Next upcoming quarterly results announcement date
- `t.earnings_dates`: Future earnings dates list (fallback if calendar is empty)

#### Where It Is Saved
**PostgreSQL Table**: `earnings_calendar`

```sql
INSERT INTO earnings_calendar (symbol, earnings_date, date_status, updated_at)
ON CONFLICT (symbol) DO UPDATE SET
    earnings_date = EXCLUDED.earnings_date,
    date_status = EXCLUDED.date_status,
    updated_at = NOW()
```

#### Refresh Schedule

**Code ref**: `earnings_calendar.py:113–119`

| Condition | TTL / Retry |
|-----------|-------------|
| Known earnings date (not today) | Skip if `updated_at >= NOW() - 45 days` |
| Missing/NULL earnings date | Skip if `updated_at >= NOW() - 7 days` |
| Stock with results expected TODAY | **Always re-checked** (Priority 1) |

- **Batch limit**: Max **100 symbols** per run.
- **Inter-request delay**: `time.sleep(1.5)` between symbols.
- **Worker concurrency**: 1 (serial, `max_workers=1`).
- **Scheduler**: Runs daily at **22:00–23:59 IST** off-peak window (`main.py:1576–1589`).
- **Rate limiter**: `yf_rate_limiter.py` — max 2 concurrent threads, 0.3s inter-request delay, circuit breaker opens on HTTP 429 for 10 minutes.

#### Read Path (Scanner Runtime)
```python
# app/earnings_calendar.py:178
def get_earnings_info(symbol: str) -> dict:
    # Fast non-blocking DB lookup — NO Yahoo Finance call at runtime
    with get_connection() as conn:
        cur.execute("SELECT earnings_date, date_status FROM earnings_calendar WHERE symbol = %s", ...)
```

#### Severity Levels

| Days to Earnings | Severity | Flag |
|----------------|----------|------|
| 0 (today) | `HIGH_TODAY` 🔴 | `earnings_flag=True` |
| 1–2 days | `HIGH_SOON` 🟠 | `earnings_flag=True` |
| 3–5 days | `MEDIUM_WEEK` 🟡 | `earnings_flag=True` |
| -1 (yesterday) | `MEDIUM_WEEK` 🟡 | `earnings_flag=True` |
| > 5 days | `NONE` 🟢 | `earnings_flag=False` |
| Not in DB | `UNVERIFIED` ⚠️ | `earnings_flag=False` |

---

## 7. Promoter Pledge Data

**Source File**: `app/pledge_worker.py`

#### What is Fetched
- Promoter pledge percentage (from Trendlyne / NSE scraper)

#### Where It Is Saved
**PostgreSQL Table**: `promoter_pledge_cache`

Columns: `symbol`, `pledge_pct`, `updated_at`, `last_attempted_at`

#### Refresh Schedule

**Code ref**: `pledge_worker.py:232, 264`

- **Stale threshold**: Any symbol with `updated_at < NOW() - 28 days` is considered stale.
- **Worker loop**: Continuous background thread, sleeps 1 hour between cycles.
- **Worker concurrency**: 1 (serial).

#### Read Path (Scanner Runtime)
```python
# app/database.py:2857-2888
def get_pledge_map(symbols: list) -> dict:
    # L1: DatasetRegistry RAM cache (fastest)
    cached_pledge = registry.get("promoter_pledge")
    if cached_pledge is not None:
        return {k: v for k, v in cached_pledge.items() if k in symbols}
    # L2: PostgreSQL bulk fetch
    cur.execute("SELECT symbol, pledge_pct FROM promoter_pledge_cache")
    # Saves to DatasetRegistry for future calls
    registry.put("promoter_pledge", pledge_map)
```

---

## 8. AI Concall Analysis

**Source File**: `app/ai_worker.py`, `app/database.py`

#### What is Fetched
- Earnings call (concall) PDFs from NSE website
- AI analysis of management guidance, confidence score (0–100), key highlights

#### Where It Is Saved
**PostgreSQL Table**: `ai_concall_cache_v3`

Columns: `symbol`, `pdf_url`, `analysis_data (JSONB)`, `created_at`

#### Refresh Schedule

**Code ref**: `database.py:2939` (`get_bulk_recent_concall_analysis`)

- **Default TTL**: `max_age_days=60` — Wealth Engine reads concalls not older than **60 days**.
- **Error cache**: Failed fetches are retried after **7 days** (`has_error_concall_cache_within_24h` checks for entries within last 7 days).
- **Worker schedule**: AI Worker runs every **1 hour** (`main.py schedule_map: "AI Worker": "Every 1h"`).

#### Read Path (Wealth Engine Runtime)
```python
# app/wealth_engine.py:1305
all_concalls = get_bulk_recent_concall_analysis(all_symbols_to_fetch, max_age_days=60)
# Uses DISTINCT ON (symbol) — most recent entry per symbol only
# Single DB query for all symbols at once (no N+1)
```

---

## 9. PostgreSQL Database Tables Summary

| Table | Data Stored | Writer | Reader | TTL/Refresh |
|-------|-------------|--------|--------|-------------|
| `earnings_calendar` | Upcoming earnings dates, severity | `earnings_calendar.py` (Yahoo) | All scanners via `get_earnings_info()` | 45d (known) / 7d (missing) |
| `parquet_cache` | Compressed OHLCV history bundles | `price_cache.py` | `restore_history_bundle_from_db()` on cold start | On full fetch completion |
| `ai_concall_cache_v3` | AI concall analysis JSON | `ai_worker.py` | Wealth Engine, Multibagger | 60d TTL (7d error retry) |
| `promoter_pledge_cache` | Pledge % per symbol | `pledge_worker.py` | All scanners via `get_pledge_map()` | 28 days |
| `fundamentals_cache` (JSON file + parquet_cache) | Piotroski score, ROE, ROCE, D/E | `fundamentals_cache.py` (Yahoo) | Wealth Engine, Multibagger via `get_fundamentals()` | 7d / 14d / 30d tiered |
| `scanner_health` | Scanner status, last run, alerts count | All scanners via `upsert_scanner_health()` | Admin Dashboard | Live |
| `alerts` | EOD/Reversal/Pullback/MTF scanner signals | Individual scanners | User & Admin Dashboard | Permanent |
| `wealth_buy_alert` | Wealth Engine BUY signals | `wealth_engine.py` | User Dashboard | Permanent |
| `manual_portfolio` | User-added portfolio positions | User API | Wealth Engine exit monitor | Permanent |
| `system_state` | Key-value config (e.g. admission state) | `database.py` | All scanners | On-demand |
| `daily_watchlist` | Fundamental universe (Daily Builder output) | `daily_builder.py` | All scanners at startup | Daily at 01:00 IST |
| `daily_excluded_watchlist` | Stocks that passed some but not all DB filters | `daily_builder.py` | Pledge Worker universe | Daily at 01:00 IST |

---

## 10. Scanner Control & Pause System

**Source File**: `app/database.py:2513–2566`

### ALL_KNOWN_SCANNERS

**Code ref**: `database.py:2545–2550`

```python
ALL_KNOWN_SCANNERS = [
    'DAILY_BUILDER', 'MULTI_TF', 'EOD', 'REVERSAL',
    'PULLBACK', 'Wealth Engine', 'MULTIBAGGER',
    'PERFORMANCE_TRACKER', 'MULTIBAGGER_EXIT', 'WEALTH_EXIT',
    'Pledge Worker', 'AI Worker', 'Earnings Calendar'
]
```

### How Pause Works

1. Admin calls `pause_all_scanners()` → calls `stop_scanner(name)` for all 13 scanners → `upsert_scanner_health(name, status="PAUSED", ...)` in PostgreSQL.
2. Each scanner's worker loop checks `is_scanner_stopped(name)` via a fast `SELECT status FROM scanner_health WHERE scanner_name = %s` query.
3. If status is `"STOPPED"` or `"PAUSED"`, the loop skips the run and logs the skip.

### In-Loop Pause Guards (mid-run abort)

The following workers check `is_scanner_stopped()` inside their symbol-processing loops for immediate stop:
- `app/earnings_calendar.py` — checked at start and per-symbol
- `app/ai_worker.py` — checked per-symbol
- `app/pledge_worker.py` — checked per-symbol

---

## 11. Data Validation

**Source File**: `app/validation/`, `app/price_cache.py:51–97`

Every dataset from any external provider is validated before being stored:

1. `High >= Low` for every bar
2. `Volume >= 0`
3. No duplicate timestamps
4. Strictly monotonically increasing timestamps (IST)
5. No future timestamps
6. `Close` and `Open` within `±1.5%` of `High/Low` bounds (corporate action tolerance)
7. OHLCV envelope auto-sanitization for split/bonus-adjusted candles

**Validation Score**: `0–100`. Scores `< 50` trigger forced re-fetch on next cycle.

---

## 12. Rate Limiter — Yahoo Finance

**Source File**: `app/yf_rate_limiter.py`

| Setting | Value |
|---------|-------|
| Max concurrent threads | 2 |
| Inter-request delay | 0.3s (+ 0.1s CPU yield for Flask) |
| Circuit breaker trigger | HTTP 429 / "Too Many Requests" / "rate limit" in error string |
| Circuit breaker duration | 10 minutes (blocks all outbound Yahoo calls) |
