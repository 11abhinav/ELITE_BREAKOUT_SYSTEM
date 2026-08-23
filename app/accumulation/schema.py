"""
app/accumulation/schema.py — Isolated DDL Schema Initializer for ACCUMULATION_SCANNER_V1.
Manages all accumulation_* tables independently without modifying shared app/database.py.
"""

import logging

logger = logging.getLogger(__name__)

CREATE_ACCUMULATION_CONTROL_TABLE = """
CREATE TABLE IF NOT EXISTS accumulation_control (
    id SERIAL PRIMARY KEY,
    scanner_name TEXT NOT NULL UNIQUE DEFAULT 'ACCUMULATION_SCANNER_V1',
    accumulation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    accumulation_paused BOOLEAN NOT NULL DEFAULT FALSE,
    accumulation_stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
    accumulation_manual_run_requested BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_ACCUMULATION_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS accumulation_runs (
    run_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL DEFAULT 'SCHEDULED_1545',
    status TEXT NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    metrics JSONB DEFAULT '{}'::jsonb
);
"""

CREATE_ACCUMULATION_HEALTH_TABLE = """
CREATE TABLE IF NOT EXISTS accumulation_health (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    scanner TEXT NOT NULL DEFAULT 'ACCUMULATION_SCANNER_V1',
    status TEXT NOT NULL DEFAULT 'HEALTHY',
    lifecycle_state TEXT NOT NULL DEFAULT 'IDLE',
    current_phase TEXT NOT NULL DEFAULT 'READY',
    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    failure_reason TEXT,
    control_state TEXT DEFAULT 'RUNNING',
    certification_status TEXT DEFAULT 'PENDING',
    strategy_version TEXT DEFAULT 'ACCUMULATION_V1.0',
    config_version TEXT DEFAULT 'ACCUM_CFG_V1',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC
);
"""

CREATE_ACCUMULATION_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS accumulation_alerts (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    audit_snapshot_id TEXT NOT NULL UNIQUE,
    parent_snapshot_id TEXT,
    finalization_snapshot_id TEXT,
    finalization_status TEXT DEFAULT 'PASSED',
    symbol TEXT NOT NULL,
    signal_state TEXT NOT NULL,
    tradable BOOLEAN NOT NULL DEFAULT TRUE,
    score NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    entry_zone_low NUMERIC NOT NULL,
    entry_zone_high NUMERIC NOT NULL,
    breakout_level NUMERIC NOT NULL,
    preferred_entry NUMERIC NOT NULL,
    entry_method TEXT NOT NULL DEFAULT 'ZONE_MIDPOINT',
    entry_trigger_rule TEXT NOT NULL DEFAULT 'RANGE_TOUCH',
    stop_loss NUMERIC NOT NULL,
    target_1 NUMERIC NOT NULL,
    target_2 NUMERIC NOT NULL,
    target_3 NUMERIC NOT NULL,
    risk_pct NUMERIC NOT NULL,
    rr_1 NUMERIC NOT NULL,
    rr_2 NUMERIC NOT NULL,
    rr_3 NUMERIC NOT NULL,
    suggested_capital NUMERIC,
    suggested_position_size INTEGER,
    position_sizing_basis TEXT DEFAULT 'ACCOUNT_RISK_1PCT',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    effective_as_of TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_ACCUMULATION_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS accumulation_trades (
    trade_id BIGSERIAL PRIMARY KEY,
    alert_id BIGINT REFERENCES accumulation_alerts(id),
    run_id TEXT NOT NULL,
    audit_snapshot_id TEXT NOT NULL,
    parent_snapshot_id TEXT,
    symbol TEXT NOT NULL,
    signal_state TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'ZONE_MIDPOINT',
    entry_trigger_rule TEXT NOT NULL DEFAULT 'RANGE_TOUCH',
    entry_reference_type TEXT NOT NULL DEFAULT 'STRATEGY_REFERENCE',
    entry_zone_low NUMERIC NOT NULL,
    entry_zone_high NUMERIC NOT NULL,
    entry_price NUMERIC NOT NULL,
    preferred_entry NUMERIC NOT NULL,
    entry_trigger_level NUMERIC NOT NULL,
    entry_displacement_reference NUMERIC NOT NULL,
    breakout_level NUMERIC NOT NULL,
    stop_loss NUMERIC NOT NULL,
    target_1 NUMERIC NOT NULL,
    target_2 NUMERIC NOT NULL,
    target_3 NUMERIC NOT NULL,
    best_target_reached TEXT,
    last_milestone_timestamp TIMESTAMPTZ,
    last_milestone_bar_timestamp TIMESTAMPTZ,
    last_milestone_price NUMERIC,
    entry_triggered_at TIMESTAMPTZ,
    entry_triggered_price NUMERIC,
    entry_trigger_type TEXT,
    entry_quality TEXT,
    entry_gap_pct NUMERIC,
    trigger_direction TEXT,
    entry_trigger_level_reached BOOLEAN,
    entry_trigger_bar_timestamp TIMESTAMPTZ,
    entry_trigger_bar_open NUMERIC,
    entry_trigger_bar_high NUMERIC,
    entry_trigger_bar_low NUMERIC,
    entry_trigger_bar_close NUMERIC,
    setup_created_as_of TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exit_bar_timestamp TIMESTAMPTZ,
    risk_pct NUMERIC NOT NULL,
    rr_1 NUMERIC NOT NULL,
    rr_2 NUMERIC NOT NULL,
    rr_3 NUMERIC NOT NULL,
    suggested_capital NUMERIC,
    suggested_position_size INTEGER,
    position_sizing_basis TEXT DEFAULT 'ACCOUNT_RISK_1PCT',
    account_risk_pct NUMERIC DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'ACTIVE_SETUP',
    setup_outcome TEXT NOT NULL DEFAULT 'PENDING',
    exit_reason TEXT,
    exit_price NUMERIC,
    exit_timestamp TIMESTAMPTZ,
    exit_status TEXT DEFAULT 'OK',
    exit_assumption TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    effective_as_of TIMESTAMPTZ DEFAULT NOW(),
    strategy_version TEXT NOT NULL DEFAULT 'ACCUMULATION_V1.0',
    sl_target_version TEXT NOT NULL DEFAULT 'ACCUM_SL_V1',
    config_version TEXT NOT NULL DEFAULT 'ACCUM_CFG_V1',
    score_normalization_version TEXT NOT NULL DEFAULT 'ACCUM_SCORE_NORM_V1',
    CONSTRAINT chk_trade_status CHECK (status IN (
        'ACTIVE_SETUP', 'ENTRY_TRIGGERED', 'TARGET_1_REACHED', 'TARGET_2_REACHED',
        'SETUP_COMPLETED', 'STOP_TRIGGERED', 'STRUCTURE_INVALIDATED', 'RS_FAILURE', 'TIME_EXIT', 'SETUP_EXPIRED', 'ENTRY_GAP_REJECTED'
    )),
    CONSTRAINT chk_setup_outcome CHECK (setup_outcome IN ('PENDING', 'SUCCESS', 'FAILURE', 'EXPIRED', 'INVALIDATED')),
    CONSTRAINT chk_entry_price CHECK (
        (entry_type = 'ZONE_MIDPOINT' AND entry_price = preferred_entry AND entry_trigger_level = preferred_entry)
        OR
        (entry_type = 'BREAKOUT_CONFIRMATION' AND entry_price = entry_trigger_level AND entry_trigger_level > preferred_entry)
    ),
    CONSTRAINT chk_entry_trigger_rule CHECK (
        (entry_type = 'ZONE_MIDPOINT' AND entry_trigger_rule = 'RANGE_TOUCH')
        OR
        (entry_type = 'BREAKOUT_CONFIRMATION' AND entry_trigger_rule = 'LEVEL_CROSS')
    ),
    CONSTRAINT chk_entry_reference_type CHECK (
        (entry_type = 'ZONE_MIDPOINT' AND entry_reference_type = 'STRATEGY_REFERENCE')
        OR
        (entry_type = 'BREAKOUT_CONFIRMATION' AND entry_reference_type = 'CONFIRMED_LEVEL')
    ),
    CONSTRAINT chk_entry_bounds CHECK (entry_zone_low <= preferred_entry AND preferred_entry <= entry_zone_high AND entry_zone_low < entry_zone_high),
    CONSTRAINT chk_price_sl CHECK (entry_price > stop_loss),
    CONSTRAINT chk_breakout CHECK (breakout_level > entry_zone_high),
    CONSTRAINT chk_targets CHECK (target_1 >= breakout_level AND target_1 > entry_price AND target_1 < target_2 AND target_2 < target_3),
    CONSTRAINT chk_risk CHECK (risk_pct > 0 AND rr_1 >= 0),
    CONSTRAINT chk_entry_trigger_method CHECK (entry_type IN ('ZONE_MIDPOINT', 'BREAKOUT_CONFIRMATION')),
    CONSTRAINT chk_entry_trigger_type CHECK (entry_trigger_type IS NULL OR entry_trigger_type IN ('ZONE_TOUCH', 'BREAKOUT_BUFFER', 'GAP_THROUGH'))
);
"""

CREATE_ACTIVE_SETUP_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_accumulation_one_live_setup
ON accumulation_trades(symbol)
WHERE status IN (
    'ACTIVE_SETUP',
    'ENTRY_TRIGGERED',
    'TARGET_1_REACHED',
    'TARGET_2_REACHED'
);
"""

CREATE_AUDIT_SNAPSHOT_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_accumulation_audit_snapshot
ON accumulation_trades(audit_snapshot_id);
"""

def init_accumulation_schema(conn=None) -> bool:
    """
    Initializes all accumulation subsystem tables, constraints, and indices.
    Accepts an optional DB connection (psycopg2 or sqlite3 mock/real).
    """
    ddl_statements = [
        CREATE_ACCUMULATION_CONTROL_TABLE,
        CREATE_ACCUMULATION_RUNS_TABLE,
        CREATE_ACCUMULATION_HEALTH_TABLE,
        CREATE_ACCUMULATION_ALERTS_TABLE,
        CREATE_ACCUMULATION_TRADES_TABLE,
        CREATE_ACTIVE_SETUP_UNIQUE_INDEX,
        CREATE_AUDIT_SNAPSHOT_UNIQUE_INDEX,
    ]
    if conn is None:
        try:
            from database import get_db_connection
            conn = get_db_connection()
        except Exception as e:
            logger.warning(f"Could not acquire DB connection for accumulation schema init: {e}")
            return False

    try:
        cur = conn.cursor()
        for stmt in ddl_statements:
            cur.execute(stmt)
        conn.commit()
        logger.info("ACCUMULATION_SCANNER_V1 schema initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize ACCUMULATION_SCANNER_V1 schema: {e}", exc_info=True)
        if hasattr(conn, "rollback"):
            conn.rollback()
        return False
