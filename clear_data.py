import sys
import os

# Add app dir to path to import database
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

try:
    from database import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stockupdates.wealth_buy_alert WHERE source = 'MULTIBAGGER'")
            count = cur.rowcount
        conn.commit()
    print(f"✅ Successfully deleted {count} MULTIBAGGER alerts from the database!")
except Exception as e:
    print(f"❌ Error: {e}")
