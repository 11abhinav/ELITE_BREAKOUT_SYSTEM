import os
import sys

env_file = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/.env"
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip("'\"")

sys.path.insert(0, "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")

from database import get_connection

try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stop_loss, initial_stop_loss FROM alerts WHERE id = 686")
            row = cur.fetchone()
            if row:
                print(f"Alert 686 -> stop_loss: {row[0]}, initial_stop_loss: {row[1]}")
            else:
                print("Alert 686 not found")
except Exception as e:
    print(f"Error: {e}")
