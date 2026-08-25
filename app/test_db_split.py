from database import get_connection
from psycopg2.extras import RealDictCursor

with get_connection() as conn:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM wealth_buy_alert WHERE symbol = 'TDPOWERSYS' ORDER BY id DESC LIMIT 1")
        print(cur.fetchone())
        
        cur.execute("SELECT * FROM wealth_sell_alert WHERE symbol = 'TDPOWERSYS' ORDER BY id DESC LIMIT 1")
        print(cur.fetchone())
