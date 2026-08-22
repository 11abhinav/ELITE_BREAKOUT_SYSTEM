import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT scanner_name, started_at, total_symbols, stale_count, fresh_count, incomplete_count, lifecycle_status, error_context FROM scanner_execution_history ORDER BY started_at DESC LIMIT 15")
        rows = cur.fetchall()
        for r in rows:
            print(f"[{r[0]}] Started: {r[1]}, Total: {r[2]}, Stale: {r[3]}, Fresh: {r[4]}, Incomp: {r[5]}, Status: {r[6]}")
