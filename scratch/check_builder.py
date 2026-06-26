from app.database import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM scanner_health WHERE scanner_name = 'DAILY_BUILDER'")
        print(cur.fetchone())
