# =====================================================================================
# app/clear_poisoned_mappings.py
# CLEANUP SCRIPT FOR POISONED OR INVALID SYMBOL MAPPINGS IN POSTGRESQL
# =====================================================================================

import logging
import sys
import os

# Ensure app directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def clear_poisoned_mappings():
    """Cleans up corrupted, poisoned, or empty symbol mapping records from PostgreSQL."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete empty or corrupted symbol master mappings
                cur.execute("""
                    DELETE FROM symbol_master 
                    WHERE yahoo_ticker IS NULL 
                       OR yahoo_ticker = '' 
                       OR symbol IS NULL 
                       OR symbol = ''
                """)
                deleted_count = cur.rowcount
                conn.commit()
                if deleted_count > 0:
                    logger.info(f"🧹 [SYMBOL CLEANUP] Cleared {deleted_count} invalid/poisoned symbol mapping records.")
                else:
                    logger.info("✅ [SYMBOL CLEANUP] No poisoned symbol mappings found. Database clean.")
    except Exception as e:
        logger.warning(f"⚠️ [SYMBOL CLEANUP] Could not execute symbol mapping cleanup: {e}")

if __name__ == "__main__":
    clear_poisoned_mappings()
