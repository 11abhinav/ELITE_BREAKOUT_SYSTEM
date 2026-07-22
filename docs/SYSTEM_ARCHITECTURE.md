# ELITE BREAKOUT SYSTEM — SYSTEM ARCHITECTURE SPECIFICATION

> **Regeneration Notice**: This document is generated directly from source code implementation at commit `8df3c5a8`. Do not edit manually. Regenerate after architectural or behavioral changes. The source implementation under `app/` remains the ultimate source of truth.

| Metadata Field | Value |
|---|---|
| **Canonical Role** | Architecture & High-Level System Guide ("What exists and how does it work?") |
| **Git Commit Hash** | `8df3c5a8` |
| **Generation Date** | `2026-07-22` |
| **Repository Branch** | `main` |
| **Verification Basis** | AST Analysis & Source Code Inspection (`app/`) |

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

## 2. Deployment Architecture

The production environment executes inside a containerized cloud runtime (Railway PaaS):

```mermaid
graph TD
    Railway[Railway Cloud Container] --> Process[Python Main Process app/main.py]
    Process --> Sched[Wall-Clock Scheduler Thread app/main.py]
    Process --> Flask[Flask HTTP Web Server Thread app/dashboard_server.py]
    Process --> DB[(PostgreSQL Connection Pool min=2, max=30 app/database.py)]
    Process --> Telegram[Async Telegram Dispatch Engine app/telegram_engine.py]
    Process --> Push[WebPush VAPID Notification Engine app/push_service.py]
```

---

## 3. Component Catalog

The following catalog defines every subsystem, its primary responsibility, dependencies, and consuming modules:

| Component Module | Subsystem Responsibility | Primary Dependencies | Consumed By |
|---|---|---|---|
| [`app/main.py`](../app/main.py) | Master entrypoint, watchdog, and scheduled loop | `daily_builder`, `scanners`, `database` | System Init / Process Runner |
| [`app/daily_builder.py`](../app/daily_builder.py) | Watchlist generator & fundamental scoring | `data_provider`, `ta`, `pandas` | `main.py`, `scanners` |
| [`app/eod_scanner.py`](../app/eod_scanner.py) | EOD price breakout & volume expansion scanner | `daily_builder`, `sl_target_helper`, `database` | `main.py` scheduler |
| [`app/pullback_pipeline.py`](../app/pullback_pipeline.py) | 3-20 bar orderly retracement scanner | `swing_utils`, `sl_target_helper`, `database` | `main.py` scheduler |
| [`app/reversal_scanner.py`](../app/reversal_scanner.py) | Mean-reversion RSI oversold & Bollinger scanner | `daily_builder`, `sl_target_helper`, `database` | `main.py` scheduler |
| [`app/multi_tf_scanner.py`](../app/multi_tf_scanner.py) | Dual-timeframe (1H/15m) intraday scanner | `data_provider`, `sl_target_helper`, `database` | `main.py` scheduler |
| [`app/wealth_engine.py`](../app/wealth_engine.py) | Long-term fundamental wealth portfolio scanner | `daily_builder`, `database` | `main.py` scheduler |
| [`app/multibagger.py`](../app/multibagger.py) | High-growth multibagger fundamental scanner | `daily_builder`, `database` | `main.py` scheduler |
| [`app/sl_target_helper.py`](../app/sl_target_helper.py) | Structural SL & target cluster engine (V7/V2) | `swing_utils`, `ta`, `config` | All Scanner Engines |
| [`app/database.py`](../app/database.py) | PostgreSQL thread-safe pool manager & DAO | `psycopg2.pool`, `config` | All Scanners & Dashboard |
| [`app/dashboard_server.py`](../app/dashboard_server.py) | Flask REST API & version release server | `database`, `forensics`, `flask` | Admin UI, Railway Health Checks |
| [`app/forensics.py`](../app/forensics.py) | Forensic telemetry & RSS memory profiler | `psutil`, `gc`, `sys` | `main.py`, `dashboard_server.py` |
| [`app/bayesian_updater.py`](../app/bayesian_updater.py) | Dynamic Bayesian regime weighting engine | `numpy`, `scipy` | Scoring & Quality Gates |
| [`app/telegram_engine.py`](../app/telegram_engine.py) | Asynchronous Telegram alert notification dispatch | `requests`, `database` | Alert Persistence DAO |
| [`app/push_service.py`](../app/push_service.py) | WebPush VAPID browser notification engine | `pywebpush`, `database` | Alert Persistence DAO |
| [`app/core/models.py`](../app/core/models.py) | Domain data models (`PullbackCandidate`, `SwingPoint`) | Python `dataclasses` | Entire Codebase |
| [`app/core/enums.py`](../app/core/enums.py) | System domain enums (`PivotKind`, `RejectionReason`) | `enum.Enum` | Entire Codebase |

---

## 4. Module Import Hierarchy & Dependency Tree

```
app/main.py
├── app/daily_builder.py
│   ├── app/data_provider.py
│   └── app/core/models.py
├── app/eod_scanner.py
│   ├── app/sl_target_helper.py
│   │   └── app/swing_utils.py
│   └── app/database.py
├── app/pullback_pipeline.py
│   ├── app/swing_utils.py
│   └── app/sl_target_helper.py
├── app/reversal_scanner.py
│   └── app/sl_target_helper.py
├── app/dashboard_server.py
│   ├── app/database.py
│   └── app/forensics.py
├── app/telegram_engine.py
└── app/push_service.py
```

---

## 5. Architecture Decision Records (ADR)

- **ADR-01: PostgreSQL Storage Engine**  
  *Decision*: Selected PostgreSQL via `psycopg2.pool.ThreadedConnectionPool` over SQLite.

- **ADR-02: Parquet Watchlist Caching**  
  *Decision*: Store daily fundamental scoring matrices in Apache Parquet format (`data/elite_fundamental_watchlist.parquet`).

- **ADR-03: Dynamic Bayesian Regime Weighting**  
  *Decision*: Use `bayesian_updater.py` to dynamically adjust technical and fundamental scoring weights based on market regime (`BULL`, `BEAR`, `SIDEWAYS`).

- **ADR-04: Multi-Layered Cooldown Architecture**  
  *Decision*: Implement two complementary cooldown mechanisms:
  1. *Raw Alert Deduplication*: 4-day (5760-min) window scoped by `(symbol, scanner_name)` composite key to suppress identical alert dispatches.
  2. *Outcome-Aware Loss Cooldown*: 30-business-day database window (`is_symbol_in_failed_reversal_cooldown`) that suppresses re-alerting ONLY if a previous reversal trade closed as a LOSS.

---

## 6. System Glossary

- **Impulse Leg**: A strong directional price move exceeding $8.0\%$ gain and $3.0\times ATR$ expansion lasting $\le 20$ bars forming the anchor for pullback detection.
- **Pullback Retracement**: An orderly $3.0\% - 15.0\%$ price decline lasting 3–20 bars following an impulse leg.
- **Resumption Trigger**: A bullish candle closing in the top of its range ($Close\_Location \ge 0.60$) with volume expansion ($\ge 1.3\times$) and upper wick ratio $\le 0.25$. Zero-range candles (`High == Low == Close`) evaluate $Close\_Location = 1.0$ ONLY if $Close > Prev\_Close$ (Upper Circuit lock).
- **Position Sizing Formula**: $\text{position\_size\_pct} = \min\left(100.0,\ \frac{\text{ACCOUNT\_RISK\_BUDGET\_PCT}}{\text{risk\_pct} / 100.0}\right)$, separating account risk budget (`ACCOUNT_RISK_BUDGET_PCT = 1.0%`) from stop distance cap (`MAX_SL_DISTANCE_PCT = 8.0%`).
- **Natural Target**: A structural resistance target derived from swing highs, pivot resistance, or Fibonacci confluence levels. If a natural cluster exists with $RR < 1.5$, the setup is rejected (`REJ_LOW_RR`).
- **Synthetic Target**: A fallback target generated ONLY when no structural resistance cluster exists, calculated as $\text{entry} + (2.5 \times \text{risk})$.

---

## 7. Out of Scope

The following capabilities are intentionally outside the scope of this core scanning engine:
- **Broker Direct Auto-Execution**: Automatic order routing/execution to live brokerage accounts (Fyers/Zerodha).
- **Crypto & Commodity Markets**: Scanning non-NSE instruments (Cryptocurrencies, Forex, MCX Commodities).
- **High-Frequency Intraday Trading**: Sub-second tick-level order book processing.
