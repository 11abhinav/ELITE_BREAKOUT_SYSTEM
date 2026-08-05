# Data Refresh Reference — Elite Breakout System

> Last Updated: 2026-08-04 | All facts derived directly from source code. Do NOT assume — verify against files listed per section.

---

## Purpose

This document maps every category of data in the system to:
1. **What** is stored in DB / disk / RAM
2. **When** it is fetched from external APIs vs. served from cache
3. **What triggers** a re-fetch (TTL, schedule, forced, on-demand)
4. **Which scanners** read that data

Use this document to diagnose stale data bugs, unexpected API calls, or "why is this value outdated?" questions.

---

## Table of Contents

1. [OHLCV Price History (Daily & Intraday)](#1-ohlcv-price-history-daily--intraday)
2. [Live CMP (Current Market Price)](#2-live-cmp-current-market-price)
3. [Fundamental Data (Piotroski, ROE, ROCE, D/E)](#3-fundamental-data-piotroski-roe-roce-de)
4. [Earnings Calendar Dates](#4-earnings-calendar-dates)
5. [Promoter Pledge Data](#5-promoter-pledge-data)
6. [AI Concall Analysis](#6-ai-concall-analysis)
7. [Daily Watchlist (Fundamental Universe)](#7-daily-watchlist-fundamental-universe)
8. [Scanner Health & Signals (DB Output)](#8-scanner-health--signals-db-output)
9. [Full Scanner Schedule Summary](#9-full-scanner-schedule-summary)

---

## 1. OHLCV Price History (Daily & Intraday)

**Source Files**: `app/price_cache.py`, `app/data_providers/upstox_provider.py`, `app/data_providers/fyers_fetcher.py`

### Storage Locations (3 Layers, highest priority first)

| Layer | Location | Contents |
|-------|----------|----------|
| **L1 Session RAM** | `MarketDataSession` immutable object | High-performance in-memory session serving all 6 scanners without network re-fetches |
| **L2 RAM Cache** | `price_cache._cache` dict (process memory) | Pandas DataFrames keyed `(interval, period)` per symbol |
| **L3 Local Disk** | `data/history/{interval}/{symbol}.parquet` | OHLCV columns + technical indicators (EMA20, SMA50, SMA200, RSI, ATR, etc.) |
| **L4 PostgreSQL DB** | PostgreSQL `parquet_cache` table | Compressed bundle of all parquets — uploaded asynchronously post-scan (`upload_history_bundle_to_db`) |

### Cache Key

**Code ref**: `price_cache.py:268–270`

All `1d` requests with any period (`"1y"`, `"6mo"`, `"1mo"`, `"10d"`, `"3mo"`) are normalized to `period="2y"` internally. So ALL EOD, Reversal, Pullback, Wealth Engine, and Multibagger share **one cache key**: `("1d", "2y")`.

### RAM Cache TTL

**Code ref**: `price_cache.py:180–257`

| Interval | RAM TTL |
|----------|--------|
| `1d` | Until market close (15:30 IST), then 12h |
| `5m` | 10 minutes minimum |
| `15m` | 20 minutes minimum |
| `1h` | 30 minutes minimum |
| Pre-market / Off-hours | Until next 09:15 IST open |

### Disk Parquet Fetch Logic (per symbol, on every call)

**Code ref**: `price_cache.py:597–700`

```
IF disk parquet exists:
  → Read last bar timestamp
  → is_up_to_date = check against expected latest closed bar (DailyPolicy / FiveMinutePolicy etc.)
  → is_long_enough = check if days_diff >= 65% of requested period
  → IF up_to_date AND long_enough AND meta_valid:
        ✅ Return cached parquet — ZERO network calls
  → IF up_to_date AND NOT long_enough:
        ⬇️ FULL fetch (extend history)
  → IF NOT up_to_date AND long_enough:
        ⬇️ DELTA fetch: range_from = last_ts - 1 day, range_to = today
  → IF NOT up_to_date AND NOT long_enough:
        ⬇️ FULL fetch
ELSE (no disk parquet):
  → Try cold-start restore from PostgreSQL parquet_cache bundle
  → If not found: FULL fetch from broker API
```

### Parquet Metadata File (`{symbol}.meta.json`)

Each parquet has a companion `.meta.json`:
- `schema_version` must equal `3`
- `indicator_version` must equal `"v5.2"`
- `ohlcv_hash` (SHA-256 of first+last rows) — mismatch forces re-compute of indicators
- `validation_score` — if `< 50` or `"INVALID"` in status, forces re-fetch

### External API Fetch Order (Fallback Chain)

1. **Upstox** (primary) — `api.upstox.com/v2/historical-candle/...`
2. **Fyers** (fallback if Upstox health < threshold) — `api-t1.fyers.in/api/v3/history`
3. **Yahoo Finance** (last resort fallback for price history only if Fyers also fails)

**Rate limit**: Upstox 100 req/10s; parallel `ThreadPoolExecutor(10 workers)`.

### Who Reads Price History

| Scanner | Interval | Period | Entry Point |
|---------|----------|--------|-------------|
| EOD Breakout | `1d` | `2y` | `fetch_watchlist_data()` |
| Reversal | `1d` | `2y` | `fetch_watchlist_data()` |
| Pullback | `1d` | `2y` | `fetch_watchlist_data()` |
| Wealth Engine | `1d` | `2y` (stored as `1y` call, normalized) | `fetch_unified_historical()` |
| Multibagger | `1d` | `2y` | `fetch_watchlist_data()` |
| Multi-TF | `5m`, `15m`, `1h` | `10d` | `get_intraday_snapshot()` |

---

## 2. Live CMP (Current Market Price)

**Source File**: `app/live_prices.py`

| Item | Detail |
|------|--------|
| **Provider** | Upstox `/v3/market-quote/ltp` (batch, 500 symbols/request) |
| **Where Stored** | **NOT saved anywhere** — in-memory dict per scan run only |
| **Fetch Frequency** | Wealth Engine: every **15 min** market hours. Exit monitors: every **5 min** market hours |
| **Market Hours** | 09:15 – 15:30 IST |
| **On failure** | Falls back to previous 1D close price from cached parquet |

**Code ref**: `wealth_engine.py:1287–1292`

```python
all_live_prices = get_live_prices(list(all_symbols_to_fetch)) or {}
# On failure:
logger.warning("Live price fetch failed. Falling back to 1D close.")
all_live_prices = {}
```

### Live Price Stitching into 1D Cache

**Code ref**: `wealth_engine.py:1341–1381`

During market hours, the live CMP is stitched into the 1D historical parquet in RAM (not written to disk):
- If last bar = today: updates the existing bar's `High`, `Low`, `Close` with the live price
- If last bar = yesterday: appends a new synthetic today's candle

This prevents the Wealth Engine from triggering a delta fetch every 15 minutes during market hours.

---

## 3. Fundamental Data (Piotroski, ROE, ROCE, D/E)

**Source File**: `app/fundamentals_cache.py`

### What is Fetched (from Yahoo Finance)

- `t.info`: ROE, ROCE (proxy via `returnOnAssets * 100 * 1.35`), Debt/Equity (`debtToEquity / 100`), Gross Margins, Current Ratio, Operating Cash Flow, Insider Holding, Payout Ratio
- `t.financials`: Annual revenue, Net Income (multi-year for Piotroski P1–P2)
- `t.balance_sheet`: Total Assets, Long-Term Debt, Shares Outstanding (for Piotroski L1–L3, E2)

### Storage Locations

| Layer | Location | Format |
|-------|----------|--------|
| **L1 RAM** | `DatasetRegistry.get("fundamentals_cache")` | Python dict `{symbol: {...}}` |
| **L2 Disk** | `data/fundamentals_cache.json` | JSON file |
| **L3 DB** | PostgreSQL `parquet_cache` table (key `"fundamentals_cache"`) | Serialized JSON file |

### Refresh Schedule — Tiered by Market Cap

**Code ref**: `fundamentals_cache.py:21–25, 402–416`

```python
FUNDAMENTAL_REFRESH_SCHEDULE = {
    "NIFTY_500":     7,    # days — market cap >= Rs. 20,000 Cr
    "NIFTY_MIDCAP":  14,   # days — market cap Rs. 5,000–20,000 Cr
    "SMALLCAP_TAIL": 30,   # days — market cap < Rs. 5,000 Cr
}
```

- **Staleness check** (`is_stale()`): `days_old > FUNDAMENTAL_REFRESH_SCHEDULE[tier]`
- **Failed fetches**: `cache_entry.get("failed") == True` → retry after **2 days**
- **No-data stocks** (e.g. SME board, delisted): Marked `no_data=True`, retried after **2 days**, notification sent to Admin

### When is `refresh_fundamentals_tiered()` Called

**Code ref**: `main.py:1602–1609` (Saturday morning) + `multibagger.py` (within Multibagger Scanner)

1. **Saturday 06:00 AM IST** — `_run_multibagger_scanner_single()` triggers Multibagger which calls `refresh_fundamentals_tiered()`
2. **Daily at 19:00 IST** — Multibagger Scanner runs nightly and calls `refresh_fundamentals_tiered()`
3. **On-demand** — Admin can trigger via dashboard

### Worker Details

- **Concurrency**: `ThreadPoolExecutor(max_workers=1)` — serial fetching to avoid Yahoo rate limits
- **Inter-symbol delay**: `time.sleep(0.1)` between symbols
- **BSE fallback**: If `.NS` ticker returns empty data, automatically retries with `.BO` suffix and saves mapping

### Read Path (Scanner Runtime — Zero Yahoo calls)

**Code ref**: `fundamentals_cache.py:486–491`

```python
def get_fundamentals(symbol: str) -> dict:
    from data_registry import registry
    cache = registry.get("fundamentals_cache")  # L1: RAM
    if not cache:
        cache = load_cache()                     # L2: disk JSON fallback
    return cache.get(symbol) or {}
```

### BSE Mapping Cache

**Code ref**: `app/bse_mapping_utils.py`

- If a stock's `.NS` ticker returns empty data but `.BO` works, the `.BO` symbol is saved in `data/bse_mappings.json` for future fetches.
- Mapping invalidated if later it returns empty data (poisoned mapping).

---

## 4. Earnings Calendar Dates

**Source File**: `app/earnings_calendar.py`

### What is Fetched (from Yahoo Finance)

- `t.calendar["Earnings Date"]`: Next upcoming quarterly results announcement date
- `t.earnings_dates`: All upcoming dates (fallback if calendar is None or empty)

### Storage Location

**PostgreSQL Table**: `earnings_calendar`

Schema: `symbol (PK)`, `earnings_date (DATE)`, `date_status (TEXT)`, `updated_at (TIMESTAMP)`

`date_status` values: `CONFIRMED`, `ESTIMATED`, `UNKNOWN`

### Refresh Schedule

**Code ref**: `earnings_calendar.py:113–119`

| Condition | Behavior |
|-----------|----------|
| Symbol has known date, not today, `updated_at >= NOW() - 45 days` | **SKIP** — cached is still valid |
| Symbol has no date (NULL), `updated_at >= NOW() - 7 days` | **SKIP** — recently checked, still missing |
| Symbol scheduled for results **TODAY** | **ALWAYS re-fetch** (Priority 1) |
| All others | **FETCH** via Yahoo Finance |

- **Batch cap**: Max **100 symbols** per run (enforced by `uncached_symbols[:100]`)
- **Inter-symbol delay**: `time.sleep(1.5)` per symbol

### Scheduler & Worker Active Window

~~`# 22:00 - 23:59 IST — Off-peak evening window`~~ **[RESOLVED on 2026-08-05]**: Synchronized worker schedule active window:
- **Saturday & Sunday (Weekends)**: **03:00 AM – 12:00 PM IST** (`3 <= hour < 12`)
- **Monday – Friday (Working Days)**: **04:00 AM – 06:00 AM IST** (`4 <= hour < 6`)
- In addition to post-market off-peak evening window (22:00 IST).

Runs in a **background daemon thread** (`EarningsCalendarWorker` / `EarningsCalendar-PostMarket`) managed by the Self-Healing Watchdog.

### In-Loop Pause Guard

**Code ref**: `earnings_calendar.py:94–97, 144–146`

```python
if is_scanner_stopped("Earnings Calendar"):
    logger.info("Earnings Calendar is PAUSED by Admin. Skipping refresh cycle.")
    return 0
# Also checked per-symbol inside the fetch loop
```

### Read Path (Scanner Runtime — Zero Yahoo calls)

```python
def get_earnings_info(symbol: str) -> dict:
    # Fast synchronous PostgreSQL lookup only — no network call
    cur.execute("SELECT earnings_date, date_status FROM earnings_calendar WHERE symbol = %s", ...)
```

If symbol not in DB → returns `UNVERIFIED` severity (⚠️) — **not** a false green "safe" signal.

---

## 5. Promoter Pledge Data

**Source File**: `app/pledge_worker.py`

### What is Fetched

- Promoter pledge percentage from Trendlyne/NSE scraper (source varies per implementation)

### Storage Location

**PostgreSQL Table**: `promoter_pledge_cache`

Schema: `symbol (PK)`, `pledge_pct (FLOAT)`, `updated_at (TIMESTAMP)`, `last_attempted_at (TIMESTAMP)`

### Refresh Schedule

**Code ref**: `pledge_worker.py:254–265`

~~`Every 1 hour (continuous)`~~ **[RESOLVED on 2026-08-05]**: Synchronized worker schedule active window:
- **Saturday & Sunday (Weekends)**: **03:00 AM – 12:00 PM IST** (`3 <= hour < 12`)
- **Monday – Friday (Working Days)**: **04:00 AM – 06:00 AM IST** (`4 <= hour < 6`)

- **Stale threshold**: Any symbol with `updated_at < NOW() - 28 days` is stale
- **Worker loop**: Continuous background thread (`worker_loop()`) sleeping 300s when outside active window
- **Concurrency**: 1 worker (serial), with retries for failed symbols
- **Log verbosity**: Per-symbol DB cache reuse logged at `DEBUG` level to prevent log clutter

---

## 6. AI Concall Analysis

**Source File**: `app/ai_worker.py`, `app/database.py`

### What is Fetched

- Earnings call (concall) PDFs from NSE official website
- Sent to AI model for analysis: management confidence score (0–100), key highlights, tone indicators

### Storage Location

**PostgreSQL Table**: `ai_concall_cache_v3`

Schema: `id (PK)`, `symbol`, `pdf_url`, `analysis_data (JSONB)`, `created_at`

Key fields in `analysis_data` JSONB: `management_confidence` (int 0–100), `highlights`, `error` (only on failure)

### Refresh Schedule

~~`Every 1 hour (continuous, 04:00-05:00 IST)`~~ **[RESOLVED on 2026-08-05]**: Synchronized worker schedule active window:
- **Saturday & Sunday (Weekends)**: **03:00 AM – 12:00 PM IST** (`3 <= hour < 12`)
- **Monday – Friday (Working Days)**: **04:00 AM – 06:00 AM IST** (`4 <= hour < 6`)

- **Skip if cached**: `has_valid_concall_cache(symbol)` checks for non-error entry in DB — if found, skips the symbol entirely
- **Error retry window**: `has_error_concall_cache_within_24h()` checks for error entries within last **7 days**. If found, skips (avoids daily retrying NSE timeouts that won't resolve)

### Read Path (Wealth Engine Runtime — Zero network calls)

**Code ref**: `database.py:2939–2960`, `wealth_engine.py:1305`

```python
# Single bulk query — DISTINCT ON (symbol) gets latest per symbol
all_concalls = get_bulk_recent_concall_analysis(
    all_symbols_to_fetch,
    max_age_days=60  # Only return analyses not older than 60 days
)
```

Concall data older than 60 days is treated as missing (returns `{}`) — scanner assigns `AI_Confidence=0`.

### Who Uses Concall Data

| Scanner | Field Read | Default if Missing |
|---------|-----------|-------------------|
| Wealth Engine | `management_confidence` → `AI_Confidence` | `0` |
| Multibagger | `management_confidence` → AI score component | `0` |

---

## 7. Daily Watchlist (Fundamental Universe)

**Source File**: `app/daily_builder.py`

### What is Built

The Daily Builder screens ~5,000 NSE/BSE equities against 16 fundamental filters (EPS > 0, ROE >= 8%, OPM >= 10%, Market Cap >= Rs.1,000 Cr, etc.) to produce a universe of 200–500 qualified stocks.

### Storage Locations

| Layer | Location | Format |
|-------|----------|--------|
| **Disk** | `data/daily_watchlist.parquet` (`WATCHLIST_PATH`) | Parquet with fundamental columns per stock |
| **DB** | PostgreSQL `daily_watchlist` table | Same data, queried by Pledge Worker and AI Worker for symbol universe |
| **Excluded** | `data/daily_watchlist_excluded.parquet` | Stocks that partially qualified — used by Pledge Worker universe |

### Schedule

**Code ref**: `main.py:1395–1411`

```python
# 01:00 AM IST — runs once per day
if now.hour == 1 and not daily_builder_ran:
```

- Runs after `is_scanner_stopped("DAILY_BUILDER")` check
- Takes ~20–40 minutes depending on TradingView/Fyers API speed
- On completion, triggers `ApplicationContext.create_session()` for fresh SessionContext

### Who Reads the Watchlist

| Scanner | When Read | Entry Point |
|---------|-----------|-------------|
| EOD, Reversal, Pullback | 18:00 IST | `pd.read_parquet(WATCHLIST_PATH)` |
| Wealth Engine | 02:00 AM + market hours | `pd.read_parquet(WATCHLIST_PATH)` |
| Multibagger | 04:00 AM + 19:00 IST | `pd.read_parquet(WATCHLIST_PATH)` |
| Multi-TF | 09:14 warmup + market hours | `pd.read_parquet(WATCHLIST_PATH)` |
| Earnings Calendar | 22:00 IST refresh | `pd.read_parquet(WATCHLIST_PATH)` |
| Pledge Worker | Continuous loop | PostgreSQL `daily_watchlist` table |

---

## 8. Scanner Health & Signals (DB Output)

### Alerts Table

**Written by**: EOD, Reversal, Pullback, Multi-TF scanners
**PostgreSQL Table**: `alerts`

Key columns: `symbol`, `scanner`, `alert_date`, `alert_time`, `entry_price`, `stop_loss`, `initial_stop_loss`, `target_1/2/3`, `score`, `status`, `signals`, `category`, `breakout_type`

Alerts are **permanent** — never deleted, only `status` is updated (`OPEN` → `CLOSED`/`EXPIRED`).

### Wealth Buy Alerts Table

**Written by**: Wealth Engine
**PostgreSQL Table**: `wealth_buy_alert`

Key columns: `symbol`, `alert_price`, `alert_date`, `is_closed`, `fm_score`, `portfolio_bucket`, `data_quality`, `engine_version`

### Scanner Health Table

**Written by**: All 13 scanners via `upsert_scanner_health()`
**PostgreSQL Table**: `scanner_health`

Key columns: `scanner_name (PK)`, `status`, `last_success`, `today_alerts`, `error_msg`, `error_severity`, `is_acknowledged`, `processed_count`, `total_count`, `duration_seconds`, `outcome`, `provider_stats`

Valid `status` values: `OK`, `DOWN`, `IDLE`, `RUNNING`, `DEGRADED`, `PAUSED`, `STOPPED`

### System State Table

**Written by**: Various modules via `save_system_state(key, value)`
**PostgreSQL Table**: `system_state`

Used for: Wealth admission gate, last scan metadata, feature flags

---

## 9. Full Scanner Schedule Summary

> All times are **IST (Asia/Kolkata)**.

| Scanner | Schedule | Data Consumed | Output |
|---------|----------|--------------|--------|
| **Daily Builder** | 01:00 AM daily | TradingView fundamentals (Fyers) | `daily_watchlist.parquet`, PostgreSQL `daily_watchlist` |
| **Wealth Engine (initial)** | 02:00 AM daily | Daily parquet (1d/2y), fundamentals cache, pledge, concalls | `elite_wealth_system.parquet`, `wealth_buy_alert` table |
| **Multibagger (initial)** | 04:00 AM daily | Daily parquet (1d/2y), fundamentals cache (triggers refresh), pledge | `alerts` table |
| **Master Symbols Refresh** | 07:00 AM daily | NSE/BSE instrument universe | In-memory symbol registry |
| **Verify Scans** | 08:30 AM daily | DB scanner_health table | Staleness notifications |
| **Cache Warmup** | 09:14:30 AM daily | Fetches 15m cache for watchlist | In-memory `price_cache._cache` |
| **Multi-TF Scanner** | Every 5 min, 09:15 AM – 14:59 PM | 5m/15m/1h intraday candles (Upstox/Fyers), live CMP | `alerts` table (`MULTI_TF` scanner) |
| **Wealth Engine (market hours)** | Every 15 min, 09:15 AM – 15:30 PM | Live CMP (Upstox), 1d parquet from RAM cache, concall cache | `wealth_buy_alert` table |
| **Performance Tracker** | Every 5 min, 09:15 AM – 15:30 PM | Live CMP, existing alerts | `alerts` table (status updates) |
| **Multibagger Exit Monitor** | Every 15 min, 09:15 AM – 15:30 PM | Live CMP, `alerts` table | `alerts` table (exit updates) |
| **Wealth Exit Monitor** | Every 5 min, 09:15 AM – 15:30 PM | Live CMP, `wealth_buy_alert` table | `wealth_buy_alert` table (exit updates) |
| **EOD Scanner** | 18:00 IST daily (after bhavcopy ready) | 1d parquet (2y), fundamentals, pledge, earnings | `alerts` table (`EOD` scanner) |
| **Reversal Scanner** | 18:00 IST daily (sequential after EOD) | 1d parquet (2y), fundamentals, pledge | `alerts` table (`REVERSAL` scanner) |
| **Pullback Scanner** | 18:00 IST daily (sequential after Reversal) | 1d parquet (2y) | `alerts` table (`PULLBACK` scanner) |
| **Multibagger Scanner** | 19:00 IST daily + Saturday 06:00 AM | 1d parquet (2y), fundamentals (triggers refresh), pledge | `alerts` table (`MULTIBAGGER` scanner) |
| **Earnings Calendar** | Sat-Sun 03:00–12:00 IST / Mon-Fri 04:00–06:00 IST + 22:00 IST | Yahoo Finance earnings dates | PostgreSQL `earnings_calendar` table |
| **AI Worker** | Sat-Sun 03:00–12:00 IST / Mon-Fri 04:00–06:00 IST | NSE concall PDFs → AI model | PostgreSQL `ai_concall_cache_v3` table |
| **Pledge Worker** | Sat-Sun 03:00–12:00 IST / Mon-Fri 04:00–06:00 IST | Trendlyne/NSE pledge data | PostgreSQL `promoter_pledge_cache` table |
| **Fundamental Refresh** | Triggered by Multibagger (7/14/30d TTL) | Yahoo Finance financials | `data/fundamentals_cache.json` + PostgreSQL |

---

## 10. Common Bugs & Known Edge Cases

| Symptom | Likely Cause | Where to Check |
|---------|-------------|---------------|
| Wealth Engine shows old CMP | Live price fetch failed (Upstox token expired) | `live_prices.py`, `UPSTOX_ACCESS_TOKEN` env var |
| Earnings risk shows ⚠️ UNVERIFIED | Symbol not in `earnings_calendar` table | Run earnings calendar refresh manually from Admin dashboard |
| Piotroski score = -1 | Yahoo returned empty financials (SME stock, delisted, or bad mapping) | `fundamentals_cache.json` entry, check `"no_data": true` or `"failed": true` |
| Scanner stuck as PAUSED | Admin paused all scanners, wasn't resumed | Admin dashboard → Resume All |
| "Data fetched for only X/Y symbols" | Fyers/Upstox circuit breaker open | `fyers_fetcher._fyers_circuit_breaker.is_open`, `data_provider._price_provider.cooldown_until` |
| Cache returns stale 1D data | `meta.json` schema mismatch after indicator upgrade | Delete `data/history/1d/*.meta.json` to force re-compute |
| Multibagger score = 70 (default) | V5 pipeline threw exception for that symbol | Check logs for `run_pipeline_for_symbol` errors |
