import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT scanner_name, date, provider_stats FROM scanner_health")
        rows = cur.fetchall()
        for r in rows:
            print(f"Scanner: {r[0]}, Date: {r[1]}, Stats: {r[2]}")
