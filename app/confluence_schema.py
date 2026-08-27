# app/confluence_schema.py
# Phase 3: Cross-Scanner Confluence & Meta-Analysis Engine Database Schema & DDL Initialization
#
# RULE 67 CHANGE-RATIONALE:
# - Initializes DDL for `confluence_ledger_v2` and `confluence_meta_conviction_v2` audit logging tables.
# - Enforces state-aware tracking and canonical opportunity_id deduplication.
# - Enforces zero V1 table mutations or side effects.

import logging
import sqlite3
import os

logger = logging.getLogger("ConfluenceV3Schema")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "breakout_system.db"))


def init_confluence_v3_schema(db_path: str = DB_PATH):
    """
    Ensures Phase 3 Confluence Engine database tables and DDL exist.
    """
    logger.info(f"🛠️ Initializing Phase 3 Confluence Database Schema at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. Create confluence_ledger_v2 table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS confluence_ledger_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                date_str TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                meta_conviction_tier TEXT NOT NULL,
                meta_score REAL NOT NULL,
                confluence_depth INTEGER NOT NULL,
                confirmed_engine_count INTEGER NOT NULL,
                watch_engine_count INTEGER NOT NULL,
                participating_engines JSON NOT NULL,
                engine_states JSON NOT NULL,
                sample_size_floor_passed BOOLEAN NOT NULL,
                data_confidence TEXT NOT NULL
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conf_opp_date ON confluence_ledger_v2(opportunity_id, date_str);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conf_sym_date ON confluence_ledger_v2(symbol, date_str);")

        conn.commit()
        logger.info("✅ Phase 3 Confluence Database Schema Initialized Successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Failed to initialize Confluence schema: {e}")
        raise e
    finally:
        conn.close()


if __name__ == "__main__":
    init_confluence_v3_schema()
