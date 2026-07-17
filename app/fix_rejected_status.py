import logging
from database import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_rejected_alerts():
    """
    Finds all alerts where is_rejected is True but status is not 'REJECTED'
    and fixes them by setting status to 'REJECTED' and clearing false PnL/exit values.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # First see how many we need to fix
            cur.execute("""
                SELECT id, symbol, status 
                FROM alerts 
                WHERE is_rejected = TRUE AND status != 'REJECTED'
            """)
            corrupted = cur.fetchall()
            
            if not corrupted:
                logger.info("✅ No corrupted rejected alerts found. Database is clean.")
                return
                
            logger.info(f"🛠️ Found {len(corrupted)} corrupted rejected alerts. Fixing...")
            
            # Fix them
            cur.execute("""
                UPDATE alerts 
                SET status = 'REJECTED',
                    pnl_pct = NULL,
                    pnl_rs = NULL,
                    exit_price = NULL,
                    closed_at = NULL,
                    exit_history = NULL,
                    execution_state = 'REJECTED',
                    remaining_shares = 0
                WHERE is_rejected = TRUE AND status != 'REJECTED'
            """)
            
            fixed_count = cur.rowcount
            conn.commit()
            
            logger.info(f"✅ Successfully fixed {fixed_count} rejected alerts in the database.")

if __name__ == "__main__":
    fix_rejected_alerts()
