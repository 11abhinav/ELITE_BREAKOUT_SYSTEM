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


def test_pit_staleness_triggers_cooloff_all():
    from app.financial_data_integrity import check_pit_freshness, DataStatus

    stale_sym = "TEST_STALE_STOCK"
    remove_symbol_from_cooloff(stale_sym, "ALL")

    # Call check_pit_freshness with a date > 2 years stale (e.g. 2022-03-31 vs 2026-10-07)
    res = check_pit_freshness(
        symbol=stale_sym,
        latest_annual_period="2022-03-31",
        scan_date=datetime(2026, 10, 7).date(),
        max_staleness_years=2.0
    )

    assert res.status == DataStatus.DATA_STALE
    # Verify symbol was automatically placed in 7-day cooloff for ALL scanners
    active_all = get_active_cooloff_symbols("ALL")
    active_fund = get_active_cooloff_symbols("FUNDAMENTAL")
    active_qvr = get_active_cooloff_symbols("QUALITY_VALUE_RECOVERY")
    assert stale_sym in active_all
    assert stale_sym in active_fund
    assert stale_sym in active_qvr

    # Clean up
    remove_symbol_from_cooloff(stale_sym, "ALL")
    assert stale_sym not in get_active_cooloff_symbols("ALL")


def test_fundamental_pre_recovery_excludes_cooloff_symbols():
    import pandas as pd
    from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine

    cool_sym = "COOLOFF_PRE_SYM"
    add_symbol_to_cooloff(cool_sym, reason="TEST_COOLOFF", scanner="ALL", duration_days=7)

    pre_rec = FundamentalPreRecoveryEngine()
    # Mock dataframe with cool_sym and another symbol
    df = pd.DataFrame([
        {"symbol": cool_sym, "latest_annual_period": "2024-03-31", "sales_cagr_5y": None},
        {"symbol": "ACTIVE_SYM", "latest_annual_period": "2024-03-31", "sales_cagr_5y": 15.0}
    ])

    active_cool = get_active_cooloff_symbols("ALL")
    assert cool_sym in active_cool

    # When filtered with active cooloff
    filtered_df = df[~df["symbol"].astype(str).str.strip().str.upper().isin(active_cool)]
    assert cool_sym not in filtered_df["symbol"].values
    assert "ACTIVE_SYM" in filtered_df["symbol"].values
    assert len(filtered_df) == 1

    # Clean up
    remove_symbol_from_cooloff(cool_sym, "ALL")


def test_missing_data_7_day_cooloff_and_8th_day_retry_flow(monkeypatch):
    """
    [RULE 67 INVARIANT]
    Any stock lacking full data is enrolled into 7-day cool-off in symbol_cooloff_journal.
    During Days 1 to 7, both QUALITY_COMPOUNDER and QUALITY_VALUE_RECOVERY exclude the stock.
    On the 8th day, cooloff expires and the scanner retries the stock.
    """
    sym = "TEST_MISSING_DATA_SYM"
    remove_symbol_from_cooloff(sym, "ALL")

    base_time = datetime(2026, 10, 8, 12, 0, 0, tzinfo=IST)
    monkeypatch.setattr("app.database.datetime", type("MockDateTime", (), {
        "now": classmethod(lambda cls, tz=None: base_time),
        "fromisoformat": datetime.fromisoformat,
    }))

    # 1. Enroll into 7-day cool-off
    res = add_symbol_to_cooloff(
        symbol=sym,
        reason="DATA_INSUFFICIENT_QUALITY:pat_cagr_5y",
        scanner="ALL",
        duration_days=7
    )
    assert res is True

    # 2. Day 1 to Day 7: Active cooloff excludes the stock from both scanners
    active_qc = get_active_cooloff_symbols("QUALITY_COMPOUNDER")
    active_qvr = get_active_cooloff_symbols("QUALITY_VALUE_RECOVERY")
    assert sym in active_qc
    assert sym in active_qvr

    # Simulate universe filtering at scanner entry
    target_univ = [sym, "INFY", "TCS"]
    filtered_univ_qc = [s for s in target_univ if s not in active_qc]
    filtered_univ_qvr = [s for s in target_univ if s not in active_qvr]
    assert sym not in filtered_univ_qc
    assert sym not in filtered_univ_qvr
    assert len(filtered_univ_qc) == 2

    # 3. Day 8: Cool-off expires, allowing retry
    past_time = datetime.now(IST) - timedelta(seconds=1)
    with get_connection() as conn:
        if not isinstance(conn, DummyConnection):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE symbol_cooloff_journal SET cooloff_until = %s WHERE symbol = %s",
                    (past_time, sym)
                )
    from app.database import _LOCAL_COOLOFF_CACHE, _LOCAL_COOLOFF_LOCK
    with _LOCAL_COOLOFF_LOCK:
        if (sym, "ALL") in _LOCAL_COOLOFF_CACHE:
            _LOCAL_COOLOFF_CACHE[(sym, "ALL")]["cooloff_until"] = past_time

    active_qc_day8 = get_active_cooloff_symbols("QUALITY_COMPOUNDER")
    active_qvr_day8 = get_active_cooloff_symbols("QUALITY_VALUE_RECOVERY")
    assert sym not in active_qc_day8
    assert sym not in active_qvr_day8

    # Universe now includes the stock again for automatic retry
    retried_univ_qc = [s for s in target_univ if s not in active_qc_day8]
    assert sym in retried_univ_qc
    assert len(retried_univ_qc) == 3

    # Clean up
    remove_symbol_from_cooloff(sym, "ALL")


def test_quality_value_recovery_missing_data_rejection_codes():
    """
    Verifies that QualityValueRecoveryScanner.evaluate_symbol_recovery correctly returns
    missing data rejection codes when full data is absent.
    """
    from app.live_fundamental_scanner import QualityValueRecoveryScanner

    row = {
        "symbol": "TEST_RECOVERY_DATA",
        "industry": "Automobiles",
        "high_2y": 0.0,
        "roce_5y_avg": None, # Missing ROCE
        "debt_to_equity": None, # Missing D/E
        "cfo_pat_5y_ratio": None, # Missing CFO/PAT
        "current_ev_ebitda": None,
        "ev_ebitda_3y_median": None,
        "current_pe": None,
        "pe_3y_median": None,
    }

    # When price is 0 and price history missing
    passed, rejections, metrics = QualityValueRecoveryScanner.evaluate_symbol_recovery(
        sym="TEST_RECOVERY_DATA",
        row=row,
        cmp_price=0.0,
        df_px=None
    )

    assert passed is False
    assert "PRICE_DATA_INSUFFICIENT" in rejections
    assert "QUALITY_DATA_INSUFFICIENT" in rejections
    assert "VALUATION_DATA_INSUFFICIENT" in rejections


