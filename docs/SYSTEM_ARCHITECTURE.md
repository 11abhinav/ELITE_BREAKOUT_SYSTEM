# ELITE BREAKOUT SYSTEM — SYSTEM ARCHITECTURE SPECIFICATION

> **Regeneration Notice**: This document is generated directly from source code implementation. Do not edit manually. Regenerate after architectural or behavioral changes. The implementation under `app/` remains the ultimate source of truth.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Architecture & High-Level System Guide ("What exists and how does it work?") |
| **Git Commit Hash** | `252aa7633ae099f400a59691b7e3f5b090100915` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | Direct AST & Source Code Inspection (`app/`) |

---

## 1. System Overview

The **Elite Breakout System** is an enterprise-grade quantitative trading engine and real-time market scanner optimized for National Stock Exchange (NSE) equity securities. The system processes multi-timeframe price action, order flow, delivery volume, fundamental metrics, and macro regime indicators to generate high-confluence swing, breakout, pullback, and mean-reversion trade alerts.

```mermaid
graph TD
    A[NSE Market Data Feeds] --> B[Data Provider Layer app/data_provider.py]
    B --> C[Watchlist & Parquet Cache app/daily_builder.py]
    C --> D[Scanner Engine Cluster]
    D --> E[SL & Target Engine V7/V2 app/sl_target_helper.py]
    E --> F[Quality & Scoring Gates]
    F --> G[PostgreSQL Pool app/database.py]
    G --> H[Flask Dashboard API app/dashboard_server.py]
    G --> I[Telegram Engine Notifications app/telegram_engine.py]
```

---

## 2. Repository Layout & Architecture Subsystems

The codebase under `app/` is partitioned into distinct architectural layers:

```
app/
├── main.py                          # Master Application Entrypoint & Watchdog
├── config.py                        # System Constants & Environment Configuration
├── database.py                      # PostgreSQL Connection Pool & DAO
├── forensics.py                     # Forensic Telemetry & Memory Profiler
├── dashboard_server.py              # Flask Web API & Dashboard Server
├── daily_builder.py                 # Watchlist Generator & Fundamental Scoring
├── eod_scanner.py                   # EOD Breakout Scanner
├── pullback_pipeline.py             # Pullback Pipeline Engine
├── reversal_scanner.py             # Reversal & Mean-Reversion Scanner
├── multi_tf_scanner.py              # Multi-Timeframe Intraday Scanner
├── wealth_engine.py                 # Wealth Portfolio & Long-Term Engine
├── multibagger.py                   # Multibagger Fundamental Scanner
├── sl_target_helper.py              # Unified SL & Target Engine (V7 / V2)
├── data_provider.py                 # Fyers, YFinance & TradingView Data Feeds
├── bayesian_updater.py              # Bayesian Regime Weighting Engine
├── push_service.py                  # WebPush VAPID Notification Engine
├── telegram_engine.py               # Telegram Alert Dispatch Engine
└── core/                            # Domain Models, Enums & Core Config
    ├── models.py
    ├── enums.py
    └── config.py
```

---

## 3. Startup & Execution Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant Main as app/main.py
    participant DB as app/database.py
    participant Forensics as app/forensics.py
    participant Watchlist as app/daily_builder.py
    participant Web as app/dashboard_server.py
    participant Sched as System Scheduler

    Main->>Forensics: take_snapshot("startup")
    Main->>DB: init_db() [Create Pool min=2 max=30]
    Main->>Watchlist: Ensure elite_fundamental_watchlist.parquet exists
    Main->>Web: Start Flask Web Server (Threaded, Port 8080)
    Main->>Sched: run_system_scheduler() [Background Loop]
```

1. **Forensic Telemetry Initialization**: `forensics.take_snapshot("startup")` captures initial RSS memory, Python heap, and thread count.
2. **PostgreSQL Pool Creation**: `database.init_db()` initializes a thread-safe `ThreadedConnectionPool` (min 2, max 30 connections).
3. **Watchlist Verification**: Checks for `data/elite_fundamental_watchlist.parquet`. If missing, executes `safe_run_daily_builder()`.
4. **Flask Dashboard Server**: Spawns web dashboard and `/version` build metadata API in a daemon thread on `$PORT` (default 8080).
5. **Scheduler Execution Loop**: Enters `run_system_scheduler()` executing scheduled job handlers on wall-clock market triggers.

---

## 4. Scheduler Architecture

Discovered job handlers registered in [`app/main.py`](../app/main.py):

| Job Handler | Schedule Window (IST) | Target Subsystem | Architectural Purpose |
|---|---|---|---|
| `safe_run_daily_builder()` | 08:30 AM | [`app/daily_builder.py`](../app/daily_builder.py) | Builds daily fundamental watchlist parquet file |
| `run_pledge_worker()` | 09:00 AM | [`app/daily_builder.py`](../app/daily_builder.py) | Fetches BSE promoter pledge percentage data |
| `multi_tf_scanner.start()` | 09:15 AM - 03:30 PM (Hourly) | [`app/multi_tf_scanner.py`](../app/multi_tf_scanner.py) | Scans intraday 15m/1h candle consolidations |
| `pullback_pipeline.run_pullback_pipeline()` | 03:45 PM | [`app/pullback_pipeline.py`](../app/pullback_pipeline.py) | Scans 3-15 day orderly retracements |
| `reversal_scanner.start()` | 04:00 PM | [`app/reversal_scanner.py`](../app/reversal_scanner.py) | Scans RSI oversold & lower Bollinger dips |
| `eod_scanner.start()` | 04:15 PM | [`app/eod_scanner.py`](../app/eod_scanner.py) | Scans EOD price breakouts & volume expansion |
| `wealth_engine.run_wealth_scan()` | 04:30 PM | [`app/wealth_engine.py`](../app/wealth_engine.py) | Rebalances wealth portfolio candidates |

---

## 5. Scanner Pipelines Overview

```mermaid
graph LR
    Sub[Watchlist Parquet Universe] --> Quality[Data Quality & Regime Filter]
    Quality --> Pattern[Scanner Pattern Engine]
    Pattern --> SL[SL & Target Engine V7/V2]
    SL --> Dedupe[Deduplication & Cooldown]
    Dedupe --> DB[(PostgreSQL Pool)]
    DB --> Web[Flask Dashboard API]
```

---

## 6. Database Schema & Pooling Architecture

- **Pool Manager**: [`app/database.py`](../app/database.py) thread-safe `ThreadedConnectionPool`.
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

---

## 7. Threading Model & Memory Profiling

- **Process Model**: Single Python process executing main scheduler thread with dedicated background daemon threads for Flask HTTP API ([`app/dashboard_server.py`](../app/dashboard_server.py)), Telegram dispatch ([`app/telegram_engine.py`](../app/telegram_engine.py)), and WebPush VAPID notifications ([`app/push_service.py`](../app/push_service.py)).
- **Memory Ownership**: Process RSS memory profiled via `ForensicTelemetry` ([`app/forensics.py`](../app/forensics.py)). Container threshold set to $< 450.0$ MB RSS with explicit `gc.collect()` memory reclamation cycles.

---

## 8. Out of Scope

The following capabilities are intentionally outside the scope of this core scanning engine:
- **Broker Direct Auto-Execution**: Automatic order routing/execution to live brokerage accounts (Fyers/Zerodha).
- **Crypto & Commodity Markets**: Scanning non-NSE instruments (Cryptocurrencies, Forex, MCX Commodities).
- **High-Frequency Intraday Trading**: Sub-second tick-level order book processing.
- **Backtesting Visual GUI**: Interactive web-based backtest strategy builder.

---

## 9. Verification & Traceability

- **Source Modules Discovered**: `app/main.py`, `app/database.py`, `app/sl_target_helper.py`, `app/daily_builder.py`, `app/eod_scanner.py`, `app/pullback_pipeline.py`, `app/reversal_scanner.py`, `app/wealth_engine.py`, `app/dashboard_server.py`.
- **Verified Against Commit**: `252aa7633ae099f400a59691b7e3f5b090100915`
