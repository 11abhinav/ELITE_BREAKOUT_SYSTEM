import sys
import os
from dotenv import load_dotenv
load_dotenv('app/.env')
sys.path.append(os.path.join(os.getcwd(), 'app'))
from database import get_connection
from psycopg2.extras import RealDictCursor

with get_connection() as conn:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, symbol, scanner, breakout_type, entry_price, target_1, target_2, initial_stop_loss, status FROM alerts ORDER BY id DESC LIMIT 5;")
        for row in cur.fetchall():
            print(row)
