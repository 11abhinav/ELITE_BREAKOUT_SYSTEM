import os
from app.database import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT original_sym, mapped_sym, is_invalid FROM symbol_mappings WHERE original_sym LIKE '%YASHHV%'")
        rows = cur.fetchall()
        for r in rows:
            print(r)
