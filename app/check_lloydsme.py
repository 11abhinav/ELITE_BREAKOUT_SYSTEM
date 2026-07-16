import sys
import os
sys.path.append("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, symbol, status, stop_loss, remaining_shares, shares_bought, pnl_pct, pnl_rs, exit_history FROM alerts WHERE symbol = 'LLOYDSME'")
        for row in cur.fetchall():
            print(row)
