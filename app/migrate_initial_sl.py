import os
import sys
import logging
from psycopg2.extras import DictCursor

# Add app directory to path so we can import from database
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def migrate_initial_stop_loss():
    """
    Copies the original stop_loss into initial_stop_loss for old trades 
    where initial_stop_loss is currently NULL.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Find how many rows will be updated
                cur.execute("SELECT COUNT(*) FROM alerts WHERE initial_stop_loss IS NULL")
                count = cur.fetchone()[0]
                
                if count == 0:
                    logger.info("✅ No alerts found with NULL initial_stop_loss. Database is up to date.")
                    return
                
                logger.info(f"🔄 Migrating {count} alerts: copying stop_loss to initial_stop_loss...")
                
                # Perform the update
                cur.execute("""
                    UPDATE alerts 
                    SET initial_stop_loss = stop_loss 
                    WHERE initial_stop_loss IS NULL
                """)
                
                conn.commit()
                logger.info(f"✅ Successfully updated {cur.rowcount} alerts!")
                
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    logger.info("Starting initial_stop_loss migration script...")
    migrate_initial_stop_loss()
