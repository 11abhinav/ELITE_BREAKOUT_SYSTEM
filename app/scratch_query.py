import sys
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, symbol, status, is_rejected, shadow_status, shadow_pnl_pct, exit_signal, exit_reason FROM alerts WHERE symbol='BSE' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            columns = [desc[0] for desc in cur.description]
            print(dict(zip(columns, row)))
        else:
            print("Not found")
