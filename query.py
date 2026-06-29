import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'app'))
from database import get_connection
from psycopg2.extras import RealDictCursor

try:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM multibagger_watchlist WHERE symbol = 'IDEA'")
            res = cur.fetchone()
            print("IDEA Multibagger Watchlist:", dict(res) if res else "Not found")
            
            cur.execute("SELECT * FROM multibagger_watchlist WHERE symbol = 'ICICIAMC'")
            res = cur.fetchone()
            print("ICICIAMC Multibagger Watchlist:", dict(res) if res else "Not found")
except Exception as e:
    print(e)
