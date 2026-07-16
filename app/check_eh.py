from database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, symbol, status, exit_history FROM alerts ORDER BY id DESC LIMIT 5")
        rows = cur.fetchall()
        for r in rows:
            print(f"ID: {r[0]} | Symbol: {r[1]} | Status: {r[2]} | EH: {r[3]}")
