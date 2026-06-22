with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'r') as f:
    content = f.read()

old_code = """                db_cols = [row[0].lower() for row in cur.fetchall()]
                date_col = None
                for candidate in ["date", "run_date", "created_at", "added_at"]:
                    if candidate in db_cols:
                        date_col = candidate
                        break
                
                if not date_col:
                    return False
                
                # 3. Check row count for today
                cur.execute(f"SELECT COUNT(*) FROM daily_watchlist WHERE {date_col} = %s", (today_str,))"""

new_code = """                db_cols_raw = [row[0] for row in cur.fetchall()]
                db_cols_lower = [c.lower() for c in db_cols_raw]
                
                date_col = None
                for candidate in ["date", "run_date", "created_at", "added_at"]:
                    if candidate in db_cols_lower:
                        idx = db_cols_lower.index(candidate)
                        date_col = db_cols_raw[idx]
                        break
                
                if not date_col:
                    return False
                
                # 3. Check row count for today (quote column name to handle case sensitivity)
                cur.execute(f'SELECT COUNT(*) FROM daily_watchlist WHERE "{date_col}" = %s', (today_str,))"""

content = content.replace(old_code, new_code)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'w') as f:
    f.write(content)
