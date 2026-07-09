from app.database import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, status, notes FROM stockupdates.watchlist LIMIT 5")
        print(cur.fetchall())
