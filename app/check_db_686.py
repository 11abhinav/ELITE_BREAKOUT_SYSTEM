import json
from dotenv import load_dotenv
load_dotenv()
from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, exit_history, status FROM alerts WHERE id = 686")
        row = cur.fetchone()
        if row:
            print(f"Alert 686:")
            print(f"Status: {row[2]}")
            print(f"Exit History: {row[1]}")
        else:
            print("Alert 686 not found")
