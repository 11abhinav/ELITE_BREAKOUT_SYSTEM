# =====================================================================================
# app/near_miss_tracker.py
# NEAR-MISS OPPORTUNITY-COST TRACKER (VALUE-ADD 1)
# =====================================================================================
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional

from database import get_connection, IST, init_db

logger = logging.getLogger("near_miss_tracker")

def init_near_miss_schema() -> None:
    """Creates the near_misses PostgreSQL table if it does not exist."""
    init_db()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS near_misses (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        scanner TEXT NOT NULL,
                        breakout_type TEXT NOT NULL,
                        gate_name TEXT NOT NULL,
                        observed_value NUMERIC(10, 2),
                        threshold_value NUMERIC(10, 2),
                        delta_pct NUMERIC(5, 2),
                        score INTEGER,
                        entry_price NUMERIC(10, 2),
                        stop_loss NUMERIC(10, 2),
                        target_1 NUMERIC(10, 2),
                        logged_at TIMESTAMPTZ NOT NULL,
                        logged_date DATE NOT NULL,
                        status TEXT DEFAULT 'TRACKING',
                        realized_rr NUMERIC(5, 2),
                        max_mfe_r NUMERIC(5, 2) DEFAULT 0.0
                    )
                """)
                # Auto-migrate existing table columns from VARCHAR(30) to TEXT
                cur.execute("""
                    ALTER TABLE near_misses ALTER COLUMN symbol TYPE TEXT;
                    ALTER TABLE near_misses ALTER COLUMN scanner TYPE TEXT;
                    ALTER TABLE near_misses ALTER COLUMN breakout_type TYPE TEXT;
                    ALTER TABLE near_misses ALTER COLUMN gate_name TYPE TEXT;
                    ALTER TABLE near_misses ALTER COLUMN status TYPE TEXT;
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_near_misses_date ON near_misses (logged_date, scanner)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_near_misses_symbol ON near_misses (symbol)")
                conn.commit()
    except Exception as e:
        logger.exception(f"Failed to initialize near_misses table: {e}")

def log_near_miss(
    symbol: str,
    scanner: str,
    breakout_type: str,
    gate_name: str,
    observed_value: float,
    threshold_value: float,
    score: Optional[int] = None,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target_1: Optional[float] = None
) -> None:
    """
    Logs a near-miss candidate rejected within 10% of a gate threshold into PostgreSQL.
    """
    if not observed_value or not threshold_value or threshold_value == 0:
        return
        
    delta_pct = abs(observed_value - threshold_value) / threshold_value * 100.0
    if delta_pct > 10.0:  # Only track candidates within 10% of gate
        return

    now_ist = datetime.now(IST)
    today_date = now_ist.date()

    clean_symbol = str(symbol).strip()[:30]
    clean_scanner = str(scanner).strip()[:100]
    clean_breakout_type = str(breakout_type).strip()[:150]
    clean_gate_name = str(gate_name).strip()[:150]

    try:
        init_near_miss_schema()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO near_misses (
                        symbol, scanner, breakout_type, gate_name, observed_value,
                        threshold_value, delta_pct, score, entry_price, stop_loss,
                        target_1, logged_at, logged_date
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    clean_symbol, clean_scanner, clean_breakout_type, clean_gate_name, observed_value,
                    threshold_value, round(delta_pct, 2), score, entry_price,
                    stop_loss, target_1, now_ist, today_date
                ))
                conn.commit()
                logger.info(f"🎯 [NEAR-MISS LOGGED] {clean_symbol} ({clean_scanner}) gate '{clean_gate_name}': obs={observed_value:.2f} vs thresh={threshold_value:.2f} (delta: {delta_pct:.1f}%)")
    except Exception as e:
        logger.exception(f"Failed to log near-miss for {symbol}: {e}")
