"""
daily_builder_schema.py
=======================
Phase 2A Daily Builder V2 — DB Schema Migrations.

Creates the V2-specific tables that are completely isolated from V1 tables.
All functions are idempotent (IF NOT EXISTS) and safe to call on every boot.

Tables created here:
  - universe_watch          (UNIVERSE_DEGRADATION_WATCH log — §3.15)
  - daily_watchlist_v2      (V2 elite universe — parallel to daily_watchlist)
  - daily_excluded_watchlist_v2 (V2 exclusion report)

[INV-1] These functions MUST NOT touch: alerts, near_misses, daily_watchlist,
        daily_excluded_watchlist, or any Phase 1 table. V1 schema is untouched.

[VERSION: DAILY_BUILDER_V2_SCHEMA_v1.0]
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# universe_watch  (UNIVERSE_DEGRADATION_WATCH)
# ---------------------------------------------------------------------------

_UNIVERSE_WATCH_DDL = """
CREATE TABLE IF NOT EXISTS universe_watch (
    id                      SERIAL PRIMARY KEY,
    symbol                  VARCHAR(40) NOT NULL,

    -- When degradation first detected and last confirmed
    first_watch_date        DATE NOT NULL,
    last_seen_date          DATE NOT NULL,

    -- What the stock was before degradation
    previous_status         VARCHAR(20),          -- 'ELITE' | 'NEAR_QUALIFIED'
    previous_score          FLOAT,
    previous_tier           VARCHAR(5),           -- 'A+' | 'A' | 'B' | 'C' | NULL
    status_change_date      DATE,
    consecutive_builds      INTEGER DEFAULT 1,

    -- Current degraded state
    current_score           FLOAT,
    current_tier            VARCHAR(5),
    primary_gap_code        VARCHAR(60),
    gap_magnitude           FLOAT,
    degradation_reason      VARCHAR(30),          -- GROWTH | PROFITABILITY | CASH_FLOW | etc.
    degradation_severity    VARCHAR(10),          -- MINOR | MODERATE | MAJOR

    -- Human-readable summary of what changed
    change_summary          TEXT,

    -- Recovery lifecycle
    is_active               BOOLEAN DEFAULT TRUE,
    recovery_date           DATE,
    recovery_status         VARCHAR(20),          -- 'RECOVERED_ELITE' | 'RECOVERED_NQ' | NULL

    algorithm_version       VARCHAR(10) DEFAULT 'v2.0',

    UNIQUE (symbol, first_watch_date)
);
"""

_UNIVERSE_WATCH_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_universe_watch_symbol ON universe_watch (symbol);",
    "CREATE INDEX IF NOT EXISTS idx_universe_watch_active ON universe_watch (is_active) WHERE is_active = TRUE;",
    "CREATE INDEX IF NOT EXISTS idx_universe_watch_last_seen ON universe_watch (last_seen_date);",
]

# ---------------------------------------------------------------------------
# daily_watchlist_v2  (V2 elite universe — mirrors daily_watchlist structure)
# ---------------------------------------------------------------------------

_DAILY_WATCHLIST_V2_DDL = """
CREATE TABLE IF NOT EXISTS daily_watchlist_v2 (
    id                          SERIAL PRIMARY KEY,
    symbol                      VARCHAR(40) NOT NULL,
    build_date                  DATE NOT NULL,

    -- Universe outcome
    universe_status             VARCHAR(20) NOT NULL,   -- ELITE | NEAR_QUALIFIED
    quality_tier                VARCHAR(5),             -- A+ | A | B | C (ELITE only)
    near_qualified_mode         VARCHAR(15),            -- SCORE_BAND | SINGLE_GAP | NULL

    -- Scores  [INV-2] data_confidence is always alongside the score
    universe_quality_score      INTEGER NOT NULL,
    data_confidence             VARCHAR(10) NOT NULL,   -- HIGH | MEDIUM | LOW
    business_quality            INTEGER,
    growth_quality              INTEGER,
    valuation_context           INTEGER,
    governance                  INTEGER,

    -- Coverage fractions
    business_quality_coverage   FLOAT,
    growth_quality_coverage     FLOAT,
    valuation_coverage          FLOAT,
    governance_coverage         FLOAT,

    -- Fundamental profile (frozen handoff contract for Phase 2B+) [INV-3]
    fundamental_profile         JSONB,

    -- Institutional context (NOT part of score)
    institutional_interest      VARCHAR(10),
    delivery_pct_5d             FLOAT,
    has_institutional_buyers    BOOLEAN,
    block_deals_30d             INTEGER,

    -- Near-qualified gap fields
    gap_quality_factors         JSONB,
    gap_count                   INTEGER DEFAULT 0,

    -- Checklist (full per-criterion JSON)
    checklist                   JSONB,

    -- Provenance
    tv_data_timestamp           TIMESTAMPTZ,
    fund_cache_age_days         INTEGER,
    delivery_data_date          DATE,
    universe_freshness          VARCHAR(30) DEFAULT 'LIVE',  -- LIVE | FALLBACK_N_DAYS_OLD
    is_survivorship_corrected   BOOLEAN DEFAULT FALSE,
    algorithm_version           VARCHAR(10) DEFAULT 'v2.0',

    -- Corporate action flag
    corporate_action_flag       VARCHAR(30),
    corporate_action_detail     JSONB,

    -- Financial routing
    financial_sub_path          VARCHAR(30),            -- NON_FINANCIAL | BANK | NBFC_HFC | INSURANCE | AMC | FINANCIAL_UNCLASSIFIED

    -- Turnaround flag
    is_turnaround               BOOLEAN DEFAULT FALSE,

    -- Raw market data snapshot
    price                       FLOAT,
    market_cap_cr               FLOAT,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (symbol, build_date)
);
"""

_DAILY_WATCHLIST_V2_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dwv2_build_date ON daily_watchlist_v2 (build_date);",
    "CREATE INDEX IF NOT EXISTS idx_dwv2_symbol ON daily_watchlist_v2 (symbol);",
    "CREATE INDEX IF NOT EXISTS idx_dwv2_status ON daily_watchlist_v2 (universe_status, build_date);",
    "CREATE INDEX IF NOT EXISTS idx_dwv2_tier ON daily_watchlist_v2 (quality_tier, build_date) WHERE quality_tier IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_dwv2_confidence ON daily_watchlist_v2 (data_confidence, build_date);",
]

# ---------------------------------------------------------------------------
# daily_excluded_watchlist_v2  (V2 exclusion report)
# ---------------------------------------------------------------------------

_DAILY_EXCLUDED_V2_DDL = """
CREATE TABLE IF NOT EXISTS daily_excluded_watchlist_v2 (
    id                          SERIAL PRIMARY KEY,
    symbol                      VARCHAR(40) NOT NULL,
    build_date                  DATE NOT NULL,

    -- Exclusion detail
    exclusion_class             VARCHAR(20) NOT NULL,   -- HARD_BLOCK | QUALITY_FAIL | DATA_FAIL | SURVEILLANCE
    primary_exclusion_code      VARCHAR(60) NOT NULL,
    secondary_exclusion_codes   JSONB,

    -- Score at time of exclusion (populated even for excluded stocks, for debugging)
    universe_quality_score      INTEGER,
    data_confidence             VARCHAR(10),            -- [INV-2] always emitted

    -- Full per-criterion checklist (admin diagnostics — not surfaced in user UI)
    checklist                   JSONB,

    -- Financial routing
    financial_sub_path          VARCHAR(30),

    -- Corporate action flag
    corporate_action_flag       VARCHAR(30),

    -- Shell risk provisional flag
    shell_risk_provisional      BOOLEAN DEFAULT FALSE,  -- TRUE if exclusion_code = SHELL_RISK

    -- Provenance
    universe_freshness          VARCHAR(30) DEFAULT 'LIVE',
    is_survivorship_corrected   BOOLEAN DEFAULT FALSE,
    algorithm_version           VARCHAR(10) DEFAULT 'v2.0',

    -- Raw market data
    price                       FLOAT,
    market_cap_cr               FLOAT,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (symbol, build_date)
);
"""

_DAILY_EXCLUDED_V2_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dev2_build_date ON daily_excluded_watchlist_v2 (build_date);",
    "CREATE INDEX IF NOT EXISTS idx_dev2_symbol ON daily_excluded_watchlist_v2 (symbol);",
    "CREATE INDEX IF NOT EXISTS idx_dev2_exclusion_class ON daily_excluded_watchlist_v2 (exclusion_class, build_date);",
    "CREATE INDEX IF NOT EXISTS idx_dev2_primary_code ON daily_excluded_watchlist_v2 (primary_exclusion_code, build_date);",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_universe_watch_schema() -> None:
    """
    Creates the universe_watch table and indexes if they do not exist.
    Idempotent — safe to call on every process boot.

    [INV-1] Only creates universe_watch. Does not touch any Phase 1 table.
    """
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_UNIVERSE_WATCH_DDL)
                for idx_sql in _UNIVERSE_WATCH_INDEXES:
                    cur.execute(idx_sql)
            conn.commit()
        logger.info("[DAILY_BUILDER_V2_SCHEMA] universe_watch table ready.")
    except Exception as e:
        logger.error(f"[DAILY_BUILDER_V2_SCHEMA] Failed to init universe_watch: {e}", exc_info=True)
        raise


def init_universe_tables_schema() -> None:
    """
    Creates daily_watchlist_v2 and daily_excluded_watchlist_v2 tables if they
    do not exist. Idempotent — safe to call on every process boot.

    [INV-1] Only creates V2 tables. The V1 tables daily_watchlist and
    daily_excluded_watchlist are never touched.
    """
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DAILY_WATCHLIST_V2_DDL)
                for idx_sql in _DAILY_WATCHLIST_V2_INDEXES:
                    cur.execute(idx_sql)
                cur.execute(_DAILY_EXCLUDED_V2_DDL)
                for idx_sql in _DAILY_EXCLUDED_V2_INDEXES:
                    cur.execute(idx_sql)
            conn.commit()
        logger.info("[DAILY_BUILDER_V2_SCHEMA] daily_watchlist_v2 + daily_excluded_watchlist_v2 ready.")
    except Exception as e:
        logger.error(f"[DAILY_BUILDER_V2_SCHEMA] Failed to init V2 universe tables: {e}", exc_info=True)
        raise


def init_all_v2_schemas() -> None:
    """
    Convenience wrapper — initialises all three V2 schema objects.
    Call once at Daily Builder V2 startup, before any run logic.

    [INV-1] V1 tables (daily_watchlist, daily_excluded_watchlist, alerts,
    near_misses) are untouched by this function.
    """
    init_universe_watch_schema()
    init_universe_tables_schema()
    logger.info("[DAILY_BUILDER_V2_SCHEMA] All V2 schemas initialised.")
