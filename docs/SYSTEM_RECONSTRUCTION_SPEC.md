# ELITE BREAKOUT SYSTEM — SYSTEM RECONSTRUCTION SPECIFICATION

> **Document Class:** System Reconstruction Blueprint
> **Status:** Step-by-step operational guide for building the exact system from scratch.
> **Target File:** `docs/SYSTEM_RECONSTRUCTION_SPEC.md`
> **Last Synchronized:** 2026-07-25 (v8.4.2+)

---

# 1. REPOSITORY RECONSTRUCTION & MODULE INVENTORY

To reconstruct the Elite Breakout System, the following directory structure and core modules MUST be assembled in the application workspace (`app/`):

```text
ELITE_BREAKOUT_SYSTEM/
├── app/
│   ├── main.py                     # Scheduler & 24/7 Background Master Loop
│   ├── application_context.py      # Process-lifetime Singleton Context & Registry
│   ├── session_context.py          # Trading-Day Session Lifecycle & Cache Tear-down
│   ├── dataset_registry.py         # Memory Registry (PERSISTENT, SESSION, EPHEMERAL)
│   ├── eod_scanner.py              # Post-market Daily Breakout Scanner
│   ├── reversal_scanner.py         # Mean-Reversion Oversold Bounce Scanner
│   ├── pullback_pipeline.py        # Trend Pullback Continuation Scanner
│   ├── multi_tf_scanner.py         # Intraday 4-Stage Multi-Timeframe Cascade Scanner
│   ├── wealth_engine.py            # Long-term Fundamental Screener & Intraday Exit Monitor
│   ├── multibagger.py              # Compounder Screener & 15m Exit Monitor
│   ├── scoring_engine.py           # Centralized Candidate Scoring Engine (0-100)
│   ├── sl_target_helper.py         # Dynamic Stop-Loss & Target Engine + Validator
│   ├── trade_ranking_engine.py     # Multi-Factor Candidate Ranking Engine
│   ├── macro_utils.py              # Market Regime Engine & Sector Ranking Calculator
│   ├── strategy_policy.py          # Strategy Policy Engine (Regime-aware thresholds)
│   ├── forensic_engine.py          # Forensic Risk Engine (CFO/PAT, Debt, Risk Tiers)
│   ├── quality_trajectory.py       # Fundamentals Quality Trajectory Engine
│   ├── data_provider.py            # High-Level Data Provider Boundary
│   ├── price_cache.py              # Centralized Price Cache & Monotonic Timestamp Normalizer
│   ├── price_provider.py           # BSE Fallback & Rate Limiter Boundary
│   ├── delivery_data.py            # NSE Bhavcopy Delivery Scraper & Fallback
│   ├── surveillance.py             # NSE ASM/GSM Blacklist Scraper
│   ├── database.py                 # PostgreSQL Driver, Migration & CRUD Interface
│   ├── lock_utils.py               # ProcessLock (Flock + PG Advisory Lock)
│   ├── memory_profiler.py          # MemoryProfiler & BatchMemoryTracker
│   ├── telemetry_manager.py        # TelemetryManager & Session Timeline
│   ├── dashboard_server.py         # Flask REST API & Web Dashboard Server
│   └── data_providers/
│       ├── provider_selector.py    # Provider Routing Authority
│       ├── unified_fetcher.py      # Unified Fetcher (Fyers -> YFinance -> BSE)
│       └── fyers_fetcher.py        # Fyers REST API Client
├── config.py                       # System Constants, Thresholds & Provider Routing Policies
├── core_enums.py                   # System Enums (CandidateState, ProviderResult, etc.)
├── core_models.py                  # DTO Dataclasses (PullbackCandidate, DataQualityError)
├── pytest.ini                      # Pytest Configuration
├── requirements.txt                # Python Dependency Specification
└── tests/
    ├── test_production_deployment_gates.py # 17 Deployment Verification Gates
    └── test_scanner_smoke.py       # Scanner Smoke Test Suite
```

---

# 2. ENVIRONMENT SETUP & DEPENDENCY BOOTSTRAP

## 2.1 System Prerequisites
- **Operating System**: macOS / Linux (Ubuntu 22.04 LTS / Debian)
- **Python Version**: Python 3.9.x (3.9.6 recommended)
- **Database**: PostgreSQL 14+ with JSONB support
- **Virtual Environment**: Python `venv` created at repository root (`venv/`)

## 2.2 Python Dependencies (`requirements.txt`)
```text
flask>=3.0.0
psycopg2-binary>=2.9.9
pandas>=2.1.0
numpy>=1.26.0
yfinance>=0.2.35
fyers-apiv3>=3.0.0
pycryptodome>=3.19.0
requests>=2.31.0
curl-cffi>=0.6.2
pytest>=8.0.0
pytest-mock>=3.12.0
pytest-cov>=4.1.0
psutil>=5.9.0
```

## 2.3 Required Environment Variables
Set the following environment variables in `.env` or system environment:
```bash
# Database Connection (PostgreSQL)
DATABASE_URL="postgresql://postgres:password@localhost:5432/elite_breakout"

# Notifications & Alerts
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyZ"
TELEGRAM_CHAT_ID="-1001234567890"

# Broker API (Fyers API v3)
FYERS_CLIENT_ID="XX12345-100"
FYERS_SECRET_KEY="ABC123XYZ"
FYERS_REDIRECT_URI="http://127.0.0.1:5000/fyers/callback"

# Proxy Scraper API (Optional for NSE Scrapers)
SCRAPERAPI_KEY="your_scraperapi_key_here"

# Batching & Memory Tuning
PULLBACK_FETCH_BATCH_SIZE="50"
EOD_FETCH_BATCH_SIZE="50"
```

---

# 3. DATABASE INITIALIZATION & SCHEMA BOOTSTRAP

To reconstruct the database, run `python3 -c "import database; database.init_db()"` or execute the following SQL migration script in PostgreSQL:

```sql
-- Create Core Tables
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

CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT PRIMARY KEY,
    bse_symbol TEXT NOT NULL,
    mapping_state TEXT NOT NULL DEFAULT 'ACTIVE',
    failure_count INTEGER DEFAULT 0,
    retry_after TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

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
```

---

# 4. SYSTEM EXECUTION & RECONSTRUCTION VERIFICATION

## 4.1 Step-by-Step System Launch Sequence
1. **Activate Environment**: `source venv/bin/activate`
2. **Verify Compilation**: `python3 -m py_compile app/*.py`
3. **Execute Deployment Gates**: `python3 -m pytest tests/test_production_deployment_gates.py`
4. **Run Complete Test Suite**: `python3 -m pytest tests/` (Must achieve 100% pass rate: 332 passed).
5. **Start System Server**: `python3 app/dashboard_server.py` (Launches REST API on port 5000).
6. **Start Background Scheduler**: `python3 app/main.py` (Launches 24/7 background scanner daemon).

---
*End of Master System Reconstruction Specification — `docs/SYSTEM_RECONSTRUCTION_SPEC.md`*
