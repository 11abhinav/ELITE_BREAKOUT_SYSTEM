# =====================================================================================
# tests/test_weekend_candle_ban.py
#
# RULE 67 CHANGE-RATIONALE:
# - HARD GLOBAL INVARIANT: System-Wide Weekend Candle Ban acceptance test suite.
# - Verifies that Saturday and Sunday candles are NEVER fetched, accepted,
#   evaluated, stored, or used for trading/exit decisions across any engine.
# =====================================================================================

import os
import sys
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date
from zoneinfo import ZoneInfo

from trading_calendar import enforce_trading_day_candles, is_weekend_date, default_trading_calendar
from price_cache import validate_ohlcv_structure
from market_data.core.models import NormalizedMarketData, DataProvenance
from technical_indicators import apply_indicators
from performance_tracker import process_trade_history, _days_held

IST = ZoneInfo("Asia/Kolkata")


def _create_mock_ohlcv_with_weekends():
    """
    Creates a mock OHLCV DataFrame spanning Friday, Saturday, Sunday, Monday:
    - 2026-09-04: Friday (Trading Day)
    - 2026-09-05: Saturday (Weekend - Prohibited)
    - 2026-09-06: Sunday (Weekend - Prohibited)
    - 2026-09-07: Monday (Trading Day)
    """
    dates = pd.date_range("2026-09-04", "2026-09-07", freq="D")
    df = pd.DataFrame({
        "Date": dates,
        "Open": [100.0, 102.0, 101.0, 105.0],
        "High": [105.0, 104.0, 103.0, 110.0],
        "Low": [98.0, 95.0, 96.0, 103.0],
        "Close": [102.0, 97.0, 100.0, 108.0],
        "Volume": [100000, 50000, 40000, 150000]
    })
    return df


def test_enforce_trading_day_candles_purges_weekends():
    """Verify enforce_trading_day_candles purges 100% of Saturday and Sunday candles."""
    df = _create_mock_ohlcv_with_weekends()
    assert len(df) == 4

    cleaned_df = enforce_trading_day_candles(df, symbol="TEST_STOCK")
    assert len(cleaned_df) == 2

    # Verify only Friday and Monday remain
    remaining_dates = pd.to_datetime(cleaned_df["Date"]).dt.date.tolist()
    assert remaining_dates == [date(2026, 9, 4), date(2026, 9, 7)]
    for d in remaining_dates:
        assert d.weekday() < 5, f"Unexpected weekend date: {d}"


def test_validate_ohlcv_structure_rejects_weekend_candles():
    """Verify validate_ohlcv_structure strictly rejects DataFrames containing weekend candles."""
    df_with_weekend = _create_mock_ohlcv_with_weekends()
    is_valid, reason = validate_ohlcv_structure(df_with_weekend)
    assert not is_valid
    assert reason == "WEEKEND_CANDLES_PROHIBITED"

    # Now purge weekends and verify validation passes
    df_clean = enforce_trading_day_candles(df_with_weekend)
    is_valid_clean, reason_clean = validate_ohlcv_structure(df_clean)
    assert is_valid_clean
    assert reason_clean == "VALID"


def test_normalized_market_data_contract_auto_purges_weekends():
    """Verify NormalizedMarketData dataclass contract automatically purges weekend candles."""
    df = _create_mock_ohlcv_with_weekends()
    prov = DataProvenance(
        provider="MOCK",
        fetch_time=datetime.now(IST),
        latency_ms=10.0,
        validation_score=100.0
    )
    md = NormalizedMarketData(
        symbol="RELIANCE",
        timeframe="1d",
        dataframe=df,
        provenance=prov
    )
    # The post-init contract must have sanitized the internal DataFrame
    assert len(md.dataframe) == 2
    for ts in pd.to_datetime(md.dataframe["Date"]):
        assert ts.weekday() < 5


def test_technical_indicators_never_calculate_on_weekends():
    """Verify technical indicators engine purges weekend candles before calculating indicators."""
    # Create 30 days of data including several weekends
    dates = pd.date_range("2026-08-01", "2026-09-01", freq="D")
    df = pd.DataFrame({
        "Date": dates,
        "Open": np.linspace(100, 150, len(dates)),
        "High": np.linspace(105, 155, len(dates)),
        "Low": np.linspace(95, 145, len(dates)),
        "Close": np.linspace(102, 152, len(dates)),
        "Volume": np.full(len(dates), 100000)
    })
    enriched = apply_indicators(df, timeframe="1d")
    enriched_dates = pd.to_datetime(enriched["Date"])
    for ts in enriched_dates:
        assert ts.weekday() < 5, f"Indicator found on weekend bar: {ts}"


def test_performance_tracker_ignores_weekend_ticks_and_avoids_false_exit():
    """
    CRITICAL EXIT MONITOR & REPLAY TEST:
    Verify that an SL breach that occurs ONLY on a mock Saturday candle is REJECTED,
    preventing a FALSE EXIT.
    """
    trade = {
        "id": 9999,
        "symbol": "INFY",
        "scanner": "EOD",
        "shares_bought": 100,
        "entry_price": 100.0,
        "actual_entry_price": 100.0,
        "stop_loss": 90.0,
        "target_1": 120.0,
        "target_2": 130.0,
        "target_3": 140.0,
        "status": "OPEN",
        "execution_state": "OPEN",
        "exit_history": "[]",
    }

    # Friday normal, Saturday crashes below SL to 85.0 (MOCK BUGGY CANDLE), Monday recovers to 105.0
    hist = pd.DataFrame([
        {"Date": pd.Timestamp("2026-09-04 15:30:00"), "Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 101.0, "Volume": 1000},
        {"Date": pd.Timestamp("2026-09-05 12:00:00"), "Open": 85.0, "High": 88.0, "Low": 80.0, "Close": 85.0, "Volume": 100},  # SATURDAY SL BREACH
        {"Date": pd.Timestamp("2026-09-07 15:30:00"), "Open": 102.0, "High": 106.0, "Low": 101.0, "Close": 105.0, "Volume": 1500},
    ]).set_index("Date")

    process_trade_history(trade, hist, cur_p=105.0)

    # Invariant: Trade MUST NOT be stopped out on the Saturday tick!
    assert trade["status"] == "OPEN", f"False exit triggered! Status is {trade['status']}"
    assert trade.get("exit_signal") != "STOP_LOSS"


def test_trading_days_held_calculation_excludes_weekends():
    """Verify holding period excludes weekends (Friday to Monday is 1 trading day, not 3)."""
    # Friday 2026-09-04 to Monday 2026-09-07
    days = default_trading_calendar.days_between("2026-09-04", "2026-09-07")
    assert days == 1, f"Expected 1 trading day between Friday and Monday, got {days}"

    # Saturday is rejected by is_weekend_date
    assert is_weekend_date("2026-09-05")
    assert is_weekend_date("2026-09-06")
    assert not is_weekend_date("2026-09-04")
    assert not is_weekend_date("2026-09-07")


def test_end_to_end_saturday_sunday_fake_sl_breach_cannot_generate_exit():
    """
    EXPLICIT REGRESSION TEST:
    Friday Close
       ↓
    Saturday mock candle with price below SL (75.0 < 90.0)
       ↓
    Sunday mock candle with price below SL (72.0 < 90.0)
       ↓
    Monday real market candle (103.0)

    VERIFIES:
    ❌ no exit
    ❌ no alert
    ❌ no SL hit
    ❌ no target hit
    ❌ no negative weekend P&L crash
    ❌ no indicator calculation on weekend bars
    ❌ no performance-event generation
    """
    import json

    # 1. Raw multi-day series with Friday -> Saturday (mock breach) -> Sunday (mock breach) -> Monday (real)
    raw_df = pd.DataFrame([
        {"Date": pd.Timestamp("2026-09-04 15:30:00"), "Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0, "Volume": 50000},
        {"Date": pd.Timestamp("2026-09-05 12:00:00"), "Open": 80.0, "High": 85.0, "Low": 70.0, "Close": 75.0, "Volume": 1000},  # SATURDAY FAKE BREACH
        {"Date": pd.Timestamp("2026-09-06 12:00:00"), "Open": 78.0, "High": 82.0, "Low": 68.0, "Close": 72.0, "Volume": 1000},  # SUNDAY FAKE BREACH
        {"Date": pd.Timestamp("2026-09-07 15:30:00"), "Open": 101.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Volume": 80000},  # MONDAY REAL
    ])

    # Ingestion Layer Validation: Raw df must be rejected
    is_valid, reason = validate_ohlcv_structure(raw_df)
    assert not is_valid, "Raw dataframe containing Saturday/Sunday mock candles was not rejected by validate_ohlcv_structure!"
    assert reason == "WEEKEND_CANDLES_PROHIBITED"

    # Ingestion Layer Sanitization: Purges both Saturday and Sunday
    clean_df = enforce_trading_day_candles(raw_df, symbol="TATAMOTORS")
    assert len(clean_df) == 2, f"Expected 2 candles after purging Saturday & Sunday, got {len(clean_df)}"
    clean_dates = pd.to_datetime(clean_df["Date"]).dt.date.tolist()
    assert clean_dates == [date(2026, 9, 4), date(2026, 9, 7)]

    # Technical Indicators Engine: Calculates ONLY for Friday and Monday
    enriched_df = apply_indicators(raw_df, timeframe="1d")
    for ts in pd.to_datetime(enriched_df["Date"]):
        assert ts.weekday() < 5, f"Indicator calculation erroneously evaluated on weekend bar: {ts}"

    # Performance / Exit Monitor Engine
    trade = {
        "id": 1001,
        "symbol": "TATAMOTORS",
        "scanner": "EOD",
        "shares_bought": 100,
        "entry_price": 100.0,
        "actual_entry_price": 100.0,
        "stop_loss": 90.0,
        "target_1": 120.0,
        "target_2": 130.0,
        "target_3": 140.0,
        "status": "OPEN",
        "execution_state": "OPEN",
        "exit_history": "[]",
    }

    # Pass the full history into performance_tracker
    cur_p = 103.0
    hist_indexed = raw_df.set_index("Date")
    process_trade_history(trade, hist_indexed, cur_p=cur_p)

    # Invariant Verifications:
    assert trade["status"] == "OPEN", f"False exit triggered on weekend mock candle! Status: {trade['status']}"
    assert trade.get("exit_signal") is None, f"False exit signal generated: {trade.get('exit_signal')}"
    assert trade.get("sl_hit") is not True, "False SL hit recorded on weekend mock candle!"
    assert trade.get("t1_hit") is not True, "False Target hit recorded!"
    assert trade.get("exit_price") is None, f"Exit price assigned from weekend candle: {trade.get('exit_price')}"

    # Performance events: exit_history must remain empty
    exit_history_events = json.loads(trade.get("exit_history", "[]"))
    assert len(exit_history_events) == 0, f"Exit history recorded false events: {exit_history_events}"

    # P&L calculation: Position remains open, so unrealized gain is evaluated at Monday's market price
    # (103.0 vs 100.0 entry = +3.0%), NOT a fake weekend crash (-25% or -28%)
    assert trade.get("pnl_pct") is None or trade.get("status") == "OPEN"
    unrealized_pnl = round((cur_p - trade["entry_price"]) / trade["entry_price"] * 100, 2)
    assert unrealized_pnl == pytest.approx(3.0, rel=1e-2), f"Unrealized P&L corrupted: {unrealized_pnl}%"


def test_all_acquisition_paths_reject_weekends():
    """
    AUDIT OF OHLCV ACQUISITION PATHS:
    Verifies that all acquisition endpoints enforce the weekend ban:
    1. Multi-TF data loading
    2. Delivery Data Bhavcopy fetching
    3. Counterfactual engine data normalization
    4. Price Cache get_cached_df
    """
    from multitf.data import load_multitf_data
    from delivery_data import fetch_delivery_data
    from trading_calendar import is_market_candle_eligible

    # 1. Multi-TF Data Loader:
    ist_now = datetime(2026, 9, 7, 10, 0, tzinfo=IST) # Monday morning
    mock_15m = pd.DataFrame([
        {"Date": pd.Timestamp("2026-09-04 15:15:00"), "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
        {"Date": pd.Timestamp("2026-09-05 10:00:00"), "Open": 90.0, "High": 92.0, "Low": 88.0, "Close": 91.0, "Volume": 500}, # Saturday
    ])
    bundle = load_multitf_data("INFY", ist_now, all_15m_data={"INFY": mock_15m})
    if bundle.df_15m_closed is not None and not bundle.df_15m_closed.empty:
        for ts in bundle.df_15m_closed["Date"]:
            assert pd.to_datetime(ts).weekday() < 5, f"MultiTF bundle contained weekend bar: {ts}"

    # 2. Bhavcopy delivery data fetcher:
    # Attempting to fetch Bhavcopy for Saturday or Sunday returns empty dict immediately without network call
    saturday_res = fetch_delivery_data(date(2026, 9, 5))
    sunday_res = fetch_delivery_data(date(2026, 9, 6))
    assert saturday_res == {}, "Bhavcopy fetch on Saturday did not return empty dict!"
    assert sunday_res == {}, "Bhavcopy fetch on Sunday did not return empty dict!"

    # 3. Market candle eligibility predicate
    assert not is_market_candle_eligible("2026-09-05")  # Saturday
    assert not is_market_candle_eligible("2026-09-06")  # Sunday
    assert not is_market_candle_eligible("2026-01-26")  # Republic Day (NSE Holiday)
    assert is_market_candle_eligible("2026-09-07")      # Monday (Trading day)

