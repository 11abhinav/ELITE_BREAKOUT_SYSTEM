from app.database import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT scanner_name, status, provider_stats FROM scanner_health")
        rows = cur.fetchall()
        for r in rows:
            print(r)
