import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.database import (
    add_symbol_to_cooloff,
    get_active_cooloff_symbols,
    remove_symbol_from_cooloff,
    cleanup_expired_cooloffs,
    get_connection,
    DummyConnection,
)

IST = ZoneInfo("Asia/Kolkata")

def test_symbol_cooloff_journal_lifecycle():
    sym = "TESTCOOLOFF1"
    # Clean up any pre-existing entry
    remove_symbol_from_cooloff(sym, "ALL")

    # 1. Verify symbol is not initially in cool-off
    initial_cooloffs = get_active_cooloff_symbols("FUNDAMENTAL")
    assert sym not in initial_cooloffs

    # 2. Add symbol to 7-day cool-off
    res = add_symbol_to_cooloff(
        symbol=sym,
        reason="APPROVED_PROVIDERS_EXHAUSTED",
        scanner="FUNDAMENTAL",
        duration_days=7
    )
    assert res is True

    # 3. Verify symbol appears in active cool-off set
    active = get_active_cooloff_symbols("FUNDAMENTAL")
    assert sym in active

    # 4. Verify cooloff_until is set to 7 days from now
    with get_connection() as conn:
        if isinstance(conn, DummyConnection):
            from app.database import _LOCAL_COOLOFF_CACHE
            rec = _LOCAL_COOLOFF_CACHE.get((sym, "FUNDAMENTAL"))
            assert rec is not None
            cool_until = rec["cooloff_until"]
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cooloff_until FROM symbol_cooloff_journal WHERE symbol = %s AND scanner = 'FUNDAMENTAL'",
                    (sym,)
                )
                row = cur.fetchone()
                assert row is not None
                cool_until = row[0]
                if isinstance(cool_until, str):
                    cool_until = datetime.fromisoformat(cool_until.replace("Z", "+00:00"))
        
        now_dt = datetime.now(IST)
        diff = (cool_until.date() - now_dt.date()).days
        assert diff == 7 or diff == 6

    # 5. Clean up test symbol
    remove_symbol_from_cooloff(sym, "FUNDAMENTAL")
    active_after = get_active_cooloff_symbols("FUNDAMENTAL")
    assert sym not in active_after
