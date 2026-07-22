import pytest
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from watchlist_cache import get_watchlist, validate_watchlist_freshness, StaleWatchlistError
from database import is_symbol_in_failed_reversal_cooldown, check_recent_alert
from confluence_engine import evaluate_confluence_shortlist
from near_miss_tracker import log_near_miss, init_near_miss_schema

IST = ZoneInfo("Asia/Kolkata")

def test_ambiguous_sl_hit_does_not_trigger_cooldown(mocker):
    """Verify that a trade with exit_reason='AMBIGUOUS_SL_HIT' does NOT trigger reversal cooldown."""
    mock_row = ("LOSS", datetime.now(IST).date(), "AMBIGUOUS_SL_HIT")
    mock_cursor = mocker.MagicMock()
    mock_cursor.fetchone.return_value = mock_row
    
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    mocker.patch("database.get_connection", return_value=mock_conn)
    mocker.patch("database.init_db")
    
    in_cooldown = is_symbol_in_failed_reversal_cooldown("RELIANCE", cooldown_days=30)
    assert in_cooldown is False

def test_confluence_recalibrated_threshold(mocker, tmp_path):
    """Verify that Confluence Engine evaluates candidates with FM_Score >= 70."""
    watchlist_df = pd.DataFrame([{
        "Stock": "TCS",
        "FM_Score": 72.0
    }])
    parquet_path = tmp_path / "test_watchlist.parquet"
    watchlist_df.to_parquet(parquet_path)
    
    mocker.patch("confluence_engine.WATCHLIST_PATH", str(parquet_path))
    mocker.patch("confluence_engine.compute_nifty_rs_rating", return_value={"TCS": 85.0})
    
    mock_alert = (1, "TCS", "EOD", "Breakout", 3000.0, 2900.0, 3200.0, 3400.0, 85, "BULL")
    mock_cursor = mocker.MagicMock()
    mock_cursor.fetchall.return_value = [mock_alert]
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    mocker.patch("confluence_engine.get_connection", return_value=mock_conn)
    
    matches = evaluate_confluence_shortlist("2026-07-22")
    assert len(matches) == 1
    assert matches[0]["symbol"] == "TCS"
    assert matches[0]["fm_score"] == 72.0

def test_score_upgrade_dedup_override(mocker):
    """Verify that check_recent_alert allows re-alerting if new_score >= old_score + 5."""
    mock_row = (80,)  # old score is 80
    mock_cursor = mocker.MagicMock()
    mock_cursor.fetchone.return_value = mock_row
    
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    mocker.patch("database.get_connection", return_value=mock_conn)
    
    # New score 83 (< 80 + 5) -> duplicate returns True
    is_dup_same = check_recent_alert("RELIANCE", "EOD", "Breakout", lookback_minutes=60, new_score=83)
    assert is_dup_same is True
    
    # New score 86 (>= 80 + 5) -> score-upgrade override returns False (allowed)
    is_dup_upgraded = check_recent_alert("RELIANCE", "EOD", "Breakout", lookback_minutes=60, new_score=86)
    assert is_dup_upgraded is False

def test_near_miss_tracker_logging(mocker):
    """Verify near_miss_tracker executes without exception."""
    mocker.patch("near_miss_tracker.init_db")
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mocker.patch("near_miss_tracker.get_connection", return_value=mock_conn)
    
    # Obs 72.0 vs Thresh 75.0 (Delta 4%)
    log_near_miss(
        symbol="INFY",
        scanner="EOD",
        breakout_type="Breakout",
        gate_name="FM_Score",
        observed_value=72.0,
        threshold_value=75.0,
        score=72
    )
