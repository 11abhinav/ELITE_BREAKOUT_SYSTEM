import sys
import os
sys.path.insert(0, os.path.abspath('app'))
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("UPDATE stock_analysis_master SET symbol = 'TMPV' WHERE symbol = 'TATAMOTORS'")
        print(f"Updated {cur.rowcount} rows in stock_analysis_master")
    conn.commit()
