# app/reversal_schema.py
# Phase 2D: Reversal Breakout Scanner V2 Database Schema & DDL Initialization
#
# RULE 67 CHANGE-RATIONALE:
# - Initializes DDL for `reversal_rejection_ledger_v2` audit logging table.
# - Verifies required metadata columns on shared `scanner_candidates` table.
# - Enforces zero V1 table mutations or side effects.

import logging
import sqlite3
import os

logger = logging.getLogger("ReversalV2Schema")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "breakout_system.db"))


def init_reversal_v2_schema(db_path: str = DB_PATH):
    """
    Ensures Phase 2D Reversal V2 database tables and DDL exist.
    """
    logger.info(f"🛠️ Initializing Phase 2D Reversal V2 Database Schema at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. Create reversal_rejection_ledger_v2 table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reversal_rejection_ledger_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date_str TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                engine_path TEXT NOT NULL,
                passed BOOLEAN NOT NULL,
                rejection_gate TEXT,
                primary_blocker TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                quality_score REAL NOT NULL,
                quality_grade TEXT NOT NULL,
                state_reasons JSON NOT NULL
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rev_rej_sym_date ON reversal_rejection_ledger_v2(symbol, date_str);")

        # 2. Verify scanner_candidates has required metadata columns
        cursor.execute("PRAGMA table_info(scanner_candidates);")
        columns = [row[1] for row in cursor.fetchall()]

        if columns:
            needed = ["quality_score", "quality_grade", "setup_id", "state_reasons"]
            for col in needed:
                if col not in columns:
                    cursor.execute(f"ALTER TABLE scanner_candidates ADD COLUMN {col} TEXT;")
                    logger.info(f"Added missing column '{col}' to scanner_candidates.")

        conn.commit()
        logger.info("✅ Phase 2D Reversal V2 Schema Initialized Successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Failed to initialize Reversal V2 schema: {e}")
        raise e
    finally:
        conn.close()


if __name__ == "__main__":
    init_reversal_v2_schema()
