from database import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM symbol_mappings")
        rows = cur.fetchall()
        for r in rows:
            print(r)
