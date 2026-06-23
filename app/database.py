# =====================================================================================
# app/database.py
#
# KEY DESIGN DECISIONS:
#
# 1. ONE-TIME INIT:  init_db() is guarded by a module-level lock + flag.
#    No matter how many scanners call it simultaneously, the CREATE TABLE
#    SQL runs exactly once per process lifetime. After that, every call
#    returns immediately — zero DB round trips, zero race conditions.
#
# 2. WHY STILL CALL init_db() IN EACH SCANNER?
#    On a fresh Railway deploy the table doesn't exist yet. We can't remove
#    the call entirely. But with the lock it's safe for all scanners to call
#    it — the second caller just sees _DB_INITIALIZED=True and returns.
#
# 3. RACE CONDITION FIX:
#    The old crash was:
#      psycopg2.errors.UniqueViolation: duplicate key value violates
#      unique constraint "pg_type_typname_nsp_index"
#    This happens when Postgres processes two simultaneous CREATE TABLE
#    statements for the same table name even with IF NOT EXISTS — it's a
#    known Postgres internal type-registry bug under concurrency.
#    The lock below makes it impossible for two threads to reach that
#    SQL at the same time.
# =====================================================================================

import os
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd


from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_DB_WRITE_LOCK = threading.RLock()

# When True, scanners should not persist alerts to the database. Used for
# startup self-tests and dry-runs where we want to exercise scanner logic
# without polluting the alerts table or triggering downstream systems.
DONT_SAVE_ALERTS = False

# When True, Wealth Engine should not persist parquet files or write buy alerts.
# Controlled by the startup self-test to prevent altering wealth data on boot.
DONT_SAVE_WEALTH = False

# ── Connection pool ───────────────────────────────────────────────────────────────────
_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
# Semaphore to limit concurrent active connections to the pool (prevents noisy exhaustion)
_conn_semaphore: Optional[threading.BoundedSemaphore] = None

def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool, _conn_semaphore
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:          # double-checked locking
            return _pool
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL env var is not set. "
                "Add the Railway Postgres addon and it will be injected automatically."
            )
        # Configure pool size via env override if provided (fallback to 30)
        maxconn = int(os.getenv("DB_MAXCONN", "30"))
        minconn = int(os.getenv("DB_MINCONN", "2"))
        _pool = pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            dsn=db_url,
            connect_timeout=5  # Add 5s timeout instead of hanging indefinitely
        )
        try:
            # Initialize semaphore to mirror pool capacity
            _conn_semaphore = threading.BoundedSemaphore(value=maxconn)
        except Exception:
            _conn_semaphore = None
        logger.info(f"✅ Postgres connection pool created (5s timeout) | min={minconn} max={maxconn}")
        return _pool


@contextmanager
def get_connection(timeout: int = 5):
    """Get DB connection with circuit breaker pattern.

    Acquires an internal semaphore before checking out a connection from the pool.
    This prevents busy loops from exhausting the pool and creating noisy logs.
    """
    from psycopg2 import OperationalError, DatabaseError

    p = _get_pool()
    conn = None
    acquired = False
    try:
        global _conn_semaphore
        # Ensure semaphore exists (in case pool was created elsewhere)
        if _conn_semaphore is None:
            try:
                _conn_semaphore = threading.BoundedSemaphore(value=getattr(p, 'maxconn', 30))
            except Exception:
                _conn_semaphore = None
        if _conn_semaphore is not None:
            acquired = _conn_semaphore.acquire(timeout=timeout)
            if not acquired:
                raise OperationalError('Connection pool exhausted (acquire timeout)')

        conn = p.getconn()
        # Test connection is alive before returning
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        yield conn
    except OperationalError as e:
        # Circuit breaker: log and fail fast instead of hanging
        logger.error(f"🔴 DB connection failed (circuit breaker): {e}")
        if conn:
            try:
                p.putconn(conn, close=True)  # Return broken connection to pool
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"🔴 DB operation failed: {e}")
        if conn:
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
        raise
    finally:
        # Return connection to pool if we checked one out
        if conn:
            try:
                p.putconn(conn)
            except Exception:
                pass
        # Release semaphore if we acquired it
        if _conn_semaphore is not None and acquired:
            try:
                _conn_semaphore.release()
            except Exception:
                pass


# ── One-time init guard ───────────────────────────────────────────────────────────────
_DB_INITIALIZED = False
_INIT_LOCK = threading.Lock()



def insert_notification(notif_type: str, title: str, message: str, symbol: str = None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO global_notifications (type, title, message, symbol)
                    VALUES (%s, %s, %s, %s)
                ''', (notif_type, title, message, symbol))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to insert notification: {e}")

def init_db():
    global _DB_INITIALIZED

    if _DB_INITIALIZED:
        return

    with _INIT_LOCK:
        if _DB_INITIALIZED:
            return

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS alerts (
                        id            SERIAL PRIMARY KEY,
                        symbol        TEXT    NOT NULL,
                        breakout_type TEXT    NOT NULL,
                        alert_time    TEXT    NOT NULL,
                        alert_date    TEXT    NOT NULL DEFAULT (CURRENT_DATE::TEXT),
                        scanner       TEXT,
                        category      TEXT,
                        entry_price   REAL,
                        stop_loss     REAL,
                        signals       TEXT,
                        score         INTEGER,
                        rsi           REAL,
                        volume_ratio  REAL,
                        UNIQUE (symbol, breakout_type, alert_date)
                    )
                """)

                cur.execute("""
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
                        last_updated TIMESTAMPTZ DEFAULT NOW(),
                        context_json TEXT
                    )
                """)
                # ── MIGRATIONS: safe to run every deploy ─────────────────────────────
                # Drop dependent views before altering columns, they will be recreated below
                cur.execute("DROP VIEW IF EXISTS v_trade_analytics CASCADE")
                
                cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'alerts'
                        AND column_name = 'alert_time'
                        AND data_type = 'text'
                    ) THEN
                        ALTER TABLE alerts ALTER COLUMN alert_time TYPE TIMESTAMPTZ USING alert_time::timestamptz;
                        ALTER TABLE alerts ALTER COLUMN alert_time SET DEFAULT NOW();
                    END IF;
                END $$;
                """)
                cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'alerts'
                        AND column_name = 'score'
                        AND data_type = 'integer'
                    ) THEN
                        ALTER TABLE alerts ALTER COLUMN score TYPE REAL USING score::real;
                    END IF;
                END $$;
                """)
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()")
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
                for col_sql in [
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS stop_loss    REAL",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS target_price REAL",
                    # Performance tracker write-back columns
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS status       TEXT    DEFAULT 'OPEN'",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS exit_price   REAL",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS pnl_pct      REAL",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS closed_at    TEXT",
                    # Portfolio tracking columns
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS capital_allocated REAL DEFAULT 0.0",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS shares_bought     INTEGER DEFAULT 0",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS pnl_rs            REAL",
                    # Diagnostic parameters context JSONB
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS context      JSONB",
                    # Bayesian Tracker Columns
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS model_version  TEXT DEFAULT 'v1'",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bayesian_regime TEXT DEFAULT 'BULL'",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bayesian_weights JSONB",
                    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS data_partition TEXT DEFAULT 'TRAIN'",
                ]:
                    cur.execute(col_sql)
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS seen_by_user BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS seen_by_admin BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS cash_in_hand REAL DEFAULT 0.0")
                cur.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS is_rejected BOOLEAN DEFAULT FALSE")

                # ── Breakout Watchlist Metadata Columns (Multi-TF Funnel) ─────────
                cur.execute("ALTER TABLE breakout_watchlist ADD COLUMN IF NOT EXISTS trigger_level REAL")
                cur.execute("ALTER TABLE breakout_watchlist ADD COLUMN IF NOT EXISTS invalidation_level REAL")
                cur.execute("ALTER TABLE breakout_watchlist ADD COLUMN IF NOT EXISTS max_extension_atr REAL")
                cur.execute("ALTER TABLE breakout_watchlist ADD COLUMN IF NOT EXISTS buffer_pct REAL")
                cur.execute("ALTER TABLE breakout_watchlist ADD COLUMN IF NOT EXISTS armed_at TIMESTAMPTZ")


                # ── Score Weight Log (Bayesian Versioning) ─────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS score_weight_log (
                        id SERIAL PRIMARY KEY,
                        model_version TEXT NOT NULL,
                        regime TEXT NOT NULL,
                        weights JSONB NOT NULL,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT)
                    )
                """)
                cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'chk_weights_json'
                    ) THEN
                        ALTER TABLE score_weight_log 
                            ADD CONSTRAINT chk_weights_json 
                            CHECK (weights ? 'volume_breakout' AND weights ? 'rsi_divergence' AND weights ? 'ema_crossover') 
                            NOT VALID;
                    END IF;
                END $$;
                """)

                # ── Bayesian Model Updates (Pending Admin Approval) ──────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bayesian_model_updates (
                        id SERIAL PRIMARY KEY,
                        regime TEXT NOT NULL,
                        proposed_version TEXT NOT NULL,
                        current_version TEXT NOT NULL,
                        current_weights JSONB NOT NULL,
                        proposed_weights JSONB NOT NULL,
                        trades_analyzed INTEGER NOT NULL,
                        win_rate REAL NOT NULL,
                        reason TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        admin_comment TEXT,
                        approved_by TEXT,
                        approved_at TEXT,
                        rejected_at TEXT,
                        applied_at TEXT,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        expires_at TEXT
                    )
                """)

                # ── Scanner health table — source of truth for dashboard ───────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS scanner_health (
                        scanner_name  TEXT PRIMARY KEY,
                        status        TEXT    NOT NULL DEFAULT 'IDLE',
                        last_success  TEXT,
                        today_alerts  INTEGER NOT NULL DEFAULT 0,
                        error_msg     TEXT,
                        is_acknowledged BOOLEAN DEFAULT TRUE,
                        updated_at    TEXT    NOT NULL,
                        error_severity TEXT DEFAULT NULL,
                        error_count    INTEGER DEFAULT 0,
                        first_error_at TEXT DEFAULT NULL,
                        retry_count    INTEGER DEFAULT 0,
                        scheduled_for  TEXT DEFAULT NULL
                    )
                """)
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS is_acknowledged BOOLEAN DEFAULT TRUE")
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS error_severity TEXT DEFAULT NULL")
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS first_error_at TEXT DEFAULT NULL")
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE scanner_health ADD COLUMN IF NOT EXISTS scheduled_for TEXT DEFAULT NULL")


                # ── System state table for dashboard metrics / state caching ───────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS system_state (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)

                # ── AI Concall Cache table ─────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_concall_cache_v3 (
                        id            SERIAL PRIMARY KEY,
                        symbol        TEXT NOT NULL,
                        pdf_url       TEXT UNIQUE NOT NULL,
                        analysis_data JSONB NOT NULL,
                        created_at    TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT)
                    )
                """)

                # ── Promoter Pledge Cache table ────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS promoter_pledge_cache (
                        symbol        TEXT PRIMARY KEY,
                        pledge_pct    REAL NOT NULL,
                        updated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                    )
                """)

                # ── Fetch error aggregation table (skipped records / fetch failures) ──
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS fetch_errors (
                        id SERIAL PRIMARY KEY,
                        source_name TEXT NOT NULL,
                        scanner_name TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        interval TEXT,
                        category TEXT NOT NULL,
                        occurrences INTEGER NOT NULL DEFAULT 1,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        last_error_msg TEXT,
                        is_acknowledged BOOLEAN DEFAULT FALSE
                    )
                """)
                # Ensure a uniqueness constraint for upsert logic
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_fetch_errors_uni ON fetch_errors (source_name, scanner_name, symbol, interval, category)")
                
                # Add missing indexes for frequently queried columns
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts(alert_date)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_symbol_date ON alerts(symbol, alert_date)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_cooldown ON alerts (symbol, scanner, breakout_type, alert_time DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_scanner_health_name ON scanner_health(scanner_name)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_data_fetch_health_source ON data_fetch_health(source_name)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_data_cache_metadata_key ON data_cache_metadata(key)")

                # ── Trade analytics view mapping JSONB context to columns ───────────
                cur.execute("""
                    CREATE OR REPLACE VIEW v_trade_analytics AS
                    SELECT 
                        id,
                        symbol,
                        alert_time,
                        alert_date,
                        scanner,
                        category,
                        entry_price,
                        stop_loss,
                        target_price,
                        status,
                        exit_price,
                        pnl_pct,
                        closed_at,
                        -- Technical indicators
                        (context->'technicals'->>'above_ema20')::boolean AS above_ema20,
                        (context->'technicals'->>'above_sma50')::boolean AS above_sma50,
                        (context->'technicals'->>'golden_cross')::boolean AS golden_cross,
                        (context->'technicals'->>'body_ratio')::float AS body_ratio,
                        (context->'technicals'->>'delivery_pct')::float AS delivery_pct,
                        (context->'technicals'->>'rsi')::float AS rsi,
                        (context->'technicals'->>'volume_ratio')::float AS volume_ratio,
                        -- Session prices
                        (context->'session'->>'open')::float AS session_open,
                        (context->'session'->>'day_high')::float AS session_day_high,
                        (context->'session'->>'day_low')::float AS session_day_low,
                        -- Fundamentals
                        (context->'fundamentals'->>'peg')::float AS peg,
                        (context->'fundamentals'->>'yoy_rev')::float AS yoy_rev,
                        (context->'fundamentals'->>'yoy_profit')::float AS yoy_profit,
                        (context->'fundamentals'->>'roe')::float AS roe,
                        -- Execution strategies
                        context->'execution'->>'sl_method' AS sl_method,
                        context->'execution'->>'t_method' AS t_method,
                        context->'execution'->>'trail_note' AS trail_note
                    FROM alerts;
                """)

                # ── Data cache metadata table (cache keys, last fetched, cadence) ──
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS data_cache_metadata (
                        key TEXT PRIMARY KEY,
                        last_fetched TEXT NOT NULL,
                        cadence_seconds INTEGER NOT NULL,
                        rows INTEGER,
                        etag TEXT,
                        source TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)

                # ── Data fetch health table for external systems (monitoring) ─────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS data_fetch_health (
                        source_name TEXT PRIMARY KEY,
                        last_success TEXT,
                        last_failure TEXT,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        error_msg TEXT,
                        is_acknowledged BOOLEAN DEFAULT TRUE,
                        updated_at TEXT NOT NULL
                    )
                """)
                cur.execute("ALTER TABLE data_fetch_health ADD COLUMN IF NOT EXISTS is_acknowledged BOOLEAN DEFAULT TRUE")

                # ── System Logs Table (for Unhandled Errors / App Crashes) ─────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS system_logs (
                        id SERIAL PRIMARY KEY,
                        level TEXT NOT NULL,
                        module TEXT NOT NULL,
                        message TEXT NOT NULL,
                        traceback TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        is_acknowledged BOOLEAN DEFAULT FALSE
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_ack ON system_logs(is_acknowledged)")


                # ── Manual Portfolio Tracker ──────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS manual_portfolio (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        entry_date DATE NOT NULL,
                        entry_price REAL NOT NULL,
                        quantity INTEGER NOT NULL,
                        added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE manual_portfolio ADD COLUMN IF NOT EXISTS hold_score_entry INTEGER")
                cur.execute("ALTER TABLE manual_portfolio ADD COLUMN IF NOT EXISTS hold_score_current INTEGER")
                cur.execute("ALTER TABLE manual_portfolio ADD COLUMN IF NOT EXISTS re_eval_due_date DATE")

                # ── Parquet Binary Cache ──────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS parquet_cache (
                        name TEXT,
                        date TEXT,
                        data BYTEA,
                        PRIMARY KEY (name, date)
                    )
                """)

                # ── Unified Notification Center ─────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS global_notifications (
                        id SERIAL PRIMARY KEY,
                        type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        message TEXT NOT NULL,
                        symbol TEXT,
                        is_seen BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                # ── System checkpoints table (persistent audit trail) ─────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS system_checkpoints (
                        id SERIAL PRIMARY KEY,
                        checkpoint_name TEXT UNIQUE NOT NULL,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        updated_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        content TEXT NOT NULL,
                        reason TEXT DEFAULT ''
                    )
                """)

                # ── Telegram Queue table (persistent alert queue with rate limiting) ──
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_queue (
                        id SERIAL PRIMARY KEY,
                        alert_id INTEGER REFERENCES alerts(id),
                        symbol TEXT NOT NULL,
                        message_text TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        retry_count INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        sent_at TEXT DEFAULT NULL
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_telegram_queue_status ON telegram_queue(status)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_telegram_queue_created ON telegram_queue(created_at)")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS wealth_score_history (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        evaluation_date DATE NOT NULL,
                        hold_score REAL,
                        fm_score REAL,
                        rs_6m REAL,
                        cmp REAL,
                        sma_200 REAL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(symbol, evaluation_date)
                    )
                """)

                # ── Wealth Buy Alerts table (historical tracking of buy signals) ──
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS wealth_buy_alert (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        alert_price REAL NOT NULL,
                        alert_date TEXT NOT NULL DEFAULT (CURRENT_DATE::TEXT),
                        alert_time TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        breakout_type TEXT,
                        fm_score REAL,
                        status TEXT DEFAULT 'ACTIVE',
                        current_price REAL,
                        current_score REAL,
                        status_updated_at TEXT DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        notes TEXT,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        entry_signal TEXT,
                        exit_signal TEXT,
                        exit_price REAL,
                        exit_date TEXT,
                        exit_time TEXT,
                        is_closed BOOLEAN DEFAULT FALSE,
                        pnl_rs REAL,
                        pnl_pct REAL
                    )
                """)
                
                # Add migration columns if table already exists (for backward compatibility)
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS entry_signal TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS exit_signal TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS exit_price REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS exit_date TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS exit_time TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS is_closed BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS pnl_rs REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS pnl_pct REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS position_pct REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS position_amount REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS position_shares INTEGER")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS portfolio_bucket TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS valuation_score REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS current_score REAL")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS momentum_score INTEGER")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS momentum_confidence TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS data_quality TEXT")
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS fallback_timestamp TIMESTAMPTZ")
                
                # Create indexes (after columns are guaranteed to exist)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_wealth_alert_symbol ON wealth_buy_alert(symbol)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_wealth_alert_date ON wealth_buy_alert(alert_date)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_wealth_alert_status ON wealth_buy_alert(status)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_wealth_alert_is_closed ON wealth_buy_alert(is_closed)")
                
                # Audit and status columns migration
                cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'wealth_buy_alert' AND column_name = 'created_at' AND data_type = 'text'
                    ) THEN
                        ALTER TABLE wealth_buy_alert ALTER COLUMN created_at DROP DEFAULT;
                        ALTER TABLE wealth_buy_alert ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::timestamptz;
                        ALTER TABLE wealth_buy_alert ALTER COLUMN created_at SET DEFAULT NOW();
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'wealth_buy_alert' AND column_name = 'status_updated_at' AND data_type = 'text'
                    ) THEN
                        ALTER TABLE wealth_buy_alert ALTER COLUMN status_updated_at DROP DEFAULT;
                        ALTER TABLE wealth_buy_alert ALTER COLUMN status_updated_at TYPE TIMESTAMPTZ USING status_updated_at::timestamptz;
                        ALTER TABLE wealth_buy_alert ALTER COLUMN status_updated_at SET DEFAULT NOW();
                    END IF;
                END $$;
                """)
                cur.execute("ALTER TABLE wealth_buy_alert ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")

                # Clean up breakout_type and add constraint safely
                cur.execute("DROP INDEX IF EXISTS unique_wealth_alert")
                cur.execute("UPDATE wealth_buy_alert SET breakout_type = '' WHERE breakout_type IS NULL")
                cur.execute("ALTER TABLE wealth_buy_alert ALTER COLUMN breakout_type SET DEFAULT ''")
                cur.execute("ALTER TABLE wealth_buy_alert ALTER COLUMN breakout_type SET NOT NULL")
                cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_wealth_symbol_date_type'
                    ) THEN
                        ALTER TABLE wealth_buy_alert ADD CONSTRAINT uq_wealth_symbol_date_type UNIQUE (symbol, alert_date, breakout_type);
                    END IF;
                END $$;
                """)

                # ── Users & Sessions Tables ──
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id SERIAL PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        email VARCHAR(255) UNIQUE,
                        first_name VARCHAR(100),
                        last_name VARCHAR(100),
                        mobile VARCHAR(20) UNIQUE,
                        password_hash VARCHAR(255),
                        role TEXT DEFAULT 'user',
                        is_active BOOLEAN DEFAULT FALSE,
                        must_change_password BOOLEAN DEFAULT FALSE,
                        failed_login_attempts INT DEFAULT 0,
                        locked_until TIMESTAMP WITH TIME ZONE,
                        last_login TIMESTAMP WITH TIME ZONE,
                        session_token UUID,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT)
                    )
                """)
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'")
                
                # Auth fields migration
                cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'name') THEN
                        ALTER TABLE users RENAME COLUMN name TO username;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'password_hash') THEN
                        ALTER TABLE users ADD COLUMN email VARCHAR(255) UNIQUE;
                        ALTER TABLE users ADD COLUMN first_name VARCHAR(100);
                        ALTER TABLE users ADD COLUMN last_name VARCHAR(100);
                        ALTER TABLE users ADD COLUMN mobile VARCHAR(20) UNIQUE;
                        ALTER TABLE users ADD COLUMN password_hash VARCHAR(255);
                        ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT FALSE;
                        ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE;
                        ALTER TABLE users ADD COLUMN failed_login_attempts INT DEFAULT 0;
                        ALTER TABLE users ADD COLUMN locked_until TIMESTAMP WITH TIME ZONE;
                        ALTER TABLE users ADD COLUMN last_login TIMESTAMP WITH TIME ZONE;
                        ALTER TABLE users ADD COLUMN session_token UUID;
                    END IF;
                END $$;
                """)
                
                # Clean up existing rows
                cur.execute("UPDATE users SET email = username || '@elitebreakout.temp' WHERE email IS NULL")
                cur.execute("ALTER TABLE users ALTER COLUMN email SET NOT NULL")
                cur.execute("UPDATE users SET password_hash = 'PLACEHOLDER' WHERE password_hash IS NULL")
                cur.execute("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                        ip_address TEXT,
                        login_time TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        logoff_time TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        is_online BOOLEAN DEFAULT TRUE
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_is_online ON user_sessions(is_online)")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_messages (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                        is_from_admin BOOLEAN NOT NULL DEFAULT FALSE,
                        message TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Kolkata')::TEXT),
                        is_read BOOLEAN DEFAULT FALSE
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_user_messages_user_id ON user_messages(user_id)")

                # ── Capital History (Track Base Capital and Deposits) ─────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS capital_history (
                        id SERIAL PRIMARY KEY,
                        transaction_type TEXT NOT NULL,
                        amount REAL NOT NULL,
                        description TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                # ── V5 MIGRATIONS (Timestamps, Dedup, Status Enums) ──────────────
                # Commit the above table creations before doing heavy DDL
                conn.commit()
                
                try:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    orig_autocommit = getattr(conn, 'autocommit', False)
                    conn.autocommit = True
                    try:
                        with conn.cursor() as mcur:
                            mcur.execute("""
-- 0. Drop dependent views before altering columns
DROP VIEW IF EXISTS v_trade_analytics CASCADE;

-- 1. Clean invalid timestamps and convert to TIMESTAMPTZ
-- Create robust safe_cast_timestamptz overloads for text, timestamp, and timestamptz
CREATE OR REPLACE FUNCTION safe_cast_timestamptz(p_val text) RETURNS timestamptz AS $func$
BEGIN
    RETURN p_val::timestamptz;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$func$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION safe_cast_timestamptz(p_val timestamp) RETURNS timestamptz AS $func2$
BEGIN
    -- Treat timestamp (without timezone) as UTC to avoid ambiguous conversions; adjust if needed.
    RETURN p_val AT TIME ZONE 'UTC';
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$func2$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION safe_cast_timestamptz(p_val timestamptz) RETURNS timestamptz AS $func3$
BEGIN
    RETURN p_val;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$func3$ LANGUAGE plpgsql IMMUTABLE;

-- Perform conversions using explicit ::text casts to ensure the text overload is used where values were stored as text
ALTER TABLE alerts ALTER COLUMN closed_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(closed_at::text);
ALTER TABLE alerts ALTER COLUMN created_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(created_at::text);
ALTER TABLE alerts ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(updated_at::text);

ALTER TABLE score_weight_log ALTER COLUMN created_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(created_at::text);

ALTER TABLE bayesian_model_updates ALTER COLUMN approved_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(approved_at::text);
ALTER TABLE bayesian_model_updates ALTER COLUMN rejected_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(rejected_at::text);
ALTER TABLE bayesian_model_updates ALTER COLUMN applied_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(applied_at::text);
ALTER TABLE bayesian_model_updates ALTER COLUMN created_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(created_at::text);
ALTER TABLE bayesian_model_updates ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(expires_at::text);

ALTER TABLE scanner_health ALTER COLUMN last_success TYPE TIMESTAMPTZ USING safe_cast_timestamptz(last_success::text);
ALTER TABLE scanner_health ALTER COLUMN first_error_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(first_error_at::text);
ALTER TABLE scanner_health ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(updated_at::text);

ALTER TABLE telegram_queue ALTER COLUMN created_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(created_at::text);
ALTER TABLE telegram_queue ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(sent_at::text);

ALTER TABLE data_fetch_health ALTER COLUMN last_success TYPE TIMESTAMPTZ USING safe_cast_timestamptz(last_success::text);
ALTER TABLE data_fetch_health ALTER COLUMN last_failure TYPE TIMESTAMPTZ USING safe_cast_timestamptz(last_failure::text);
ALTER TABLE data_fetch_health ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING safe_cast_timestamptz(updated_at::text);

ALTER TABLE fetch_errors ALTER COLUMN first_seen TYPE TIMESTAMPTZ USING safe_cast_timestamptz(first_seen::text);
ALTER TABLE fetch_errors ALTER COLUMN last_seen TYPE TIMESTAMPTZ USING safe_cast_timestamptz(last_seen::text);

-- 2. Convert alert_date to DATE with safe overloads
CREATE OR REPLACE FUNCTION safe_cast_date(p_val text) RETURNS date AS $fd$
BEGIN
    RETURN p_val::date;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$fd$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION safe_cast_date(p_val date) RETURNS date AS $fd2$
BEGIN
    RETURN p_val;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$fd2$ LANGUAGE plpgsql IMMUTABLE;

ALTER TABLE alerts ALTER COLUMN alert_date TYPE DATE USING safe_cast_date(alert_date::text);
ALTER TABLE alerts ALTER COLUMN alert_date SET DEFAULT CURRENT_DATE;

-- 3. Add deduplication constraint including scanner
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_symbol_breakout_type_alert_date_key;
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_dedup_idx;
ALTER TABLE alerts ADD CONSTRAINT alerts_dedup_idx UNIQUE (symbol, breakout_type, scanner, alert_date);

-- 4. Add status CHECK constraints (NOT VALID to avoid full-table validation during deploy)
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS chk_alerts_status;
ALTER TABLE alerts ADD CONSTRAINT chk_alerts_status CHECK (status IN ('OPEN', 'WIN', 'LOSS', 'CLOSED')) NOT VALID;
ALTER TABLE scanner_health DROP CONSTRAINT IF EXISTS chk_scanner_status;
ALTER TABLE scanner_health ADD CONSTRAINT chk_scanner_status CHECK (status IN ('OK', 'DOWN', 'IDLE')) NOT VALID;
ALTER TABLE telegram_queue DROP CONSTRAINT IF EXISTS chk_tg_status;
ALTER TABLE telegram_queue ADD CONSTRAINT chk_tg_status CHECK (status IN ('pending', 'sent')) NOT VALID;
ALTER TABLE bayesian_model_updates DROP CONSTRAINT IF EXISTS chk_bayes_status;
ALTER TABLE bayesian_model_updates ADD CONSTRAINT chk_bayes_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')) NOT VALID;
""")
                    finally:
                        try:
                            conn.autocommit = orig_autocommit
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Failed to run V5 migrations: {e}")
                # outer connection will commit below

                conn.commit()

        _DB_INITIALIZED = True
        logger.info("✅ Database ready (Postgres) — all columns ensured")
        logger.info("ℹ️  Data Retention Active: preserving all alerts for historical analysis.")

        bootstrap_admin()

# =====================================================================================
# FAILED-REVERSAL COOLDOWN (v6.1)
#
# Makes the reversal scanner's cooldown REAL by reading the EXISTING `status` column
# (populated by performance_tracker via update_alert_outcome). No new table/job needed.
#
# A symbol is "in cooldown" if its most recent REVERSAL alert closed as a LOSS within
# the last `cooldown_days` trading days. This suppresses repeated low-quality reversal
# candidates — the #1 leak identified in the 44% backtest.
#
# Trading days are approximated via business-day count (Mon–Fri) using alert_date.
# =====================================================================================

def is_symbol_in_failed_reversal_cooldown(symbol: str, cooldown_days: int = 30) -> bool:
    """
    PREFERRED cooldown backend for reversal_scanner (logs 🟢 OUTCOME_AWARE when present).

    Returns True if `symbol`'s MOST RECENT REVERSAL alert:
        • closed as status='LOSS', AND
        • that alert fired within the last `cooldown_days` business days.
    Returns False if the last reversal won, is still OPEN, or no recent reversal exists.

    Relies on the existing `status` column written by performance_tracker /
    update_alert_outcome(). No separate outcome table required.
    """
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Most recent REVERSAL alert for this symbol (any status)
                cur.execute("""
                    SELECT status, alert_date
                    FROM alerts
                    WHERE symbol = %s AND scanner = 'REVERSAL'
                    ORDER BY alert_date DESC, alert_time DESC
                    LIMIT 1
                """, (symbol,))
                row = cur.fetchone()
                if not row:
                    return False

                status, alert_date = row[0], row[1]

                # Only LOSS triggers cooldown. WIN / OPEN / CLOSED do not suppress.
                if str(status).upper() != "LOSS":
                    return False

                # Business-day distance from the losing alert's date to today.
                try:
                    # Prefer numpy for performance if available
                    import numpy as np
                    from datetime import date as _date
                    # alert_date is a DATE column → psycopg2 returns datetime.date
                    if not isinstance(alert_date, _date):
                        # Fallback if it came back as text
                        from datetime import datetime as _dt
                        alert_date = _dt.strptime(str(alert_date)[:10], "%Y-%m-%d").date()
                    today = datetime.now(IST).date()
                    if today < alert_date:
                        return False
                    try:
                        biz_days = int(np.busday_count(alert_date, today))
                        return biz_days < cooldown_days
                    except Exception:
                        # If numpy present but busday_count failed, fall through to pure-Python calc
                        logger.warning(f"numpy.busday_count failed for {symbol}, falling back")
                except ImportError:
                    # numpy not available — fallback to pure-Python business-day calc
                    pass

                # Pure-Python business-day count (no external deps)
                try:
                    from datetime import timedelta
                    today = datetime.now(IST).date()
                    if today < alert_date:
                        return False
                    delta_days = (today - alert_date).days
                    weeks, remainder = divmod(delta_days, 7)
                    biz_days = weeks * 5
                    start_weekday = alert_date.weekday()  # 0=Mon,6=Sun
                    for i in range(remainder):
                        if (start_weekday + i) % 7 < 5:
                            biz_days += 1
                    return biz_days < cooldown_days
                except Exception:
                    logger.exception(f"cooldown business-day calc failed for {symbol}")
                    # Conservative: if we can't compute distance, do NOT suppress.
                    return False
    except Exception:
        logger.exception(f"❌ is_symbol_in_failed_reversal_cooldown failed for {symbol}")
        return False


# ── Public API ────────────────────────────────────────────────────────────────────────

def save_alert_if_new(
    symbol: str,
    breakout_type: str,
    alert_time: str,
    scanner: str = None,
    category: str = None,
    entry_price: float = None,
    stop_loss: float = None,
    target_price: float = None,
    signals: str = None,
    score: int = None,
    rsi: float = None,
    volume_ratio: float = None,
    context: dict = None,
    model_version: str = "v1",
    data_partition: str = "TRAIN",
    bayesian_regime: str = "BULL",
    bayesian_weights: dict = None,
    cash_in_hand: float = None,
    **kwargs
) -> tuple[bool, float, int]:
    """
    Insert a new alert.  Returns (inserted, capital_allocated, shares_bought).
    
    Captures:
    - model_version: Bayesian model version (v1, v2, etc)
    - bayesian_regime: Market regime (BULL, BEAR, SIDEWAYS)
    - bayesian_weights: Actual weights used for scoring
    """
    context_str = json.dumps(context) if context is not None else None
    weights_str = json.dumps(bayesian_weights) if bayesian_weights is not None else None

    # Safety: Never persist a BUY-style alert if the input/context indicates stale or fallback data.
    # Many scanners pass `used_fallback_data`, `data_quality` or `alert_details` in `context` or **kwargs.
    # If any of these flags indicate cached/stale data, suppress the insert and return as not-inserted.
    try:
        def _is_stale_buy() -> bool:
            # Normalize context to dict if possible
            ctx = context if isinstance(context, dict) else {}
            # If context was passed as JSON string in some callers, try to decode it
            if isinstance(context, str):
                try:
                    ctx = json.loads(context)
                except Exception:
                    ctx = {}

            # Check common stale indicators
            stale_indicators = ("CACHED_PREV_DAY", "CACHED_MULTI_DAY", "MISSING_PARTIAL")
            if isinstance(ctx, dict):
                if bool(ctx.get("used_fallback_data")):
                    return True
                if str(ctx.get("data_quality", "")).upper() in stale_indicators:
                    return True

            # Inspect kwargs for similar indicators (some callers pass them there)
            if bool(kwargs.get("used_fallback_data", False)):
                return True
            if str(kwargs.get("data_quality", "")).upper() in stale_indicators:
                return True

            # Some scanners attach a richer alert_details / alert_context containing timestamps or flags
            alert_details = kwargs.get("alert_details") or kwargs.get("alert_context") or kwargs.get("context")
            if isinstance(alert_details, dict) and bool(alert_details.get("used_fallback_data", False)):
                return True

            # Conservative default: not stale
            return False

        if _is_stale_buy():
            logger.warning(f"🛡️ save_alert_if_new: Suppressing persistence for {symbol} due to stale/fallback data in context")
            return False, 0.0, 0
    except Exception:
        # If the check fails for any reason, prefer to continue and allow the insert (fail-open)
        logger.exception("⚠️ save_alert_if_new: stale-data guard check failed unexpectedly — allowing insert")
    
    # Calculate portfolio allocation dynamically if not provided
    from portfolio_engine import calculate_trade_allocation
    capital_allocated = kwargs.get('capital_allocated')
    shares_bought = kwargs.get('shares_bought')
    
    if capital_allocated is None or shares_bought is None:
        if entry_price and stop_loss:
            capital_allocated, shares_bought = calculate_trade_allocation(entry_price, stop_loss, score or 80)
        else:
            capital_allocated, shares_bought = 0.0, 0
            
    # Dry-run mode: if enabled, do not persist alerts.
    if DONT_SAVE_ALERTS:
        logger.info(f"🧪 DONT_SAVE_ALERTS enabled — not saving alert for {symbol} ({breakout_type})")
        return False, 0.0, 0

    with _DB_WRITE_LOCK:
        with get_connection() as conn:
            success = False
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO alerts
                            (symbol, breakout_type, alert_time, scanner, category,
                             entry_price, stop_loss, target_price, signals, score,
                             rsi, volume_ratio, status, context, capital_allocated, shares_bought,
                             model_version, bayesian_regime, bayesian_weights, data_partition, cash_in_hand)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, breakout_type, scanner, alert_date) DO NOTHING
                    """, (symbol, breakout_type, alert_time, scanner, category,
                          entry_price, stop_loss, target_price, signals, score,
                          rsi, volume_ratio, context_str, capital_allocated, shares_bought,
                          model_version, bayesian_regime, weights_str, data_partition, cash_in_hand or 0.0))
                    conn.commit()
                    success = True
                    inserted = cur.rowcount > 0
                    if inserted:
                        msg = f'{symbol} | {category} | Buy: ₹{entry_price} | SL: ₹{stop_loss} | TGT: ₹{target_price}'
                        insert_notification('buy', f'Buy Alert / {scanner}', msg, symbol)
                    return inserted, capital_allocated, shares_bought
            except Exception:
                logger.exception(f"❌ save_alert_if_new failed for {symbol}")
                return False, 0.0, 0
            finally:
                if not success:
                    conn.rollback()


def update_alert_outcome(
    alert_id: int,
    status: str,          # "WIN" | "LOSS"
    exit_price: float,
    pnl_pct: float,
    pnl_rs: float = None,
    closed_at: Optional[str] = None,
) -> None:
    """
    Lock in the final outcome of a trade once SL or Target is hit.
    Called by performance_tracker — writes back so future runs skip bar downloads
    for already-closed positions.
    """
    if closed_at is None:
        closed_at = datetime.now(IST).isoformat()
    with _DB_WRITE_LOCK:
        with get_connection() as conn:
            success = False
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE alerts
                        SET status     = %s,
                            exit_price = %s,
                            pnl_pct    = %s,
                            pnl_rs     = %s,
                            closed_at  = %s
                        WHERE id = %s
                          AND status = 'OPEN'   -- never overwrite an already-closed row
                    """, (status, exit_price, pnl_pct, pnl_rs, closed_at, alert_id))
                    conn.commit()
                    success = True
                    if cur.rowcount:
                        logger.info(f"🔒 Alert {alert_id} locked as {status} | exit={exit_price} pnl={pnl_pct}%")
                        # Fetch symbol to send notification
                        cur.execute("SELECT symbol FROM alerts WHERE id = %s", (alert_id,))
                        row = cur.fetchone()
                        if row:
                            sym = row[0]
                            p_str = f"₹{pnl_rs:.2f}" if pnl_rs is not None else f"{pnl_pct:.2f}%"
                            msg = f"{sym} | Exit: ₹{exit_price:.2f} | P&L: {p_str}"
                            insert_notification('sell', f'Exit Alert ({status})', msg, sym)
            except Exception:
                logger.exception(f"❌ update_alert_outcome failed for alert_id={alert_id}")
            finally:
                if not success:
                    conn.rollback()

def check_recent_alert(symbol: str, scanner: str, breakout_type: str, lookback_minutes: int) -> bool:
    """Returns True if a duplicate alert exists within the cooldown window."""
    from datetime import datetime, timedelta
    cutoff = datetime.now(IST) - timedelta(minutes=lookback_minutes)
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM alerts
                WHERE symbol = %s
                AND scanner = %s
                AND breakout_type = %s
                AND alert_time > %s
                LIMIT 1
            """, (symbol, scanner, breakout_type, cutoff))
            return cur.fetchone() is not None

def get_all_alerts() -> list[dict]:
    """Return every alert, newest first — including outcome columns.

    Calls init_db() first to ensure all migration columns exist regardless
    of whether a scanner has started yet (performance tracker runs independently).
    """
    init_db()   # no-op if already initialised; ensures columns exist before SELECT
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    id, symbol, breakout_type, alert_time, alert_date,
                    scanner, category, entry_price, stop_loss, target_price,
                    signals, score, rsi, volume_ratio,
                    status, exit_price, pnl_pct, closed_at, is_rejected,
                    capital_allocated, shares_bought, pnl_rs, context,
                    model_version, data_partition
                FROM alerts
                ORDER BY alert_time DESC
            """)
            rows = []
            for row in cur.fetchall():
                d = dict(row)
                if d.get("is_rejected"):
                    d["status"] = "REJECTED"
                rows.append(d)
            return rows


# ── Scanner Health API ────────────────────────────────────────────────────────────────

def classify_error_severity(error_msg: str) -> str:
    """
    Classify an error as CRITICAL or IGNORABLE.
    
    CRITICAL: Code failures, missing config files, compilation errors
    IGNORABLE: API failures for individual/multiple stocks - scanner rejects them and continues
    
    Returns: 'CRITICAL' | 'IGNORABLE'
    
    Key principle: If scanner can handle it by rejecting/skipping the stock and continuing,
    it's IGNORABLE (keeps scanner GREEN). If scanner crashes entirely, it's CRITICAL.
    
    Example: BAJAJ AUTO yfinance timeout
      → Stock rejected, scan continues with 49 other stocks
      → Scanner shows GREEN with alerts from successful stocks
      → Not critical because scanner completed successfully
    """
    if not error_msg:
        return None
    
    error_lower = error_msg.lower()
    
    # IGNORABLE patterns: missing stock data, API timeouts for specific/all stocks
    # Scanner handles these gracefully by rejecting the stock(s) and continuing
    ignorable_patterns = [
        'yfinance',
        'timeout',
        'connection refused',
        'no data found',
        'stock not found',
        'not available',
        'api rate limit',
        'temporarily unavailable',
        'data not available',
        'failed to get data for',
        'returned 0 data',  # Stock(s) rejected, others continue
    ]
    
    # CRITICAL patterns: code/infrastructure issues that crash the scanner
    critical_patterns = [
        'critical',
        'syntax error',
        'import error',
        'indentation error',
        'nameerror',
        'typeerror',
        'attributeerror',
        'keyerror',
        'file not found',
        'no such file',
        'cannot open',
        'permission denied',
        'assert',
        'index error',
        'value error',
        'runtime error',
        'null pointer',
        'undefined',
        'not defined',
        'could not import',
    ]
    
    # Check for critical patterns first
    for pattern in critical_patterns:
        if pattern in error_lower:
            return 'CRITICAL'
    
    # Check for ignorable patterns
    for pattern in ignorable_patterns:
        if pattern in error_lower:
            return 'IGNORABLE'
    
    # Default to CRITICAL for unknown errors (safety first)
    return 'CRITICAL'


def upsert_scanner_health(
    scanner_name: str,
    status: str = None,           # "OK" | "DOWN" | "IDLE" | None (keep existing)
    last_success: str = None,     # ISO timestamp of last successful scan
    today_alerts: int = None,     # number of alerts fired today (None = keep existing)
    error_msg: str = None,        # error message when status=DOWN, else None
    scheduled_for: str = None,    # When this scanner is scheduled to run (e.g., "01:00 IST")
) -> None:
    """
    Insert or update a scanner's health record in the scanner_health table.
    
    Auto-recovery logic:
      • When status='OK': Auto-clear error fields + set is_acknowledged=TRUE (recovery)
      • When status='DOWN': Classify error severity + set is_acknowledged=FALSE
      • When status='DOWN' with IGNORABLE error: Still set DOWN but error_severity=IGNORABLE
    """
    init_db()
    now_str = datetime.now(IST).isoformat()

    # Normalize and sanitize status values to match DB CHECK constraint
    if status is not None:
        status = str(status).upper()
    allowed_statuses = {'OK', 'DOWN', 'IDLE'}
    if status is not None and status not in allowed_statuses:
        logger.warning(f"upsert_scanner_health: unknown status '{status}' provided — mapping to 'IDLE'")
        status = 'IDLE'

    error_severity = None
    is_ack = None

    # Classify error severity and set acknowledgement status
    if status == 'DOWN' and error_msg:
        error_severity = classify_error_severity(error_msg)
        is_ack = False  # NEW ERROR: mark unacknowledged
    elif status == 'OK':
        # AUTO-RECOVERY: Clear errors and mark as acknowledged
        error_msg = None
        error_severity = None
        is_ack = True

    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Build the update/insert query
                set_clauses = []
                params = []
                
                if status is not None:
                    set_clauses.append("status = %s")
                    params.append(status)
                if last_success is not None:
                    set_clauses.append("last_success = %s")
                    params.append(last_success)
                if today_alerts is not None:
                    set_clauses.append("today_alerts = %s")
                    params.append(today_alerts)
                if error_msg is not None:
                    set_clauses.append("error_msg = %s")
                    params.append(error_msg)
                elif error_msg is None and status == 'OK':
                    # Explicitly clear error_msg on recovery
                    set_clauses.append("error_msg = NULL")
                if error_severity is not None:
                    set_clauses.append("error_severity = %s")
                    params.append(error_severity)
                elif status == 'OK':
                    # Clear error_severity on recovery
                    set_clauses.append("error_severity = NULL")
                if is_ack is not None:
                    set_clauses.append("is_acknowledged = %s")
                    params.append(is_ack)
                if scheduled_for is not None:
                    set_clauses.append("scheduled_for = %s")
                    params.append(scheduled_for)
                
                set_clauses.append("updated_at = %s")
                params.append(now_str)
                
                # Always include scanner_name for conflict/insert
                params.insert(0, scanner_name)
                if status is None:
                    status = 'IDLE'
                params.insert(1, status)
                params.insert(2, now_str)
                
                set_sql = ", ".join(set_clauses)
                cur.execute(f"""
                    INSERT INTO scanner_health
                        (scanner_name, status, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (scanner_name) DO UPDATE
                        SET {set_sql}
                """, params)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ upsert_scanner_health failed for {scanner_name}")


def get_all_scanner_health() -> list[dict]:
    """Return all scanner health rows from the scanner_health table."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT scanner_name, status, last_success, today_alerts, error_msg, is_acknowledged, updated_at, error_severity, error_count, first_error_at, retry_count, scheduled_for
                    FROM scanner_health
                    ORDER BY scanner_name
                """)
                return [dict(row) for row in cur.fetchall()]
            except Exception:
                logger.exception("❌ get_all_scanner_health failed")
                return []


def get_scanner_today_trades(scanner_name: str, today_str: str) -> list[dict]:
    """
    Return today's alerts for a specific scanner — used by the dashboard API
    to build hover/drill-down trade list directly from the DB.
    """
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT
                        symbol, category, signals, entry_price, alert_time,
                        stop_loss, target_price, pnl_pct, status, score,
                        exit_price, closed_at
                    FROM alerts
                    WHERE scanner    = %s
                      AND alert_date = %s
                    ORDER BY alert_time DESC
                """, (scanner_name, today_str))
                return [dict(row) for row in cur.fetchall()]
            except Exception:
                logger.exception(f"❌ get_scanner_today_trades failed for {scanner_name}")
                return []

    logger.debug("🗑️  cleanup_old_alerts called — deletion disabled, all data retained.")


def get_todays_alerts(today_str: str) -> list[dict]:
    """Return all alerts for the provided alert_date (YYYY-MM-DD)."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT id, symbol, breakout_type, alert_time, scanner, category, entry_price,
                           stop_loss, target_price, signals, score, status, seen_by_user, seen_by_admin
                    FROM alerts
                    WHERE alert_date = %s
                    ORDER BY alert_time DESC
                """, (today_str,))
                return [dict(row) for row in cur.fetchall()]
            except Exception:
                logger.exception("❌ get_todays_alerts failed")
                return []


def mark_alert_seen(alert_id: int, role: str = "user") -> bool:
    """Mark an alert as seen by 'user' or 'admin'. Returns True if updated."""
    init_db()
    # Validate column name to prevent SQL injection
    allowed_cols = {'user': 'seen_by_user', 'admin': 'seen_by_admin'}
    col = allowed_cols.get(role)
    if not col:
        logger.warning(f"Invalid role '{role}' for mark_alert_seen")
        return False
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Use parameterized query with validated column name
                cur.execute(f"UPDATE alerts SET {col} = TRUE WHERE id = %s", (alert_id,))
                conn.commit()
                return cur.rowcount > 0
            except Exception:
                conn.rollback()
                logger.exception(f"❌ mark_alert_seen failed for id={alert_id}")
                return False


def save_system_state(key: str, value_str: str) -> None:
    """Save/update a string value (like JSON payload) for a specific key."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO system_state (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value
                """, (key, value_str))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ save_system_state failed for key={key}")


def get_system_state(key: str) -> Optional[str]:
    """Retrieve system state value for a specific key."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT value FROM system_state WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else None
            except Exception:
                logger.exception(f"❌ get_system_state failed for key={key}")
                return None

# ── AI CONCALL CACHE ────────────────────────────────────────────────────────
def get_cached_concall_analysis(symbol: str, pdf_url: str):
    """Retrieves cached AI analysis for a specific PDF url."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT analysis_data
                FROM ai_concall_cache_v3
                WHERE symbol = %s AND pdf_url = %s
            """, (symbol, pdf_url))
            row = cur.fetchone()
            if row:
                return row[0]
            return None

def get_ai_cache_count() -> int:
    """Returns the total number of cached AI analyses."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT symbol) FROM ai_concall_cache_v3")
                row = cur.fetchone()
                if row:
                    return int(row[0])
    except Exception:
        pass
    return 0


def get_total_cached_concalls() -> int:
    """Returns the total number of distinct stocks that have cached concall data."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT COUNT(DISTINCT symbol) FROM ai_concall_cache_v3")
                row = cur.fetchone()
                return row[0] if row else 0
            except Exception as e:
                logger.error(f"Error getting total cached concalls: {e}")
                return 0


def get_ai_concall_stats() -> dict:
    """Return stats for AI concall cache: total distinct symbols, last processed symbol and timestamp."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT COUNT(DISTINCT symbol) FROM ai_concall_cache_v3")
                total_row = cur.fetchone()
                total = total_row[0] if total_row else 0
                cur.execute("SELECT symbol, created_at FROM ai_concall_cache_v3 ORDER BY created_at DESC LIMIT 1")
                last = cur.fetchone()
                if last:
                    return {"total_cached": int(total), "last_symbol": last[0], "last_updated": last[1]}
                return {"total_cached": int(total), "last_symbol": None, "last_updated": None}
            except Exception as e:
                logger.error(f"Error getting ai concall stats: {e}")
                return {"total_cached": 0, "last_symbol": None, "last_updated": None}


def get_promoter_pledge_stats() -> dict:
    """Return stats for promoter_pledge_cache: total symbols cached, last processed symbol and timestamp."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT COUNT(*) FROM promoter_pledge_cache")
                total_row = cur.fetchone()
                total = total_row[0] if total_row else 0
                cur.execute("SELECT symbol, updated_at FROM promoter_pledge_cache ORDER BY updated_at DESC LIMIT 1")
                last = cur.fetchone()
                if last:
                    return {"total_cached": int(total), "last_symbol": last[0], "last_updated": last[1]}
                return {"total_cached": int(total), "last_symbol": None, "last_updated": None}
            except Exception as e:
                logger.error(f"Error getting pledge stats: {e}")
                return {"total_cached": 0, "last_symbol": None, "last_updated": None}

def get_recent_concall_analysis(symbol: str, max_age_days: int = 60):
    """Retrieves cached AI analysis for a symbol if it is less than max_age_days old."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT analysis_data
                FROM ai_concall_cache_v3
                WHERE symbol = %s AND created_at::TIMESTAMP WITH TIME ZONE >= NOW() - INTERVAL '1 day' * %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (symbol, max_age_days))
            row = cur.fetchone()
            if row:
                return row[0]
            return None

def save_concall_analysis(symbol: str, pdf_url: str, analysis_data: dict):
    """Saves AI analysis to the cache for a specific PDF url."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                import json
                cur.execute("""
                    INSERT INTO ai_concall_cache_v3 (symbol, pdf_url, analysis_data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (pdf_url) DO UPDATE
                    SET analysis_data = EXCLUDED.analysis_data,
                        created_at = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT
                """, (symbol, pdf_url, json.dumps(analysis_data)))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save concall cache for {symbol}: {e}")


def get_cache_metadata(key: str):
    """Return metadata for a cache key from data_cache_metadata or None if missing."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("SELECT key, last_fetched, cadence_seconds, rows, etag, source, updated_at FROM data_cache_metadata WHERE key = %s", (key,))
                row = cur.fetchone()
                return dict(row) if row else None
            except Exception:
                logger.exception(f"❌ get_cache_metadata failed for key={key}")
                return None


def get_latest_weights(regime: str) -> dict:
    """Get the latest JSON weights for a given regime."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT model_version, weights 
                    FROM score_weight_log 
                    WHERE regime = %s 
                    ORDER BY id DESC LIMIT 1
                """, (regime,))
                row = cur.fetchone()
                if row:
                    return {"version": row[0], "weights": row[1]}
                return None
            except Exception:
                logger.exception(f"❌ get_latest_weights failed for regime={regime}")
                return None

def save_new_weights(model_version: str, regime: str, weights: dict):
    """Save a new version of weights for a given regime."""
    init_db()
    import json
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO score_weight_log (model_version, regime, weights)
                    VALUES (%s, %s, %s)
                """, (model_version, regime, json.dumps(weights)))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ save_new_weights failed for regime={regime}")


def upsert_cache_metadata(key: str, last_fetched: str, cadence_seconds: int, rows: int = None, etag: str = None, source: str = None):
    """Insert or update cache metadata for a given key."""
    init_db()
    now = datetime.now(IST).isoformat()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO data_cache_metadata (key, last_fetched, cadence_seconds, rows, etag, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE
                        SET last_fetched = EXCLUDED.last_fetched,
                            cadence_seconds = EXCLUDED.cadence_seconds,
                            rows = COALESCE(EXCLUDED.rows, data_cache_metadata.rows),
                            etag = COALESCE(EXCLUDED.etag, data_cache_metadata.etag),
                            source = COALESCE(EXCLUDED.source, data_cache_metadata.source),
                            updated_at = EXCLUDED.updated_at
                """, (key, last_fetched, cadence_seconds, rows, etag, source, now))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ upsert_cache_metadata failed for key={key}")


def upsert_data_fetch_health(source_name: str, last_success: str = None, last_failure: str = None, consecutive_failures: int = None, error_msg: str = None):
    """Insert/update health row for an external data provider (yfinance, nse, etc.)."""
    init_db()
    now = datetime.now(IST).isoformat()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # If consecutive_failures is None, don't overwrite the existing value.
                if consecutive_failures == 0:
                    # Success for API: Reset consecutive failures, but keep is_acknowledged as-is (requires admin dismissal)
                    cur.execute("""
                        INSERT INTO data_fetch_health (source_name, last_success, consecutive_failures, is_acknowledged, updated_at)
                        VALUES (%s, %s, 0, TRUE, %s)
                        ON CONFLICT (source_name) DO UPDATE
                            SET last_success = COALESCE(EXCLUDED.last_success, data_fetch_health.last_success),
                                consecutive_failures = 0,
                                updated_at = EXCLUDED.updated_at
                    """, (source_name, last_success, now))
                elif consecutive_failures is not None:
                    # Specific consecutive_failures provided (uncommon pathway)
                    cur.execute("""
                        INSERT INTO data_fetch_health (source_name, last_success, last_failure, consecutive_failures, error_msg, is_acknowledged, updated_at)
                        VALUES (%s, %s, %s, %s, %s, FALSE, %s)
                        ON CONFLICT (source_name) DO UPDATE
                            SET last_success = COALESCE(EXCLUDED.last_success, data_fetch_health.last_success),
                                last_failure = COALESCE(EXCLUDED.last_failure, data_fetch_health.last_failure),
                                consecutive_failures = EXCLUDED.consecutive_failures,
                                error_msg = COALESCE(EXCLUDED.error_msg, data_fetch_health.error_msg),
                                is_acknowledged = FALSE,
                                updated_at = EXCLUDED.updated_at
                    """, (source_name, last_success, last_failure, consecutive_failures, error_msg, now))
                else:
                    # Standard failure reporting
                    cur.execute("""
                        INSERT INTO data_fetch_health 
                          (source_name, last_success, last_failure, consecutive_failures, error_msg, is_acknowledged, updated_at)
                        VALUES (%s, %s, %s, 1, %s, FALSE, %s)
                        ON CONFLICT (source_name) DO UPDATE
                          SET last_failure = COALESCE(EXCLUDED.last_failure, data_fetch_health.last_failure),
                              consecutive_failures = COALESCE(data_fetch_health.consecutive_failures, 0) + 1,
                              is_acknowledged = CASE WHEN EXCLUDED.error_msg IS DISTINCT FROM data_fetch_health.error_msg THEN FALSE ELSE data_fetch_health.is_acknowledged END,
                              error_msg = COALESCE(EXCLUDED.error_msg, data_fetch_health.error_msg),
                              updated_at = EXCLUDED.updated_at
                    """, (source_name, last_success, last_failure, error_msg, now))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ upsert_data_fetch_health failed for {source_name}")

def acknowledge_data_fetch_health(source_name: str):
    """Admin acknowledgment to clear persistent UI warnings.

    Also clear corresponding scanner_health rows (External:<source> and impacted scanners)
    so the UI immediately reflects the dismissal.
    """
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    UPDATE data_fetch_health 
                    SET is_acknowledged = TRUE, error_msg = NULL, consecutive_failures = 0
                    WHERE source_name = %s
                """, (source_name,))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ acknowledge_data_fetch_health failed for {source_name}")
    # Also attempt to clear any scanner_health rows that were set due to this external source
    try:
        # Split base and scope if present
        base = source_name.split(':', 1)[0] if ':' in source_name else source_name
        scope = source_name.split(':', 1)[1] if ':' in source_name else None
        cleared = []
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Clear the generic External:<source_name> row (exact)
                cur.execute("UPDATE scanner_health SET is_acknowledged = TRUE, error_msg = NULL, status = 'OK' WHERE scanner_name = %s", (f'External:{source_name}',))
                if cur.rowcount:
                    cleared.append(f'External:{source_name}')
                # Clear the External:<base> row as well
                cur.execute("UPDATE scanner_health SET is_acknowledged = TRUE, error_msg = NULL, status = 'OK' WHERE scanner_name = %s", (f'External:{base}',))
                if cur.rowcount:
                    cleared.append(f'External:{base}')

                # Try to import mapping from data_fetch_status to know impacted scanners
                try:
                    from data_fetch_status import SOURCE_IMPACT_MAP, INTERVAL_TO_SCANNER
                    impacted = SOURCE_IMPACT_MAP.get(base, [])
                    targeted = []
                    if scope:
                        mapped = INTERVAL_TO_SCANNER.get(scope.lower()) if hasattr(INTERVAL_TO_SCANNER, 'get') else INTERVAL_TO_SCANNER.get(scope.lower())
                        if mapped:
                            targeted = [sc for sc in impacted if sc == mapped]
                        else:
                            targeted = [sc for sc in impacted if sc.upper() == scope.upper()]
                    else:
                        targeted = impacted
                    for sc in targeted:
                        cur.execute("UPDATE scanner_health SET is_acknowledged = TRUE, error_msg = NULL, status = 'OK' WHERE scanner_name = %s", (sc,))
                        if cur.rowcount:
                            cleared.append(sc)
                    conn.commit()
                except Exception:
                    # If we can't import the mapping, still attempt a best-effort clear of External:base
                    conn.rollback()
    except Exception:
        logger.exception(f"❌ Failed to clear scanner_health rows after acknowledging {source_name}")

def acknowledge_scanner_health(scanner_name: str):
    """Admin acknowledgment to clear persistent UI warnings for scanners."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    UPDATE scanner_health 
                    SET is_acknowledged = TRUE, error_msg = NULL, status = 'OK'
                    WHERE scanner_name = %s
                """, (scanner_name,))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ acknowledge_scanner_health failed for {scanner_name}")


def upsert_fetch_error(source_name: str, scanner_name: str, symbol: str, interval: str, category: str, error_msg: str = None):
    """Insert or update a fetch_errors aggregation row.

    If the combination (source, scanner, symbol, interval, category) exists, increment occurrences
    and update last_seen/last_error_msg. Otherwise create a new row with occurrences=1.
    """
    init_db()
    now = datetime.now(IST).isoformat()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO fetch_errors (source_name, scanner_name, symbol, interval, category, occurrences, first_seen, last_seen, last_error_msg, is_acknowledged)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, FALSE)
                    ON CONFLICT (source_name, scanner_name, symbol, interval, category) DO UPDATE
                      SET occurrences = fetch_errors.occurrences + 1,
                          last_seen = EXCLUDED.last_seen,
                          last_error_msg = COALESCE(EXCLUDED.last_error_msg, fetch_errors.last_error_msg),
                          is_acknowledged = FALSE
                """, (source_name, scanner_name, symbol, interval, category, now, now, error_msg))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(f"❌ upsert_fetch_error failed for {source_name}/{symbol}")


def get_all_fetch_errors(limit: int = 100) -> list:
    """Return all non-hidden fetch errors (excluding acknowledged with 0 occurrences).
    
    Hide errors where is_acknowledged=TRUE AND occurrences=0.
    """
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT id, source_name, scanner_name, symbol, interval, category, occurrences, first_seen, last_seen, last_error_msg, is_acknowledged
                    FROM fetch_errors
                    WHERE NOT (is_acknowledged = TRUE AND occurrences = 0)
                    ORDER BY occurrences DESC, last_seen DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
            except Exception:
                logger.exception("❌ get_all_fetch_errors failed")
                return []


def get_fetch_errors_for_scanner(scanner_name: str) -> list:
    """Return all non-acknowledged fetch_errors for a specific scanner.
    
    Hide errors where is_acknowledged=TRUE AND occurrences=0.
    """
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT id, source_name, scanner_name, symbol, interval, category, occurrences, first_seen, last_seen, last_error_msg, is_acknowledged
                    FROM fetch_errors
                    WHERE scanner_name = %s 
                      AND NOT (is_acknowledged = TRUE AND occurrences = 0)
                    ORDER BY occurrences DESC, last_seen DESC
                """, (scanner_name,))
                return [dict(r) for r in cur.fetchall()]
            except Exception:
                logger.exception(f"❌ get_fetch_errors_for_scanner failed for {scanner_name}")
                return []


def has_unacknowledged_errors(scanner_name: str) -> bool:
    """Check if a scanner has ANY unacknowledged fetch_errors."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT 1 FROM fetch_errors
                    WHERE scanner_name = %s AND is_acknowledged = FALSE
                    LIMIT 1
                """, (scanner_name,))
                return cur.fetchone() is not None
            except Exception:
                logger.exception(f"❌ has_unacknowledged_errors failed for {scanner_name}")
                return False


def acknowledge_fetch_error(error_id: int) -> bool:
    """Mark a fetch_errors row as acknowledged and reset counter to 0.
    
    When user clicks 'Ignore', this resets occurrences to 0 and sets is_acknowledged=TRUE.
    If error reoccurs, upsert_fetch_error will set occurrences=1 and is_acknowledged=FALSE.
    """
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Mark the fetch error as acknowledged AND reset counter to 0
                cur.execute("""
                    UPDATE fetch_errors 
                    SET is_acknowledged = TRUE, occurrences = 0
                    WHERE id = %s
                """, (error_id,))
                if cur.rowcount == 0:
                    return False
                
                # Get the scanner_name from this error
                cur.execute("SELECT scanner_name FROM fetch_errors WHERE id = %s", (error_id,))
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return True
                
                scanner_name = row[0]
                
                # Check if this scanner has ANY remaining unacknowledged errors
                cur.execute("""
                    SELECT 1 FROM fetch_errors
                    WHERE scanner_name = %s AND is_acknowledged = FALSE
                    LIMIT 1
                """, (scanner_name,))
                has_more_errors = cur.fetchone() is not None
                
                # If no more errors, clear the scanner_health record (turn green)
                if not has_more_errors:
                    cur.execute("""
                        UPDATE scanner_health
                        SET status = 'OK', is_acknowledged = TRUE, error_msg = NULL, updated_at = %s
                        WHERE scanner_name = %s
                    """, (datetime.now(IST).isoformat(), scanner_name))
                    logger.info(f"✓ Cleared scanner_health for {scanner_name} (all errors acknowledged)")
                
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                logger.exception(f"❌ acknowledge_fetch_error failed for id={error_id}")
                return False

def acknowledge_all_fetch_errors() -> bool:
    """Acknowledge all fetch errors at once."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Mark all errors as acknowledged and reset counters
                cur.execute("""
                    UPDATE fetch_errors 
                    SET is_acknowledged = TRUE, occurrences = 0
                    WHERE is_acknowledged = FALSE
                """)
                
                # Clear scanner_health for all scanners (mark as OK)
                cur.execute("""
                    UPDATE scanner_health
                    SET status = 'OK', is_acknowledged = TRUE, error_msg = NULL, updated_at = %s
                    WHERE status != 'OK'
                """, (datetime.now(IST).isoformat(),))
                
                conn.commit()
                logger.info("✓ All fetch errors acknowledged")
                return True
            except Exception:
                conn.rollback()
                logger.exception("❌ acknowledge_all_fetch_errors failed")
                return False

def deposit_funds(amount: float) -> float:
    """Deposit funds. Returns new total capital."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Insert deposit transaction
                cur.execute("""
                    INSERT INTO capital_history (transaction_type, amount, description)
                    VALUES ('DEPOSIT', %s, 'User deposit via admin dashboard')
                """, (amount,))
                
                # Get total capital (base + all deposits)
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0) FROM capital_history
                    WHERE transaction_type IN ('BASE_CAPITAL', 'DEPOSIT')
                """)
                result = cur.fetchone()
                total_capital = result[0] if result else 0
                
                conn.commit()
                logger.info(f"✓ Deposited ₹{amount}. New total capital: ₹{total_capital}")
                return total_capital
            except Exception as e:
                conn.rollback()
                logger.exception(f"❌ deposit_funds failed for amount={amount}")
                raise

def get_capital_info() -> dict:
    """Returns {base_capital, total_deposited, total_capital}. Initializes base capital if empty."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                # Check if base capital exists, if not initialize with 500000
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0) FROM capital_history
                    WHERE transaction_type = 'BASE_CAPITAL'
                """)
                base = cur.fetchone()[0]
                
                if base == 0:
                    # Initialize with default base capital
                    cur.execute("""
                        INSERT INTO capital_history (transaction_type, amount, created_at)
                        VALUES ('BASE_CAPITAL', 500000, NOW())
                    """)
                    conn.commit()
                    base = 500000
                
                # Get total deposits (excluding base)
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0) FROM capital_history
                    WHERE transaction_type = 'DEPOSIT'
                """)
                deposited = cur.fetchone()[0]
                
                # Get total capital
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0) FROM capital_history
                    WHERE transaction_type IN ('BASE_CAPITAL', 'DEPOSIT')
                """)
                total = cur.fetchone()[0]
                
                return {
                    "base_capital": base,
                    "total_deposited": deposited,
                    "total_capital": total
                }
            except Exception:
                logger.exception("❌ get_capital_info failed")
                return {"base_capital": 500000, "total_deposited": 0, "total_capital": 500000}

def get_all_data_fetch_health() -> list:
    """Return all rows from data_fetch_health as list of dicts."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("SELECT source_name, last_success, last_failure, consecutive_failures, error_msg, is_acknowledged, updated_at FROM data_fetch_health ORDER BY source_name")
                return [dict(r) for r in cur.fetchall()]
            except Exception:
                logger.exception("❌ get_all_data_fetch_health failed")
                return []

# ── Manual Portfolio Tracker ──────────────────────────────────────────────────

def get_manual_portfolio():
    """Retrieve all manual portfolio entries."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, symbol, entry_date::TEXT, entry_price, quantity
                FROM manual_portfolio
                ORDER BY added_at DESC
            """)
            return cur.fetchall()

def add_portfolio_entry(symbol: str, entry_date: str, entry_price: float, quantity: int):
    """Add a new stock to the manual portfolio."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO manual_portfolio (symbol, entry_date, entry_price, quantity)
                VALUES (%s, %s, %s, %s)
            """, (symbol.upper(), entry_date, entry_price, quantity))
        conn.commit()

def remove_portfolio_entry(entry_id: int):
    """Remove a stock from the manual portfolio by ID."""
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM manual_portfolio WHERE id = %s", (entry_id,))
        conn.commit()

def get_sector_momentum(days=7):
    """Get sector momentum for the last N days. Returns sector stats with win rates & P&L."""
    init_db()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                # Query: Get sector performance from watchlist (joined with alerts)
                cur.execute("""
                    WITH sector_trades AS (
                        SELECT 
                            dw."Sector" as sector,
                            a.symbol,
                            a.status,
                            a.pnl_rs,
                            a.alert_date::DATE as trade_date,
                            a.created_at::DATE as created_date
                        FROM alerts a
                        LEFT JOIN daily_watchlist dw ON a.symbol = dw."Stock"
                        WHERE a.created_at >= CURRENT_TIMESTAMP - INTERVAL '%d days'
                        AND a.status IN ('WIN', 'LOSS')
                    )
                    SELECT 
                        COALESCE(sector, 'Unknown') as sector,
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
                        ROUND(100.0 * SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate_pct,
                        ROUND(COALESCE(SUM(pnl_rs), 0)::NUMERIC, 0)::INTEGER as total_pnl,
                        ROUND((COALESCE(SUM(pnl_rs), 0) / NULLIF(COUNT(*), 0))::NUMERIC, 0)::INTEGER as avg_pnl_per_trade
                    FROM sector_trades
                    GROUP BY sector
                    ORDER BY win_rate_pct DESC, total_trades DESC
                """ % days)
                return [dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.exception(f"❌ get_sector_momentum failed: {e}")
                return []

# ── Parquet Binary Cache ──────────────────────────────────────────────────────

def upload_parquet_to_db(name: str, file_path: str):
    """Upload a binary parquet file to the database for today."""
    if not os.path.exists(file_path):
        return
    today = datetime.now(IST).strftime("%Y-%m-%d")
    init_db()
    try:
        with open(file_path, "rb") as f:
            binary_data = f.read()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO parquet_cache (name, date, data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name, date) DO UPDATE SET data = EXCLUDED.data
                """, (name, today, binary_data))
            conn.commit()
        logger.info(f"💾 Uploaded {name} to DB parquet_cache for {today}")
    except Exception as e:
        logger.error(f"❌ Failed to upload {name} to DB: {e}")

def download_parquet_from_db(name: str, file_path: str) -> bool:
    """Download the latest binary parquet file from the database."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data, date FROM parquet_cache WHERE name = %s ORDER BY date DESC LIMIT 1", (name,))
                row = cur.fetchone()
                if row and row[0]:
                    import os
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "wb") as f:
                        f.write(row[0])
                    logger.info(f"⚡ Downloaded {name} from DB parquet_cache (from date: {row[1]})")
                    return True
        return False
    except Exception as e:
        logger.error(f"❌ Failed to download {name} from DB: {e}")
        return False

def save_df_to_table(table_name: str, df: pd.DataFrame):
    """Saves a Pandas DataFrame to a PostgreSQL table dynamically."""
    if df.empty:
        return
    init_db()
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Fetch destination table columns
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = %s
            """, (table_name.lower(),))
            rows = cur.fetchall()
            db_cols = {row[0].lower(): row[0] for row in rows}
            
            if not db_cols:
                logger.warning(f"⚠️ Table '{table_name}' does not exist in DB or has no columns.")
                return

            # 2. Identify date column
            date_col = None
            for candidate in ["date", "run_date", "created_at", "added_at", "updated_at"]:
                if candidate in db_cols:
                    date_col = db_cols[candidate]
                    break

            # 3. If there is old date data, delete it first
            if date_col:
                cur.execute(f"DELETE FROM {table_name} WHERE {date_col} < %s", (today_str,))
                # Also delete today's data just to be safe from duplicates on retry
                cur.execute(f"DELETE FROM {table_name} WHERE {date_col} = %s", (today_str,))
            else:
                cur.execute(f"TRUNCATE TABLE {table_name}")
                
            # 4. Map DataFrame columns to DB columns (case-insensitive)
            df_cols_mapped = {}
            for col in df.columns:
                col_lower = col.lower().replace(" ", "_").replace("%", "pct").replace("yoy", "yoy").replace("qoq", "qoq")
                if col_lower in db_cols:
                    df_cols_mapped[col] = db_cols[col_lower]
                elif col.lower() in db_cols:
                    df_cols_mapped[col] = db_cols[col.lower()]

            insert_cols = list(df_cols_mapped.values())
            df_source_cols = list(df_cols_mapped.keys())

            # If there's a date column and it's not mapped from DataFrame, add it to insert
            add_date_val = False
            if date_col and date_col not in insert_cols:
                insert_cols.append(date_col)
                add_date_val = True

            if not insert_cols:
                logger.warning(f"⚠️ No matching columns found between DataFrame and table '{table_name}'.")
                return

            # 5. Insert rows
            col_list_str = ", ".join(f'"{c}"' for c in insert_cols)
            val_placeholders = ", ".join(["%s"] * len(insert_cols))
            insert_query = f"INSERT INTO {table_name} ({col_list_str}) VALUES ({val_placeholders})"

            for _, row in df.iterrows():
                vals = [row[sc] for sc in df_source_cols]
                # Convert nan to None for DB
                vals = [None if pd.isna(v) else v for v in vals]
                if add_date_val:
                    vals.append(today_str)
                cur.execute(insert_query, tuple(vals))
                
        conn.commit()
    logger.info(f"✅ Saved {len(df)} rows to table '{table_name}' in database.")

def check_data_exists_for_today() -> bool:
    """Checks if the public table 'daily_watchlist' (fundamental watchlist) contains data for today's IST date."""
    init_db()
    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. First check if 'daily_watchlist' table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'daily_watchlist'
                    )
                """)
                if not cur.fetchone()[0]:
                    return False
                
                # 2. Find date column
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'daily_watchlist'
                """)
                db_cols_raw = [row[0] for row in cur.fetchall()]
                db_cols_lower = [c.lower() for c in db_cols_raw]
                
                date_col = None
                for candidate in ["date", "run_date", "created_at", "added_at"]:
                    if candidate in db_cols_lower:
                        idx = db_cols_lower.index(candidate)
                        date_col = db_cols_raw[idx]
                        break
                
                if not date_col:
                    return False
                
                # 3. Check row count for today (quote column name to handle case sensitivity)
                cur.execute(f'SELECT COUNT(*) FROM daily_watchlist WHERE "{date_col}" = %s', (today_str,))
                count = cur.fetchone()[0]
                return count > 0
    except Exception as e:
        logger.error(f"Error checking if today's data exists in DB: {e}")
        return False

# ── Checkpoint persistence (audit trail) ──────────────────────────────────────────────

def save_checkpoint(checkpoint_name: str, content: str, reason: str = '') -> bool:
    """Save system checkpoint to persistent database."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO system_checkpoints (checkpoint_name, created_at, updated_at, content, reason)
                    VALUES (%s, NOW(), NOW(), %s, %s)
                    ON CONFLICT (checkpoint_name) 
                    DO UPDATE SET updated_at=NOW(), content=EXCLUDED.content, reason=EXCLUDED.reason
                """, (checkpoint_name, content, reason))
                conn.commit()
                logger.info(f"✅ Checkpoint saved: {checkpoint_name}")
                return True
    except Exception as e:
        logger.error(f"❌ Failed to save checkpoint '{checkpoint_name}': {e}")
        return False

def get_checkpoint(checkpoint_name: str) -> str:
    """Retrieve system checkpoint from database."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT content FROM system_checkpoints 
                    WHERE checkpoint_name = %s
                """, (checkpoint_name,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"❌ Failed to retrieve checkpoint '{checkpoint_name}': {e}")
        return None

# ── Telegram Queue Management ──────────────────────────────────────────────────────────

def queue_alert_to_telegram(symbol: str, message_text: str, alert_id: int = None) -> bool:
    """Queue alert for asynchronous Telegram delivery with rate limiting."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO telegram_queue (alert_id, symbol, message_text, created_at)
                    VALUES (%s, %s, %s, NOW())
                """, (alert_id, symbol, message_text))
                conn.commit()
                logger.debug(f"✅ Queued Telegram alert for {symbol}")
                return True
    except Exception as e:
        logger.error(f"❌ Failed to queue Telegram alert: {e}")
        return False

def get_pending_telegram_alerts(limit: int = 5) -> list:
    """Get pending alerts from queue (5 per batch respects 30/sec Telegram limit)."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, alert_id, symbol, message_text, retry_count
                    FROM telegram_queue 
                    WHERE status = 'pending' AND retry_count < 3
                    ORDER BY created_at ASC
                    LIMIT %s
                """, (limit,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch pending Telegram alerts: {e}")
        return []

def mark_telegram_sent(queue_id: int) -> bool:
    """Mark alert as sent in Telegram queue."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE telegram_queue 
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = %s
                """, (queue_id,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"❌ Failed to mark alert sent: {e}")
        return False

def mark_telegram_failed(queue_id: int) -> bool:
    """Increment retry count for failed Telegram send."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE telegram_queue 
                    SET retry_count = retry_count + 1
                    WHERE id = %s AND retry_count < 3
                """, (queue_id,))
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"❌ Failed to retry Telegram alert: {e}")
        return False

def cleanup_old_telegram_sent(days: int = 7) -> int:
    """Clean up sent Telegram messages older than N days."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM telegram_queue 
                    WHERE status = 'sent' 
                    AND created_at < NOW() - INTERVAL %s
                """, (f"{days} days",))
                deleted = cur.rowcount
                conn.commit()
                logger.info(f"🗑️  Deleted {deleted} old Telegram messages (>{days} days)")
                return deleted
    except Exception as e:
        logger.error(f"❌ Failed to cleanup Telegram queue: {e}")
        return 0

# ── Alert Save Verification (2026-06-17) ──────────────────────────────────────────────

def verify_alerts_saved_today(scanner_name: str, expected_count: int) -> bool:
    """
    CRITICAL ERROR CHECK: Verify that alerts from this scan were actually saved to DB.
    
    If a scanner runs but produces 0 alerts in database (when we expected some),
    this is a CRITICAL ERROR indicating database connectivity issues.
    
    Args:
        scanner_name: Name of scanner (e.g., 'INTRADAY', 'EOD', 'REVERSAL')
        expected_count: Number of alerts the scanner generated
    
    Returns:
        True if alerts were successfully saved, False if save failed (CRITICAL ERROR)
    
    Usage:
        total_alerts = 10  # Generated by scanner
        if total_alerts > 0:
            if not verify_alerts_saved_today("INTRADAY", total_alerts):
                # Mark scanner as DOWN - database save failed!
                upsert_scanner_health("INTRADAY", "DOWN", 
                    error_msg="CRITICAL: Alerts failed to save to database")
                return  # Exit early with critical error
    """
    if expected_count == 0:
        return True  # No alerts expected, so nothing to verify
    
    init_db()
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Count alerts from this scanner created today
                cur.execute("""
                    SELECT COUNT(*)
                    FROM alerts
                    WHERE scanner = %s
                    AND DATE(alert_time) = %s
                """, (scanner_name, today_str))
                
                saved_count = cur.fetchone()[0]
                
                if saved_count >= expected_count:
                    logger.info(f"✅ VERIFIED: {scanner_name} saved {saved_count} alerts to DB (expected {expected_count})")
                    return True
                else:
                    logger.error(f"❌ CRITICAL: {scanner_name} expected {expected_count} alerts but only {saved_count} saved to DB")
                    return False
                    
    except Exception as e:
        logger.error(f"❌ CRITICAL: Could not verify alerts for {scanner_name}: {e}")
        return False


def get_current_bayesian_model():
    """
    Get the current ACTIVE (APPROVED) Bayesian model version and weights for all regimes.
    
    CRITICAL: This ONLY returns weights from score_weight_log that have been
    explicitly approved by admin. PENDING updates in bayesian_model_updates
    are NOT included here.
    
    Returns:
        dict: {'BULL': {'version': 'v1', 'weights': {...}}, ...}
    """
    import json
    init_db()
    
    try:
        model = {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get latest APPROVED version and weights for each regime
                # Only read from score_weight_log, which contains only approved weights
                for regime in ['BULL', 'BEAR', 'SIDEWAYS']:
                    cur.execute("""
                        SELECT model_version, weights
                        FROM score_weight_log
                        WHERE regime = %s
                        ORDER BY id DESC
                        LIMIT 1
                    """, (regime,))
                    
                    row = cur.fetchone()
                    if row:
                        model[regime] = {
                            'version': row[0],
                            'weights': json.loads(row[1]) if isinstance(row[1], str) else row[1]
                        }
        
        return model if model else {
            'BULL': {'version': 'v1', 'weights': {}},
            'BEAR': {'version': 'v1', 'weights': {}},
            'SIDEWAYS': {'version': 'v1', 'weights': {}}
        }
    except Exception as e:
        logger.exception(f"❌ Failed to get current Bayesian model: {e}")
        return {}


# ── Bayesian Model Admin Approval Workflow ────────────────────────────────────────────────

def submit_bayesian_update_for_approval(
    regime: str,
    proposed_version: str,
    current_version: str,
    current_weights: dict,
    proposed_weights: dict,
    trades_analyzed: int,
    win_rate: float,
    reason: str
) -> int:
    """
    Submit a Bayesian model weight change for admin approval.
    
    IMPORTANT: This ONLY saves the proposal to bayesian_model_updates.
    Weights are NOT used for calculations until admin explicitly approves.
    
    Args:
        regime: 'BULL', 'BEAR', or 'SIDEWAYS'
        proposed_version: e.g., 'v2'
        current_version: e.g., 'v1' (what's currently live)
        current_weights: dict of current active weights
        proposed_weights: dict of new proposed weights
        trades_analyzed: number of TRAIN trades analyzed
        win_rate: win rate percentage (0.0-1.0)
        reason: explanation of why weights changed
    
    Returns:
        update_id (int) if successful, or None if failed
        
    Side effect: Inserts row into bayesian_model_updates with status='PENDING'
    """
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check if there's already a PENDING update for this regime
                cur.execute("""
                    SELECT id FROM bayesian_model_updates
                    WHERE regime = %s AND status = 'PENDING'
                    LIMIT 1
                """, (regime,))
                
                pending = cur.fetchone()
                if pending:
                    logger.error(f"❌ BLOCKED: Already have PENDING update for {regime} regime (ID: {pending[0]})")
                    logger.error(f"   Admin must approve/reject it before submitting a new proposal")
                    return None
                
                # Insert the proposal with status='PENDING'
                cur.execute("""
                    INSERT INTO bayesian_model_updates (
                        regime, proposed_version, current_version,
                        current_weights, proposed_weights,
                        trades_analyzed, win_rate, reason, status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', (now() AT TIME ZONE 'Asia/Kolkata')::TEXT)
                    RETURNING id
                """, (
                    regime,
                    proposed_version,
                    current_version,
                    json.dumps(current_weights),
                    json.dumps(proposed_weights),
                    trades_analyzed,
                    win_rate,
                    reason
                ))
                
                update_id = cur.fetchone()[0]
                conn.commit()
                
                logger.info(f"✅ Bayesian update SUBMITTED for approval (ID: {update_id})")
                logger.info(f"   Status: PENDING (awaiting admin review)")
                logger.info(f"   Regime: {regime}")
                logger.info(f"   Current version: {current_version}")
                logger.info(f"   Proposed version: {proposed_version}")
                logger.info(f"   Win rate: {win_rate:.1%} from {trades_analyzed} trades")
                
                return update_id
                
    except Exception as e:
        logger.error(f"❌ Failed to submit Bayesian update for approval: {e}")
        return None


def get_pending_bayesian_updates() -> list:
    """Get all PENDING Bayesian updates awaiting admin approval."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, regime, proposed_version, current_version,
                           current_weights, proposed_weights,
                           trades_analyzed, win_rate, reason, created_at
                    FROM bayesian_model_updates
                    WHERE status = 'PENDING'
                    ORDER BY created_at DESC
                """)
                
                updates = []
                for row in cur.fetchall():
                    row_dict = dict(row)
                    # Parse JSON fields
                    row_dict['current_weights'] = json.loads(row_dict['current_weights'])
                    row_dict['proposed_weights'] = json.loads(row_dict['proposed_weights'])
                    updates.append(row_dict)
                
                return updates
    except Exception as e:
        logger.error(f"❌ Failed to fetch pending Bayesian updates: {e}")
        return []


def approve_bayesian_update(update_id: int, admin_name: str, comment: str = "") -> bool:
    """
    ADMIN APPROVES a Bayesian update. Weights are NOW applied to all future scanners.
    
    WORKFLOW:
    1. Update bayesian_model_updates status to APPROVED
    2. INSERT proposed_weights into score_weight_log (makes them LIVE)
    3. Future scanners will use these weights via get_current_bayesian_model()
    
    Args:
        update_id: ID of the bayesian_model_updates row
        admin_name: Admin user who approved
        comment: Optional approval comment
    
    Returns:
        True if approval successful, False otherwise
    """
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Fetch the pending update details
                cur.execute("""
                    SELECT regime, proposed_version, proposed_weights, trades_analyzed, win_rate
                    FROM bayesian_model_updates
                    WHERE id = %s AND status = 'PENDING'
                """, (update_id,))
                
                row = cur.fetchone()
                if not row:
                    logger.error(f"❌ Update {update_id} not found or already processed")
                    return False
                
                regime, proposed_version, proposed_weights_json, trades_analyzed, win_rate = row
                
                # Parse the weights
                proposed_weights = json.loads(proposed_weights_json) if isinstance(proposed_weights_json, str) else proposed_weights_json
                
                # Step 1: Insert into score_weight_log (MAKES WEIGHTS LIVE)
                cur.execute("""
                    INSERT INTO score_weight_log (model_version, regime, weights, created_at)
                    VALUES (%s, %s, %s, (now() AT TIME ZONE 'Asia/Kolkata')::TEXT)
                """, (proposed_version, regime, json.dumps(proposed_weights)))
                
                # Step 2: Update bayesian_model_updates to APPROVED
                cur.execute("""
                    UPDATE bayesian_model_updates
                    SET status = 'APPROVED', approved_by = %s, approved_at = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT,
                        admin_comment = %s, applied_at = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT
                    WHERE id = %s
                """, (admin_name, comment, update_id))
                
                conn.commit()
                
                logger.info(f"✅ APPROVED: Bayesian Update ID {update_id}")
                logger.info(f"   Admin: {admin_name}")
                logger.info(f"   Regime: {regime}")
                logger.info(f"   New version: {proposed_version} NOW LIVE")
                logger.info(f"   Weights inserted into score_weight_log")
                logger.info(f"   Future scanners will use this version")
                
                return True
                
    except Exception as e:
        logger.error(f"❌ Failed to approve Bayesian update {update_id}: {e}")
        return False


def reject_bayesian_update(update_id: int, admin_name: str, reason: str = "") -> bool:
    """
    ADMIN REJECTS a Bayesian update. Weights are NOT applied.
    
    Args:
        update_id: ID of the bayesian_model_updates row
        admin_name: Admin user who rejected
        reason: Why it was rejected
    
    Returns:
        True if rejection successful, False otherwise
    """
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bayesian_model_updates
                    SET status = 'REJECTED', approved_by = %s, rejected_at = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT,
                        admin_comment = %s
                    WHERE id = %s AND status = 'PENDING'
                """, (admin_name, reason, update_id))
                
                if cur.rowcount == 0:
                    logger.error(f"❌ Update {update_id} not found or already processed")
                    return False
                
                conn.commit()
                
                logger.info(f"✅ REJECTED: Bayesian Update ID {update_id}")
                logger.info(f"   Admin: {admin_name}")
                logger.info(f"   Reason: {reason or '(none provided)'}")
                logger.info(f"   Current weights remain unchanged")
                
                return True
                
    except Exception as e:
        logger.error(f"❌ Failed to reject Bayesian update {update_id}: {e}")
        return False


def get_bayesian_update_history(regime: str = None, limit: int = 20) -> list:
    """Get approval history for Bayesian updates."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if regime:
                    cur.execute("""
                        SELECT id, regime, proposed_version, current_version,
                               trades_analyzed, win_rate, status, approved_by,
                               approved_at, rejected_at, admin_comment, created_at
                        FROM bayesian_model_updates
                        WHERE regime = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """, (regime, limit))
                else:
                    cur.execute("""
                        SELECT id, regime, proposed_version, current_version,
                               trades_analyzed, win_rate, status, approved_by,
                               approved_at, rejected_at, admin_comment, created_at
                        FROM bayesian_model_updates
                        ORDER BY created_at DESC
                        LIMIT %s
                    """, (limit,))
                
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch Bayesian update history: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────────────────
# WEALTH BUY ALERT TRACKING
# ──────────────────────────────────────────────────────────────────────────────────────────

def save_wealth_buy_alert(symbol: str, alert_price: float, breakout_type: str = None, 
                         fm_score: float = None, notes: str = None,
                         position_pct: float = None, position_amount: float = None,
                         position_shares: int = None,
                         portfolio_bucket: str = None, valuation_score: float = None,
                         momentum_score: int = None, momentum_confidence: str = None,
                         data_quality: str = None, fallback_timestamp: str = None) -> bool:
    """Save BUY alert to wealth_buy_alert with position sizing. Deduplicates by (symbol, alert_date, breakout_type)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo('Asia/Kolkata'))
    ist_today = now_ist.strftime('%Y-%m-%d')
    ist_time = now_ist.strftime('%H:%M:%S')

    # Safety: Do not persist wealth BUY alerts when the input data is stale.
    # Callers pass `data_quality` and/or `fallback_timestamp` when using cached data.
    try:
        stale_indicators = ("CACHED_PREV_DAY", "CACHED_MULTI_DAY", "MISSING_PARTIAL")
        if data_quality and str(data_quality).upper() in stale_indicators:
            logger.warning(f"🛡️ save_wealth_buy_alert: Suppressing wealth BUY for {symbol} due to data_quality={data_quality}")
            return False

        if fallback_timestamp:
            try:
                from datetime import datetime as _dt
                # Accept either ISO strings or naive timestamps
                if isinstance(fallback_timestamp, str):
                    ts = _dt.fromisoformat(fallback_timestamp)
                else:
                    ts = _dt(fallback_timestamp)
                if ts.date() != now_ist.date():
                    logger.warning(f"🛡️ save_wealth_buy_alert: Suppressing wealth BUY for {symbol} because fallback_timestamp={fallback_timestamp} is not today")
                    return False
            except Exception:
                # If parsing fails, be conservative and suppress
                logger.warning(f"🛡️ save_wealth_buy_alert: Could not parse fallback_timestamp for {symbol}; suppressing buy")
                return False
    except Exception:
        logger.exception("⚠️ save_wealth_buy_alert: stale-data guard check failed unexpectedly — allowing insert")
    
    with _DB_WRITE_LOCK:
        try:
            with get_connection() as conn:
                success = False
                try:
                    with conn.cursor() as cur:
                        # New alert - insert it with position sizing data and explicit IST time (Atomic DO NOTHING)
                        cur.execute("""
                            INSERT INTO wealth_buy_alert 
                            (symbol, alert_price, breakout_type, fm_score, status, notes, alert_date, alert_time,
                             position_pct, position_amount, position_shares, portfolio_bucket, valuation_score,
                             momentum_score, momentum_confidence, data_quality, fallback_timestamp)
                            VALUES (%s, %s, %s, %s, 'ACTIVE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT ON CONSTRAINT uq_wealth_symbol_date_type
                            DO UPDATE SET fm_score = EXCLUDED.fm_score, updated_at = NOW()
                        """, (symbol, alert_price, breakout_type or '', fm_score, notes, ist_today, ist_time,
                              position_pct, position_amount, position_shares, portfolio_bucket, valuation_score,
                              momentum_score, momentum_confidence, data_quality, fallback_timestamp))
                        
                        if cur.rowcount == 0:
                            logger.info(f"⏭️  BUY alert already saved today: {symbol} {breakout_type}")
                            return False  # Duplicate, skip

                        elif cur.rowcount == 1 and cur.statusmessage == 'INSERT 0 1':
                            pass # Normal insert
                        else:
                            pass # Was an update
                            
                        insert_notification('buy', 'New Wealth Buy Alert', f'Wealth alert triggered for {symbol} at ₹{alert_price} ({breakout_type})', symbol)
                            
                        conn.commit()
                        success = True
                finally:
                    if not success:
                        conn.rollback()
            
            msg = f"✅ BUY alert saved: {symbol} @ ₹{alert_price} ({breakout_type}) | Score: {fm_score}"
            if position_pct:
                msg += f" | Size: {position_pct}% (₹{int(position_amount or 0)})"
            logger.info(msg)
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save wealth buy alert: {e}")
            return False


def get_wealth_buy_alerts(symbol: str = None, days_back: int = 30) -> list:
    """Retrieve wealth buy alerts, optionally filtered by symbol."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if symbol:
                    cur.execute("""
                        SELECT * FROM wealth_buy_alert 
                        WHERE symbol = %s AND alert_date::DATE >= (CURRENT_DATE - INTERVAL '%s days')
                        ORDER BY alert_date DESC, alert_time DESC
                    """, (symbol, days_back))
                else:
                    cur.execute("""
                        SELECT * FROM wealth_buy_alert 
                        WHERE alert_date::DATE >= (CURRENT_DATE - INTERVAL '%s days')
                        ORDER BY alert_date DESC, alert_time DESC
                    """, (days_back,))
                
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch wealth buy alerts: {e}")
        return []


def update_wealth_alert_status(alert_id: int, status: str, current_price: float = None) -> bool:
    """Update the status of a wealth buy alert."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE wealth_buy_alert 
                    SET status = %s, current_price = %s, status_updated_at = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT
                    WHERE id = %s
                """, (status, current_price, alert_id))
                conn.commit()
        logger.info(f"✅ Wealth alert {alert_id} status updated to {status}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to update wealth alert status: {e}")
        return False


def get_today_wealth_alerts() -> list:
    """Get all wealth buy alerts for today."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist_today = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM wealth_buy_alert 
                    WHERE alert_date = %s
                    ORDER BY alert_time DESC
                """, (ist_today,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch today's wealth alerts: {e}")
        return []



# ──────────────────────────────────────────────────────────────────────────────
# POSITION LIFECYCLE TRACKING (Open/Closed Positions)
# ──────────────────────────────────────────────────────────────────────────────

def get_open_positions() -> list:
    """Get all open positions (where is_closed=FALSE)."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM wealth_buy_alert 
                    WHERE is_closed = FALSE
                    ORDER BY alert_date DESC, alert_time DESC
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch open positions: {e}")
        return []


def get_closed_positions(days_back: int = 30) -> list:
    """Get closed positions from last N days."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM wealth_buy_alert 
                    WHERE is_closed = TRUE 
                    AND exit_date::DATE >= (CURRENT_DATE - INTERVAL '%s days')
                    ORDER BY exit_date DESC, exit_time DESC
                """, (days_back,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch closed positions: {e}")
        return []


def close_position(symbol: str, exit_price: float, exit_signal: str = None) -> bool:
    """Auto-close an open position when SELL signal detected."""
    with _DB_WRITE_LOCK:
        try:
            with get_connection() as conn:
                success = False
                try:
                    with conn.cursor() as cur:
                        # Get the most recent OPEN position for this symbol
                        cur.execute("""
                            SELECT id, alert_price FROM wealth_buy_alert 
                            WHERE symbol = %s AND is_closed = FALSE
                            ORDER BY alert_date DESC, alert_time DESC
                            LIMIT 1
                        """, (symbol,))
                        
                        result = cur.fetchone()
                        if not result:
                            logger.warning(f"⚠️  No open position found for {symbol}")
                            return False
                        
                        position_id, entry_price = result[0], result[1]
                        
                        # Calculate P&L
                        pnl_rs = exit_price - entry_price
                        pnl_pct = (pnl_rs / entry_price * 100) if entry_price else 0
                        
                        now = datetime.now(IST)
                        exit_date = now.strftime('%Y-%m-%d')
                        exit_time = now.strftime('%H:%M:%S')
                        
                        # Update position as closed
                        cur.execute("""
                            UPDATE wealth_buy_alert 
                            SET is_closed = TRUE, 
                                exit_price = %s, 
                                exit_date = %s, 
                                exit_time = %s,
                                exit_signal = %s,
                                pnl_rs = %s,
                                pnl_pct = %s,
                                status = 'CLOSED'
                            WHERE id = %s
                        """, (exit_price, exit_date, exit_time, exit_signal, pnl_rs, pnl_pct, position_id))
                        
                    conn.commit()
                    success = True
                    logger.info(f"💰 POSITION CLOSED: {symbol} at {exit_price} (P&L: {pnl_pct:.2f}%)")
                    insert_notification('sell', 'Position Closed', f'{symbol} closed at ₹{exit_price} ({exit_signal}). P&L: {pnl_pct:.2f}%', symbol)
                except Exception as inner_e:
                    logger.error(f"Failed to execute position close query: {inner_e}")
                    conn.rollback()
                return success
        except Exception as e:
            logger.error(f"❌ Failed to close position: {e}")
            return False

def get_open_symbols() -> list:
    """Get list of symbols with open positions."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT symbol FROM wealth_buy_alert 
                    WHERE is_closed = FALSE
                    ORDER BY symbol
                """)
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch open symbols: {e}")
        return []


def update_position_current_price(symbol: str, current_price: float) -> bool:
    """Update current_price for all open positions of a symbol."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE wealth_buy_alert 
                    SET current_price = %s, status_updated_at = NOW()
                    WHERE symbol = %s AND is_closed = FALSE
                """, (current_price, symbol))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Failed to update current price for {symbol}: {e}")
        return False


def update_position_real_time_prices(symbols_metrics: dict) -> int:
    """Batch update current_price and current_score for open positions.
    
    Args:
        symbols_metrics: Dict of {symbol: {"price": float, "score": float}}
    
    Returns:
        Count of updated positions
    """
    with _DB_WRITE_LOCK:
        updated_count = 0
        try:
            with get_connection() as conn:
                success = False
                try:
                    with conn.cursor() as cur:
                        for symbol, metrics in symbols_metrics.items():
                            price = metrics.get("price")
                            score = metrics.get("score")
                            
                            if symbol and price is not None and price > 0:
                                cur.execute("""
                                    UPDATE wealth_buy_alert 
                                    SET current_price = %s, current_score = %s, status_updated_at = NOW()
                                    WHERE symbol = %s AND is_closed = FALSE
                                """, (price, score, symbol))
                                updated_count += cur.rowcount
                        conn.commit()
                        success = True
                finally:
                    if not success:
                        conn.rollback()
            logger.info(f"✅ Updated {updated_count} position(s) with real-time metrics")
            return updated_count
        except Exception as e:
            logger.error(f"❌ Failed to update real-time prices: {e}")
            return 0

# ── USER AND SESSION TRACKING ─────────────────────────────────────────────

def upsert_user(name: str) -> Optional[int]:
    """Ensure user exists and return their user_id.

    Use INSERT ... ON CONFLICT DO NOTHING followed by a SELECT to avoid
    performing an UPDATE on every call (which causes RowExclusiveLock thrashing
    when many concurrent processes upsert the same username such as 'aB').
    This minimizes row locking. If high contention persists, consider an
    application-level cache or advisory locks per username.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Insert if missing, but do NOT force an update; doing updates on
                # identical values creates unnecessary row locks under concurrency.
                cur.execute(
                    "INSERT INTO users (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (name,)
                )
                # Retrieve the user_id whether it was inserted now or already existed
                cur.execute("SELECT user_id FROM users WHERE name = %s", (name,))
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"❌ Failed to upsert user {name}: {e}")
        return None

def ping_user_session(user_id: int, ip_address: str):
    """Update active session or create new one."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check for active session for this user/ip
                cur.execute("""
                    SELECT id FROM user_sessions 
                    WHERE user_id = %s AND ip_address = %s AND is_online = TRUE
                    ORDER BY login_time DESC LIMIT 1
                """, (user_id, ip_address))
                session = cur.fetchone()

                if session:
                    # Update logoff_time (last ping)
                    cur.execute("""
                        UPDATE user_sessions SET logoff_time = (now() AT TIME ZONE 'Asia/Kolkata')::TEXT
                        WHERE id = %s
                    """, (session[0],))
                else:
                    # Create new session
                    cur.execute("""
                        INSERT INTO user_sessions (user_id, ip_address, login_time, logoff_time, is_online)
                        VALUES (%s, %s, (now() AT TIME ZONE 'Asia/Kolkata')::TEXT, (now() AT TIME ZONE 'Asia/Kolkata')::TEXT, TRUE)
                    """, (user_id, ip_address))
                conn.commit()
    except Exception as e:
        logger.error(f"❌ Failed to ping user session {user_id}: {e}")

def cleanup_stale_sessions():
    """Mark sessions as offline if not pinged within 2 minutes."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_sessions
                    SET is_online = FALSE
                    WHERE is_online = TRUE
                    AND EXTRACT(EPOCH FROM (now() - logoff_time::timestamp)) > 120
                """)
                conn.commit()
    except Exception as e:
        logger.error(f"❌ Failed to cleanup stale sessions: {e}")

def get_online_users_and_history():
    """Get active viewers and a brief session history."""
    try:
        with get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Active Viewers
                cur.execute("""
                    SELECT u.name, s.ip_address, s.login_time 
                    FROM user_sessions s
                    JOIN users u ON s.user_id = u.user_id
                    WHERE s.is_online = TRUE
                    ORDER BY s.login_time DESC
                """)
                online = cur.fetchall()

                # Session History (last 50)
                cur.execute("""
                    SELECT u.name, s.ip_address, s.login_time, s.logoff_time 
                    FROM user_sessions s
                    JOIN users u ON s.user_id = u.user_id
                    WHERE s.is_online = FALSE
                    ORDER BY s.logoff_time DESC LIMIT 50
                """)
                history = cur.fetchall()

        # Format dates/times for cleaner frontend display
        for row in online:
            # strip fractional seconds if any
            row['login_time'] = row['login_time'].split('.')[0] if row['login_time'] else ''
            
        for row in history:
            row['login_time'] = row['login_time'].split('.')[0] if row['login_time'] else ''
            row['logoff_time'] = row['logoff_time'].split('.')[0] if row['logoff_time'] else ''
            
        return {"online": online, "history": history}
    except Exception as e:
        logger.error(f"❌ Failed to fetch users and history: {e}")
        return {"online": [], "history": []}


# ──────────────────────────────────────────────────────────────────────────────
# REAL-TIME MESSAGING SYSTEM
# ──────────────────────────────────────────────────────────────────────────────

def send_user_message(user_id: int, message: str, is_from_admin: bool = False) -> bool:
    """Send a message between Admin and a specific User."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_messages (user_id, is_from_admin, message)
                    VALUES (%s, %s, %s)
                """, (user_id, is_from_admin, message))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send message for user {user_id}: {e}")
        return False

def get_user_messages(user_id: int) -> list:
    """Fetch all messages for a specific user, ordered chronologically."""
    try:
        with get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, is_from_admin, message, created_at, is_read 
                    FROM user_messages 
                    WHERE user_id = %s 
                    ORDER BY id ASC
                """, (user_id,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to fetch messages for user {user_id}: {e}")
        return []

def mark_user_messages_read(user_id: int, as_admin: bool = False) -> bool:
    """
    Mark messages as read.
    If as_admin=True, marks messages FROM the user (is_from_admin=FALSE) as read.
    If as_admin=False, marks messages FROM the admin (is_from_admin=TRUE) as read.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_messages 
                    SET is_read = TRUE 
                    WHERE user_id = %s AND is_from_admin = %s AND is_read = FALSE
                """, (user_id, not as_admin))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Failed to mark messages read for user {user_id}: {e}")
        return False

def get_unread_message_counts() -> dict:
    """
    Returns unread counts.
    Admin needs to know which users have sent unread messages: {user_name: count}
    """
    try:
        with get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT u.name, COUNT(m.id) as unread_count
                    FROM user_messages m
                    JOIN users u ON m.user_id = u.user_id
                    WHERE m.is_from_admin = FALSE AND m.is_read = FALSE
                    GROUP BY u.name
                """)
                return {row['name']: row['unread_count'] for row in cur.fetchall()}
    except Exception as e:
        logger.error(f"❌ Failed to fetch unread message counts: {e}")
        return {}

# =====================================================================================
# WEALTH SCORE HISTORY PERSISTENCE
# =====================================================================================

def save_hold_score_history(symbol: str, hold_score: int, fm_score: float, rs_6m: float, cmp: float, sma_200: float, evaluation_date: str = None) -> bool:
    """
    Saves the daily hold score evaluation for an open position to the database.
    Also prunes records older than 30 days for closed positions to manage table size.
    """
    init_db()
    if evaluation_date is None:
        from datetime import date
        evaluation_date = date.today().isoformat()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO wealth_score_history (symbol, evaluation_date, hold_score, fm_score, rs_6m, cmp, sma_200)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, evaluation_date) DO UPDATE
                    SET hold_score = EXCLUDED.hold_score,
                        fm_score = EXCLUDED.fm_score,
                        rs_6m = EXCLUDED.rs_6m,
                        cmp = EXCLUDED.cmp,
                        sma_200 = EXCLUDED.sma_200,
                        created_at = NOW();
                """, (symbol, evaluation_date, hold_score, fm_score, rs_6m, cmp, sma_200))
                
                # Prune history for this symbol if it's no longer open and records are > 30 days old
                # This ensures the DB doesn't grow infinitely.
                cur.execute("""
                    DELETE FROM wealth_score_history
                    WHERE symbol = %s 
                    AND evaluation_date < CURRENT_DATE - INTERVAL '30 days'
                    AND NOT EXISTS (
                        SELECT 1 FROM wealth_buy_alert WHERE symbol = %s AND is_closed = FALSE
                    );
                """, (symbol, symbol))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save hold score history for {symbol}: {e}")
        return False


# ==========================================
# BREAKOUT WATCHLIST MULTI-TF
# ==========================================

def upsert_breakout_watchlist(
    symbol: str,
    category: str,
    current_state: str,
    h1_status: str = "PENDING",
    m30_status: str = "PENDING",
    m15_status: str = "PENDING",
    m5_status: str = "PENDING",
    breakout_level: float = None,
    support_level: float = None,
    trigger_level: float = None,
    invalidation_level: float = None,
    max_extension_atr: float = None,
    buffer_pct: float = None,
    armed_at: str = None,
    context_json: str = None
):
    if DONT_SAVE_ALERTS:
        return
    from datetime import datetime
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                session_date = datetime.now(IST).strftime("%Y-%m-%d")
                cur.execute("""
                    INSERT INTO breakout_watchlist (
                        symbol, category, current_state,
                        h1_status, m30_status, m15_status, m5_status,
                        breakout_level, support_level, trigger_level, invalidation_level, 
                        max_extension_atr, buffer_pct, armed_at, session_date, context_json, last_updated
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (symbol) DO UPDATE SET
                        category = EXCLUDED.category,
                        current_state = EXCLUDED.current_state,
                        h1_status = EXCLUDED.h1_status,
                        m30_status = EXCLUDED.m30_status,
                        m15_status = EXCLUDED.m15_status,
                        m5_status = EXCLUDED.m5_status,
                        breakout_level = COALESCE(EXCLUDED.breakout_level, breakout_watchlist.breakout_level),
                        support_level = COALESCE(EXCLUDED.support_level, breakout_watchlist.support_level),
                        trigger_level = COALESCE(EXCLUDED.trigger_level, breakout_watchlist.trigger_level),
                        invalidation_level = COALESCE(EXCLUDED.invalidation_level, breakout_watchlist.invalidation_level),
                        max_extension_atr = COALESCE(EXCLUDED.max_extension_atr, breakout_watchlist.max_extension_atr),
                        buffer_pct = COALESCE(EXCLUDED.buffer_pct, breakout_watchlist.buffer_pct),
                        armed_at = COALESCE(EXCLUDED.armed_at, breakout_watchlist.armed_at),
                        session_date = EXCLUDED.session_date,
                        context_json = COALESCE(EXCLUDED.context_json, breakout_watchlist.context_json),
                        last_updated = NOW()
                """, (symbol, category, current_state, h1_status, m30_status, m15_status, m5_status, 
                      breakout_level, support_level, trigger_level, invalidation_level, max_extension_atr, 
                      buffer_pct, armed_at, session_date, context_json))
                conn.commit()

    except Exception as e:
        logger.exception(f"❌ Failed to upsert breakout_watchlist for {symbol}: {e}")

def get_active_breakout_watchlist() -> list:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, category, current_state, h1_status, m30_status, m15_status, m5_status, 
                           breakout_level, support_level, trigger_level, invalidation_level, max_extension_atr, buffer_pct, armed_at, 
                           context_json, last_updated
                    FROM breakout_watchlist
                    WHERE current_state IN ('HOURLY_APPROVED', 'SETUP_ARMED', 'BREAKOUT_CONFIRMED', 'ENTRY_READY')
                      AND (cooldown_until IS NULL OR cooldown_until < NOW())
                      AND (invalidated_at IS NULL OR invalidated_at > NOW())
                """)
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.exception(f"❌ Failed to fetch active breakout_watchlist: {e}")
        return []

def mark_breakout_watchlist_cooldown(symbol: str, state: str, hours: int = 24):
    if DONT_SAVE_ALERTS:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE breakout_watchlist
                    SET current_state = %s,
                        cooldown_until = NOW() + interval '%s hours',
                        last_updated = NOW()
                    WHERE symbol = %s
                """, (state, hours, symbol))
                conn.commit()
    except Exception as e:
        logger.exception(f"❌ Failed to cooldown {symbol}: {e}")

def sweep_stale_breakout_watchlist():
    """Removes or demotes stale setups."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # If a stock was confirmed or ready but didn't execute by end of session, downgrade it
                cur.execute("""
                    UPDATE breakout_watchlist
                    SET current_state = 'SETUP_ARMED', m15_status = 'PENDING', m5_status = 'PENDING', last_updated = NOW()
                    WHERE current_state IN ('BREAKOUT_CONFIRMED', 'ENTRY_READY')
                      AND session_date < CURRENT_DATE::TEXT
                """)
                # If an hourly approved setup hasn't triggered after 2 days, drop it
                cur.execute("""
                    UPDATE breakout_watchlist
                    SET current_state = 'FAILED', invalidated_at = NOW()
                    WHERE current_state IN ('HOURLY_APPROVED', 'SETUP_ARMED')
                      AND last_updated < NOW() - interval '2 days'
                """)
    except Exception as e:
        logger.exception(f"❌ Failed to sweep breakout_watchlist: {e}")

def reject_alert(alert_id: int):
    """Marks an alert as rejected and refunds its allocated capital."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_rejected, capital_allocated FROM alerts WHERE id = %s", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            is_rejected, capital_allocated = row
            if is_rejected:
                return True
                
            cur.execute("UPDATE alerts SET is_rejected = TRUE WHERE id = %s", (alert_id,))
            
            cap = float(capital_allocated) if capital_allocated else 0.0
            if cap > 0:
                cur.execute(
                    "INSERT INTO capital_history (transaction_type, amount, description) VALUES (%s, %s, %s)",
                    ('trade_refund', cap, f"Refund for rejected alert #{alert_id}")
                )
        conn.commit()
    return True

def reject_multiple_alerts(alert_ids: list):
    """Marks multiple alerts as rejected and refunds their allocated capital."""
    if not alert_ids:
        return True
    with get_connection() as conn:
        with conn.cursor() as cur:
            for alert_id in alert_ids:
                cur.execute("SELECT is_rejected, capital_allocated FROM alerts WHERE id = %s", (alert_id,))
                row = cur.fetchone()
                if not row:
                    continue
                is_rejected, capital_allocated = row
                if is_rejected:
                    continue
                    
                cur.execute("UPDATE alerts SET is_rejected = TRUE WHERE id = %s", (alert_id,))
                
                cap = float(capital_allocated) if capital_allocated else 0.0
                if cap > 0:
                    cur.execute(
                        "INSERT INTO capital_history (transaction_type, amount, description) VALUES (%s, %s, %s)",
                        ('trade_refund', cap, f"Refund for rejected alert #{alert_id}")
                    )
        conn.commit()
    return True

def accept_alert(alert_id: int):
    """Marks an alert as accepted (not rejected) and deducts its allocated capital."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_rejected, capital_allocated FROM alerts WHERE id = %s", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            is_rejected, capital_allocated = row
            if not is_rejected:
                return True
                
            cur.execute("UPDATE alerts SET is_rejected = FALSE WHERE id = %s", (alert_id,))
            
            cap = float(capital_allocated) if capital_allocated else 0.0
            if cap > 0:
                cur.execute(
                    "INSERT INTO capital_history (transaction_type, amount, description) VALUES (%s, %s, %s)",
                    ('trade_deduct', -cap, f"Deduction for re-accepted alert #{alert_id}")
                )
        conn.commit()
    return True

def reallocate_capital(alert_id: int):
    """
    Manually recalculates and reallocates capital to an existing alert.
    Useful if it originally fired when cash was negative and allocated 0.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Fetch current details
            cur.execute("SELECT entry_price, stop_loss, target_price, score, capital_allocated, status, exit_price, scanner, context FROM alerts WHERE id = %s", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            
            entry_price, stop_loss, target_price, score, old_cap, status, exit_price, scanner, context_str = row
            old_cap = float(old_cap) if old_cap else 0.0
            
            # Auto-fill missing Stop Loss and Target Price
            entry_price = float(entry_price) if entry_price else 0.0
            stop_loss = float(stop_loss) if stop_loss else 0.0
            target_price = float(target_price) if target_price else 0.0
            
            if entry_price > 0 and stop_loss <= 0:
                # ── SCANNER-AWARE FALLBACK LOGIC ──
                import json
                fallback_sl = entry_price * 0.90  # Ultimate 10% safety net
                try:
                    ctx = json.loads(context_str) if context_str else {}
                    if scanner == "1H" or scanner == "INTRADAY":
                        # Rely on ATR stored in context during generation
                        atr = float(ctx.get("execution", {}).get("atr", 0))
                        if atr > 0:
                            fallback_sl = entry_price - (1.5 * atr)
                    elif scanner == "multi_tf_scanner":
                        # Explicit final_sl is often stored here
                        f_sl = float(ctx.get("final_sl", 0))
                        if f_sl > 0:
                            fallback_sl = f_sl
                    elif scanner == "EOD":
                        atr = float(ctx.get("technicals", {}).get("atr20", 0))
                        if atr > 0:
                            fallback_sl = entry_price - (2.0 * atr)
                except Exception:
                    pass
                stop_loss = fallback_sl
                
            if entry_price > 0 and stop_loss > 0 and target_price <= 0:
                risk_per_share = entry_price - stop_loss
                target_price = entry_price + (risk_per_share * 2)  # Default 1:2 R:R if missing
            
            # Temporarily free the current margin from the DB view so portfolio_engine sees it
            if old_cap > 0:
                cur.execute("UPDATE alerts SET capital_allocated = 0 WHERE id = %s", (alert_id,))
                conn.commit()
                
            from portfolio_engine import calculate_trade_allocation
            new_cap, new_shares = calculate_trade_allocation(entry_price, stop_loss, score or 80)
            
            # Update the alert with the newly calculated amounts, plus the patched SL/Target, and ensure it's not marked rejected
            cur.execute(
                "UPDATE alerts SET capital_allocated = %s, shares_bought = %s, stop_loss = %s, target_price = %s, is_rejected = FALSE WHERE id = %s",
                (new_cap, new_shares, stop_loss, target_price, alert_id)
            )
            
            # If the trade is already closed (WIN/LOSS), retroactively fix its realized PnL in Rupees
            if status in ('WIN', 'LOSS') and exit_price is not None:
                new_pnl_rs = new_shares * (exit_price - entry_price)
                cur.execute("UPDATE alerts SET pnl_rs = %s WHERE id = %s", (new_pnl_rs, alert_id))
                
            # Adjust the capital_history by recording the net difference
            net_change = old_cap - new_cap
            if net_change != 0:
                tx_type = 'trade_refund' if net_change > 0 else 'trade_deduct'
                desc = f"Reallocation diff for alert #{alert_id}"
                cur.execute(
                    "INSERT INTO capital_history (transaction_type, amount, description) VALUES (%s, %s, %s)",
                    (tx_type, net_change, desc)
                )
                
            conn.commit()
    return True

def reallocate_capital_multiple(alert_ids: list):
    """
    Allocates capital to multiple trades at once, distributing the available cash
    evenly amongst them so one trade doesn't eat the entire budget.
    """
    if not alert_ids: return []
    
    from portfolio_engine import get_portfolio_state, RISK_PERCENT, MAX_POSITION_PCT
    import math
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            format_strings = ','.join(['%s'] * len(alert_ids))
            cur.execute(f"SELECT id, entry_price, stop_loss, target_price, score, capital_allocated, status, exit_price, scanner, context FROM alerts WHERE id IN ({format_strings})", tuple(alert_ids))
            rows = cur.fetchall()
            
            if not rows: return []
            
            # Free up existing capital from these trades so they pool into available_margin
            for r in rows:
                if r[5] and float(r[5]) > 0:
                    cur.execute("UPDATE alerts SET capital_allocated = 0 WHERE id = %s", (r[0],))
            conn.commit()
            
            # Get portfolio state (now includes the freed up capital)
            state = get_portfolio_state()
            total_equity = state["total_equity"]
            available_margin = state["available_margin"]
            
            num_trades = len(rows)
            cash_budget_per_trade = available_margin / num_trades
            
            results = []
            
            for row in rows:
                a_id, entry_price, stop_loss, target_price, score, old_cap, status, exit_price, scanner, context_str = row
                
                entry_price = float(entry_price) if entry_price else 0.0
                stop_loss = float(stop_loss) if stop_loss else 0.0
                target_price = float(target_price) if target_price else 0.0
                
                if entry_price > 0 and stop_loss <= 0:
                    import json
                    fallback_sl = entry_price * 0.90
                    try:
                        ctx = json.loads(context_str) if context_str else {}
                        if scanner in ("1H", "INTRADAY"):
                            atr = float(ctx.get("execution", {}).get("atr", 0))
                            if atr > 0: fallback_sl = entry_price - (1.5 * atr)
                        elif scanner == "multi_tf_scanner":
                            f_sl = float(ctx.get("final_sl", 0))
                            if f_sl > 0: fallback_sl = f_sl
                        elif scanner == "EOD":
                            atr = float(ctx.get("technicals", {}).get("atr20", 0))
                            if atr > 0: fallback_sl = entry_price - (2.0 * atr)
                    except Exception:
                        pass
                    stop_loss = fallback_sl
                    
                if entry_price > 0 and stop_loss > 0 and target_price <= 0:
                    risk_per_share = entry_price - stop_loss
                    target_price = entry_price + (risk_per_share * 2)
                    
                base_risk_percent = RISK_PERCENT
                risk_percent = min(0.05, base_risk_percent * 2) if (score and score >= 90) else base_risk_percent
                per_trade_risk = total_equity * risk_percent
                
                per_share_risk = abs(entry_price - stop_loss)
                if per_share_risk <= 0:
                    shares_to_buy = 0
                else:
                    shares_by_risk = math.floor(per_trade_risk / per_share_risk)
                    max_allocation = total_equity * MAX_POSITION_PCT
                    capital_required = shares_by_risk * entry_price
                    if capital_required > max_allocation:
                        shares_by_risk = math.floor(max_allocation / entry_price)
                        
                    shares_by_cash = math.floor(cash_budget_per_trade / entry_price)
                    shares_to_buy = max(0, min(shares_by_risk, shares_by_cash))
                    
                new_cap = float(shares_to_buy * entry_price)
                
                cur.execute(
                    "UPDATE alerts SET capital_allocated = %s, shares_bought = %s, stop_loss = %s, target_price = %s, is_rejected = FALSE WHERE id = %s",
                    (new_cap, shares_to_buy, stop_loss, target_price, a_id)
                )
                
                if status in ('WIN', 'LOSS') and exit_price is not None:
                    exit_price_val = float(exit_price) if exit_price else 0.0
                    new_pnl_rs = float(exit_price_val - entry_price) * shares_to_buy
                    cur.execute("UPDATE alerts SET pnl_rs = %s WHERE id = %s", (new_pnl_rs, a_id))
                    
                results.append({
                    "id": a_id,
                    "capital_allocated": new_cap,
                    "shares_bought": shares_to_buy,
                    "stop_loss": stop_loss,
                    "target_price": target_price
                })
            conn.commit()
            return results





import secrets
from werkzeug.security import generate_password_hash, check_password_hash

def bootstrap_admin():
    import os
    if os.getenv('BOOTSTRAP_AUTH', '').lower() != 'true':
        return
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # =====================================================================
                # TODO(Abhinav): REMOVE THIS TRUNCATE ONCE YOU HAVE YOUR ADMIN ACC!
                # If left in, this will wipe all registered users on every restart!
                # =====================================================================
                cur.execute("TRUNCATE TABLE users CASCADE")
                
                password = secrets.token_urlsafe(16)
                p_hash = generate_password_hash(password, method='scrypt')
                
                cur.execute("""
                    INSERT INTO users (username, email, mobile, password_hash, role, is_active, must_change_password)
                    VALUES ('admin', 'admin@elitebreakout.temp', '0000000000', %s, 'admin', TRUE, TRUE)
                """, (p_hash,))
            conn.commit()
            logger.info(f"🔐 [SECURITY] Admin setup required. Login as 'admin' with password: {password}")
    except Exception as e:
        logger.error(f"Failed to bootstrap admin: {e}")

def create_user(username, email, mobile, password, first_name='', last_name='', role='user'):
    try:
        p_hash = generate_password_hash(password, method='scrypt')
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (username, email, mobile, password_hash, first_name, last_name, role, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
                    RETURNING user_id
                """, (username, email, mobile, p_hash, first_name, last_name, role))
                user_id = cur.fetchone()[0]
            conn.commit()
            return user_id
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        return None

def verify_user(identifier, password):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, username, password_hash, role, is_active, must_change_password, session_token 
                    FROM users 
                    WHERE username = %s OR email = %s
                """, (identifier, identifier))
                row = cur.fetchone()
                
                if row and check_password_hash(row[2], password):
                    if row[4]: # is_active
                        # reset failed attempts and update last_login
                        cur.execute("UPDATE users SET failed_login_attempts = 0, last_login = NOW() WHERE user_id = %s", (row[0],))
                        conn.commit()
                        return {
                            'user_id': row[0],
                            'username': row[1],
                            'role': row[3],
                            'must_change_password': row[5],
                            'session_token': str(row[6]) if row[6] else None
                        }
                    else:
                        return {'error': 'pending_approval'}
        return None
    except Exception as e:
        logger.error(f"Failed to verify user: {e}")
        return None
