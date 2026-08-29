import os
import sys
sys.path.insert(0, os.path.join(os.getcwd(), 'app'))

from database import get_connection

def main():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Print scanner_control
                cur.execute("SELECT scanner_name, enabled, paused, stop_requested FROM scanner_control")
                rows = cur.fetchall()
                print("--- SCANNER CONTROL ---")
                for r in rows:
                    print(f"Name: {r[0]} | Enabled: {r[1]} | Paused: {r[2]} | Stop Requested: {r[3]}")

                # 2. Print scanner_health
                cur.execute("SELECT scanner_name, status, last_success, error_msg, duration_seconds, active_run_id FROM scanner_health")
                rows = cur.fetchall()
                print("\n--- SCANNER HEALTH ---")
                for r in rows:
                    print(f"Name: {r[0]} | Status: {r[1]} | Last Success: {r[2]} | Duration: {r[4]}s | Active Run ID: {r[5]}")

    except Exception as e:
        print(f"Error querying DB: {e}")

if __name__ == "__main__":
    main()
