import sys
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        print("--- RECENT ACCUMULATION RUNS ---")
        try:
            cur.execute("SELECT id, run_id, started_at, completed_at, status, duration_seconds FROM accumulation_runs ORDER BY id DESC LIMIT 5")
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            print(f"Error querying accumulation_runs: {e}")
            
        print("\n--- RECENT ACCUMULATION HEALTH ---")
        try:
            cur.execute("SELECT id, run_id, status, requested_symbols, processed_symbols, valid_symbols, candidates, alerts FROM accumulation_health ORDER BY id DESC LIMIT 5")
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            print(f"Error querying accumulation_health: {e}")
            
        print("\n--- RECENT ACCUMULATION ALERTS ---")
        try:
            cur.execute("SELECT id, run_id, symbol, state, tradable, score, created_at FROM accumulation_alerts ORDER BY id DESC LIMIT 15")
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            print(f"Error querying accumulation_alerts: {e}")

        print("\n--- ALERTS FROM ACCUMULATION SCANNER ---")
        try:
            cur.execute("SELECT id, symbol, breakout_type, alert_date, scanner, category, entry_price, status, is_rejected FROM alerts WHERE scanner = 'ACCUMULATION' ORDER BY id DESC LIMIT 15")
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            print(f"Error querying alerts: {e}")
