# External API Integrations & Market Data Architecture

This document serves as the comprehensive technical documentation for how the **Elite Breakout System** integrates with external market data providers (Upstox, Fyers, Yahoo Finance, and NSE).

---

## 1. Executive Summary & Routing Philosophy

To prevent Web Application Firewall (WAF) IP bans on cloud hosting providers (e.g., Contabo datacenter IPs blocked by Cloudflare), all high-frequency market data requests are routed through **Authorized Broker APIs**.

- **Timezone Standard:** Strict **IST (Asia/Kolkata)** across all storage, logs, and data contracts.
- **Rule:** **Fetch Once → Compute Once → Cache Once → Reuse Many Times.**
- **Single Entry Point:** Scanners ONLY communicate with `HistoricalDataService`. Outbound external network requests are strictly managed by `FetchCoordinator`.

---

## 2. Upstox API v2 Integration (`UpstoxProvider`)

Upstox serves as the **Primary Provider** for daily and intraday OHLCV historical data and batch market quotes.

### Authentication & Token Management
- **Token Type:** Long-Lived Analytics Access Token (valid for 1 year).
- **Environment Variable:** `UPSTOX_ACCESS_TOKEN`
- **Header:** `Authorization: Bearer {UPSTOX_ACCESS_TOKEN}`

### Key Endpoints Used

#### A. Historical Candles
- **Endpoint:** `GET https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}`
- **Interval Mapping:**
  - `1m` -> `1minute`
  - `5m` -> `5minute`
  - `15m` -> `15minute`
  - `1h` -> `60minute`
  - `1d` -> `day`
- **Parallelization:** Historical endpoints accept 1 symbol per call. `UpstoxProvider.fetch_batch_ohlcv()` uses a `ThreadPoolExecutor` (10 workers) to download historical batches in parallel while adhering to rate limits (100 req / 10s).

#### B. Full Market Quotes (500-Symbol Batch)
- **Endpoint:** `GET https://api.upstox.com/v2/market-quote/quotes?instrument_key={comma_separated_keys}`
- **Batching Capacity:** Up to **500 instruments in a single HTTP request**.
- **Payload Data:** OHLC, 5-Level Bid/Ask Depth, Volume, Last Price, Open Interest.

#### C. LTP Quotes V3
- **Endpoint:** `GET https://api.upstox.com/v3/market-quote/ltp?instrument_key={comma_separated_keys}`
- **Batching Capacity:** Up to 500 instruments in 1 request for ultra-fast price checks.

#### D. Option Greeks
- **Endpoint:** `GET https://api.upstox.com/v2/market-quote/option-greek`
- **Metrics:** Delta, Gamma, Theta, Vega, Implied Volatility (IV).

---

## 3. Fyers API v3 Integration (`FyersProvider`)

Fyers serves as the **Secondary Fallback Provider** if Upstox encounters downtime or rate limit breaches.

### Authentication & OAuth 2.0 Flow
- **Base URL:** `https://api-t1.fyers.in/api/v3`
- **Auth Code Endpoint:** `GET /generate-authcode`
- **Token Endpoint:** `POST /token`
  - `grant_type`: `"authorization_code"`
  - `appIdHash`: SHA-256 hash of `client_id + ":" + secret_key`
  - *App ID Suffix Rule:* System calculates SHA-256 across both `-100` (Data) and `-200` (Trading) suffixes to prevent `invalid app id hash` errors.

### Key Endpoints Used

#### A. Historical Data (History API)
- **Endpoint:** `POST https://api-t1.fyers.in/api/v3/history`
- **Payload:**
  ```json
  {
    "symbol": "NSE:RELIANCE-EQ",
    "resolution": "1D",
    "date_format": "1",
    "range_from": "YYYY-MM-DD",
    "range_to": "YYYY-MM-DD",
    "cont_flag": "1"
  }
  ```
- **Error Handling:** If Fyers returns HTTP 403 or code `-403` ("Additional permission required"), `FyersProvider` penalizes the health score (-20) and triggers failover routing.

#### B. Market Depth & Quotes
- **Endpoint:** `GET /api/v3/quotes`
- **Market Depth:** Setting `ohlcv_flag=1` includes full 5-level order book depth alongside OHLCV snapshots.

---

## 4. Yahoo Finance Integration (Isolated Fundamentals)

Yahoo Finance (`yfinance`) is strictly restricted to **Fundamental Data Fetching** (PE ratio, ROE, Sector, Market Cap).

- **Reason for Isolation:** Mass fetching OHLCV historical prices via Yahoo Finance triggers Cloudflare/WAF IP bans on datacenter VPS providers (Contabo).
- **Execution Rate:** Fundamentals are fetched **once per day/week** and heavily cached in `fundamentals_cache.py`. This low volume uses negligible bandwidth and avoids triggering WAF penalties.

---

## 5. Zero-Trust Data Validation & Provenance

Every dataset returned from any external provider is wrapped into a `NormalizedMarketData` contract and checked by `DataValidationEngine`:
1. `High >= Low`
2. `Volume >= 0`
3. No duplicate timestamps
4. Sorted monotonically increasing timestamps (IST)
5. No future timestamps

Every cached dataset records its **Data Provenance**:
- `provider`: e.g., `"Upstox"`, `"Fyers"`
- `fetch_time`: Timestamp of fetch (IST)
- `latency_ms`: HTTP response latency
- `validation_score`: Quality score (0-100)
