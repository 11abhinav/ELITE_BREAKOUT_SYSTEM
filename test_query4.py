import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scanner_execution_history")
        print("Count in execution_history:", cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM scanner_health")
        print("Count in scanner_health:", cur.fetchone()[0])
