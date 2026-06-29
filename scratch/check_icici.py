import sys
sys.path.append('app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT fair_value, valuation_method, target_multiple, current_multiple, peer_multiple FROM stockupdates.watchlist WHERE symbol = 'ICICIAMC'")
        print(cur.fetchone())
