import sys
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, entry_price, stop_loss, status, exit_price, current_price, alert_time, exit_signal FROM alerts WHERE scanner = 'ACCUMULATION_SCANNER_V1' AND alert_time >= CURRENT_DATE")
        res = cur.fetchall()
        for row in res:
            print(row)
