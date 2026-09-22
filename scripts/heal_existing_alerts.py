#!/usr/bin/env python3
"""
scripts/heal_existing_alerts.py
Standalone database audit and repair script for all existing/raised alerts.
Heals:
1. Alerts with actual_entry_price populated that are stuck in PENDING_ENTRY -> sets execution_state='OPEN', status='OPEN'
2. Market/legacy alerts stuck in PENDING_ENTRY -> sets execution_state='OPEN', status='OPEN', actual_entry_price=entry_price
3. OPEN alerts missing actual_entry_price -> populates actual_entry_price=entry_price
4. Terminal alerts (WIN/LOSS/CLOSED) stuck in PENDING_ENTRY -> aligns execution_state=status
"""

import sys
import os
import logging

# Ensure app directory is on PYTHONPATH
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("heal_existing_alerts")


def heal_all_raised_alerts():
    from database import get_connection, init_db
    init_db()

    logger.info("🔍 [HEAL_ALERTS] Starting full audit of existing alerts in PostgreSQL...")

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Filled positions stuck in PENDING_ENTRY
            cur.execute("""
                SELECT id, symbol, entry_price, actual_entry_price, execution_state, entry_mode
                FROM alerts
                WHERE execution_state = 'PENDING_ENTRY' AND actual_entry_price IS NOT NULL;
            """)
            filled_pending = cur.fetchall()
            if filled_pending:
                syms = [f"#{r[0]} {r[1]} (₹{r[3]})" for r in filled_pending]
                logger.info(f"🔄 Healing {len(filled_pending)} filled alerts from PENDING_ENTRY to OPEN: {', '.join(syms)}")
                cur.execute("""
                    UPDATE alerts
                    SET execution_state = 'OPEN',
                        status = CASE WHEN status = 'PENDING_ENTRY' THEN 'OPEN' ELSE status END,
                        entry_mode = CASE WHEN entry_mode = 'LEGACY_UNKNOWN' THEN 'CONFIRMED_BUY' ELSE entry_mode END
                    WHERE execution_state = 'PENDING_ENTRY' AND actual_entry_price IS NOT NULL;
                """)

            # 2. Market / legacy alerts stuck in PENDING_ENTRY
            cur.execute("""
                SELECT id, symbol, entry_price, entry_mode
                FROM alerts
                WHERE execution_state = 'PENDING_ENTRY' AND (entry_mode IN ('MARKET', 'LEGACY_UNKNOWN') OR entry_mode IS NULL);
            """)
            market_pending = cur.fetchall()
            if market_pending:
                syms = [f"#{r[0]} {r[1]} ({r[3]})" for r in market_pending]
                logger.info(f"🔄 Healing {len(market_pending)} market/legacy alerts from PENDING_ENTRY to OPEN: {', '.join(syms)}")
                cur.execute("""
                    UPDATE alerts
                    SET execution_state = 'OPEN',
                        status = CASE WHEN status = 'PENDING_ENTRY' THEN 'OPEN' ELSE status END,
                        actual_entry_price = COALESCE(actual_entry_price, entry_price)
                    WHERE execution_state = 'PENDING_ENTRY' AND (entry_mode IN ('MARKET', 'LEGACY_UNKNOWN') OR entry_mode IS NULL);
                """)

            # 3. OPEN alerts missing actual_entry_price
            cur.execute("""
                SELECT id, symbol, entry_price
                FROM alerts
                WHERE execution_state = 'OPEN' AND actual_entry_price IS NULL AND entry_price IS NOT NULL;
            """)
            missing_open_price = cur.fetchall()
            if missing_open_price:
                syms = [f"#{r[0]} {r[1]}" for r in missing_open_price]
                logger.info(f"🔄 Backfilling actual_entry_price for {len(missing_open_price)} OPEN alerts: {', '.join(syms)}")
                cur.execute("""
                    UPDATE alerts
                    SET actual_entry_price = entry_price
                    WHERE execution_state = 'OPEN' AND actual_entry_price IS NULL AND entry_price IS NOT NULL;
                """)

            # 4. Terminal alerts with PENDING_ENTRY execution_state
            cur.execute("""
                SELECT id, symbol, status, execution_state
                FROM alerts
                WHERE status IN ('WIN', 'LOSS', 'CLOSED', 'EXPIRED') AND execution_state = 'PENDING_ENTRY';
            """)
            terminal_pending = cur.fetchall()
            if terminal_pending:
                syms = [f"#{r[0]} {r[1]} ({r[2]})" for r in terminal_pending]
                logger.info(f"🔄 Aligning execution_state for {len(terminal_pending)} closed alerts: {', '.join(syms)}")
                cur.execute("""
                    UPDATE alerts
                    SET execution_state = status
                    WHERE status IN ('WIN', 'LOSS', 'CLOSED', 'EXPIRED') AND execution_state = 'PENDING_ENTRY';
                """)

            conn.commit()

    total_healed = len(filled_pending) + len(market_pending) + len(missing_open_price) + len(terminal_pending)
    logger.info(f"✅ [HEAL_ALERTS] Audit complete. Total alerts healed/synchronized: {total_healed}")

    if total_healed > 0:
        try:
            from performance_tracker import trigger_performance_rebuild
            trigger_performance_rebuild()
            logger.info("⚡ Triggered performance rebuild to refresh metrics with healed states.")
        except Exception as exc:
            logger.debug(f"Could not trigger rebuild: {exc}")

    return total_healed


if __name__ == "__main__":
    heal_all_raised_alerts()
