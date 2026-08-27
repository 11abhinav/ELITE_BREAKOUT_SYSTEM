# app/accumulation_schema.py
# Phase 2F: Accumulation Breakout Scanner V2 Database Schema & DDL Initialization
#
# RULE 67 CHANGE-RATIONALE:
# - Initializes DDL for `accumulation_rejection_ledger_v2` audit logging table.
# - Verifies required metadata columns on shared `scanner_candidates` table.
# - Enforces zero V1 table mutations or side effects.

import logging
import sqlite3
import os

logger = logging.getLogger("AccumulationV2Schema")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "breakout_system.db"))


def init_accumulation_v2_schema(db_path: str = DB_PATH):
    """
    Ensures Phase 2F Accumulation V2 database tables and DDL exist.
    """
    logger.info(f"🛠️ Initializing Phase 2F Accumulation V2 Database Schema at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. Create accumulation_rejection_ledger_v2 table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accumulation_rejection_ledger_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date_str TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                passed BOOLEAN NOT NULL,
                rejection_gate TEXT,
                primary_blocker TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                quality_score REAL NOT NULL,
                quality_grade TEXT NOT NULL,
                stage_progress INTEGER NOT NULL,
                maturity_score REAL NOT NULL,
                accumulation_class TEXT NOT NULL,
                distribution_risk_score INTEGER NOT NULL,
                data_confidence TEXT NOT NULL,
                state_reasons JSON NOT NULL
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_acc_rej_sym_date ON accumulation_rejection_ledger_v2(symbol, date_str);")

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
        logger.info("✅ Phase 2F Accumulation V2 Schema Initialized Successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Failed to initialize Accumulation V2 schema: {e}")
        raise e
    finally:
        conn.close()


if __name__ == "__main__":
    init_accumulation_v2_schema()
