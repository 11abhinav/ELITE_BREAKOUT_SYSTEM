import pytest
import sqlite3
from unittest.mock import MagicMock

class SqliteConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def cursor(self):
        return SqliteCursorWrapper(self.conn.cursor())
    def commit(self):
        self.conn.commit()

class SqliteCursorWrapper:
    def __init__(self, cur):
        self.cur = cur
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cur.close()
    def execute(self, sql, params=()):
        # Convert Postgres %s and ANY(%s) syntax to SQLite compatible syntax
        sql_mod = sql.replace('%s', '?')
        if 'ANY(?)' in sql_mod:
            # params contains (user_id, clean_syms_list)
            user_id = params[0]
            clean_syms = params[1]
            placeholders = ','.join(['?'] * len(clean_syms))
            sql_mod = f"DELETE FROM user_watchlists WHERE user_id = ? AND symbol IN ({placeholders})"
            new_params = (user_id, *clean_syms)
            return self.cur.execute(sql_mod, new_params)
        return self.cur.execute(sql_mod, params)
    def fetchall(self):
        return self.cur.fetchall()

def test_user_watchlist_batch_delete_and_clear_all(monkeypatch):
    sqlite_conn = sqlite3.connect(":memory:")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.execute("""
        CREATE TABLE user_watchlists (
            user_id TEXT,
            symbol TEXT,
            company_name TEXT,
            added_at TEXT,
            last_scanned_at TEXT,
            last_health_score REAL,
            last_status TEXT,
            notes TEXT,
            last_deep_analysis_at TEXT,
            deep_analysis_result TEXT,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    sqlite_conn.execute("""
        CREATE TABLE stock_analysis_master (
            symbol TEXT PRIMARY KEY,
            last_scanned_at TEXT,
            health_score REAL,
            status TEXT,
            last_deep_analysis_at TEXT,
            deep_analysis_result TEXT,
            cmp REAL,
            cmp_updated_at TEXT
        )
    """)
    # [VERSION: EARNINGS_BADGE_v1.0] earnings_calendar needed by get_user_watchlist LEFT JOIN
    sqlite_conn.execute("""
        CREATE TABLE earnings_calendar (
            symbol TEXT PRIMARY KEY,
            earnings_date TEXT,
            date_status TEXT,
            updated_at TEXT
        )
    """)


    monkeypatch.setattr("app.database.init_db", lambda: None)
    monkeypatch.setattr("app.database.get_connection", lambda: SqliteConnWrapper(sqlite_conn))

    from app.database import add_to_user_watchlist, get_user_watchlist, remove_from_user_watchlist

    user_id = "TEST_BATCH_USER_999"
    
    # 1. Clear initial state
    remove_from_user_watchlist(user_id=user_id, clear_all=True)
    assert len(get_user_watchlist(user_id=user_id)) == 0

    # 2. Add multiple stocks
    symbols = ["TCS", "INFY", "RELIANCE", "HDFCBANK", "ICICIBANK"]
    for sym in symbols:
        add_to_user_watchlist(sym, company_name=f"Company {sym}", user_id=user_id)
        
    wl = get_user_watchlist(user_id=user_id)
    assert len(wl) == 5
    
    # 3. Batch delete 2 stocks
    ok = remove_from_user_watchlist(["TCS", "INFY"], user_id=user_id)
    assert ok is True
    
    remaining = [item["symbol"] for item in get_user_watchlist(user_id=user_id)]
    assert len(remaining) == 3
    assert "TCS" not in remaining
    assert "INFY" not in remaining
    assert "RELIANCE" in remaining

    # 4. Clear all remaining stocks
    ok_clear = remove_from_user_watchlist(user_id=user_id, clear_all=True)
    assert ok_clear is True
    assert len(get_user_watchlist(user_id=user_id)) == 0

