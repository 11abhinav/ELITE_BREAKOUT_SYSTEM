import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT scanner_name, date, total_count, processed_count, outcome, error_msg, provider_stats FROM scanner_health ORDER BY date DESC LIMIT 20")
        rows = cur.fetchall()
        for r in rows:
            print(f"Scanner: {r[0]}, Date: {r[1]}, Total: {r[2]}, Processed: {r[3]}, Outcome: {r[4]}")
            print(f"  Error: {r[5]}")
            print(f"  Stats: {r[6]}")
