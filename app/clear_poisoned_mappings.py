import sys
import os
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clear_poisoned_mappings():
    """
    Clears all persistent BSE mappings in symbol_mappings where the original symbol
    is a standard NSE symbol. This prevents Fyers quotes from failing due to symbol-based
    BSE ticker requests.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # First, check how many matching mappings we have
            cur.execute("""
                SELECT original_sym, mapped_sym 
                FROM symbol_mappings 
                WHERE mapping_type = 'BSE' 
                  AND is_invalid = FALSE
                  AND mapped_sym LIKE '%.BO'
                  AND original_sym !~ '^[0-9]+$'
            """)
            poisoned = cur.fetchall()
            
            if not poisoned:
                logger.info("✅ No poisoned persistent BSE mappings found.")
                return
                
            logger.info(f"🗑️ Found {len(poisoned)} poisoned BSE mappings in the database.")
            for row in poisoned:
                logger.info(f"  Mapping to remove: {row[0]} -> {row[1]}")
                
            # Invalidate them
            cur.execute("""
                UPDATE symbol_mappings 
                SET is_invalid = TRUE 
                WHERE mapping_type = 'BSE' 
                  AND is_invalid = FALSE
                  AND mapped_sym LIKE '%.BO'
                  AND original_sym !~ '^[0-9]+$'
            """)
            
            updated_count = cur.rowcount
            conn.commit()
            logger.info(f"✅ Successfully invalidated {updated_count} poisoned BSE symbol mappings in PostgreSQL.")

if __name__ == "__main__":
    clear_poisoned_mappings()
