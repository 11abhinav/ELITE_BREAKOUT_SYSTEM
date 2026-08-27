# app/eod_v2_schema.py
# Phase 2B: EOD Breakout Scanner V2 DDL & Database Schema Initialization
#
# RULE 67 CHANGE-RATIONALE:
# - Creates isolated DDL tables for eod_rejection_ledger_v2.
# - Integrates cleanly with Phase-1 universal tables (scanner_candidates, candidate_snapshots, alerts).
# - Enforces strict isolation invariants: Zero mutations to V1 schema or legacy tables.

import logging
from database import get_connection

logger = logging.getLogger(__name__)


def init_eod_v2_schemas() -> None:
    """
    Initializes PostgreSQL DDL schemas for EOD Breakout Scanner V2.
    Safe to execute repeatedly (IDEMPOTENT).
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # ── EOD V2 Rejection Ledger Table ──────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS eod_rejection_ledger_v2 (
                        id BIGSERIAL PRIMARY KEY,
                        eval_date DATE NOT NULL,
                        symbol VARCHAR(30) NOT NULL,
                        setup_id VARCHAR(100),
                        rejection_stage VARCHAR(50) NOT NULL,
                        rejection_code VARCHAR(50) NOT NULL,
                        primary_reason TEXT,
                        actual_value NUMERIC(14, 4),
                        threshold_value NUMERIC(14, 4),
                        market_regime VARCHAR(30),
                        universe_status VARCHAR(30),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_eod_rej_date_sym ON eod_rejection_ledger_v2 (eval_date, symbol);
                    CREATE INDEX IF NOT EXISTS idx_eod_rej_code ON eod_rejection_ledger_v2 (rejection_code);
                """)
        logger.info("[EOD_V2_SCHEMA] eod_rejection_ledger_v2 schema initialized successfully.")
    except Exception as e:
        logger.warning(f"[EOD_V2_SCHEMA] Error initializing EOD V2 schemas: {e}")
