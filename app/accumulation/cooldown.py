"""
app/accumulation/cooldown.py — Deduplication & State Promotion Engine for ACCUMULATION_SCANNER_V1.
Manages 10-day terminal setup cooldown and contiguous multi-day state transitions.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from app.accumulation.config import COOLDOWN_AFTER_TERMINAL_DAYS

logger = logging.getLogger(__name__)

class AccumulationCooldownEngine:
    """Cooldown & Deduplication Engine for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def is_in_cooldown(symbol: str, as_of_date: Optional[datetime] = None, conn=None) -> bool:
        """
        Checks if symbol has a terminal setup created within the last COOLDOWN_AFTER_TERMINAL_DAYS (10 days).
        """
        if as_of_date is None:
            as_of_date = datetime.utcnow()

        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.warning(f"Could not connect DB for cooldown check: {e}")
                return False

        try:
            cur = conn.cursor()
            query = """
                SELECT setup_created_as_of, exit_bar_timestamp, updated_at
                FROM accumulation_trades
                WHERE symbol = %s
                  AND status IN ('SETUP_COMPLETED', 'STOP_TRIGGERED', 'STRUCTURE_INVALIDATED', 'RS_FAILURE', 'TIME_EXIT', 'SETUP_EXPIRED', 'ENTRY_GAP_REJECTED')
                ORDER BY setup_created_as_of DESC
                LIMIT 1;
            """
            cur.execute(query, (symbol,))
            row = cur.fetchone()
            if not row:
                return False

            setup_created_as_of = row[0]
            if setup_created_as_of:
                days_elapsed = (as_of_date - setup_created_as_of.replace(tzinfo=None)).days
                if days_elapsed < COOLDOWN_AFTER_TERMINAL_DAYS:
                    logger.info(f"Symbol {symbol} in terminal cooldown ({days_elapsed} / {COOLDOWN_AFTER_TERMINAL_DAYS} days elapsed)")
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking accumulation cooldown for {symbol}: {e}")
            return False
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass
