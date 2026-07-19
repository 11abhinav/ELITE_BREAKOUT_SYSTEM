import pytest
from unittest.mock import patch, MagicMock, call
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from app.multi_tf_scanner import run_lower_tf_phase
from app.validation.report import MarketData
from tests.factories import make_price_history
import logging

IST = ZoneInfo("Asia/Kolkata")

@pytest.fixture(autouse=True)
def setup_logging(caplog):
    caplog.set_level(logging.INFO)

@pytest.fixture
def base_history():
    return make_price_history().build()

def make_test_watchlist_item(symbol, state, breakout_level=100.0):
    return {
        "symbol": symbol,
        "current_state": state,
        "category": "High Momentum",
        "breakout_level": breakout_level,
        "trigger_level": breakout_level,
        "invalidation_level": breakout_level * 0.95,
        "armed_at": "2023-01-01 10:00:00",
        "context_json": "{}"
    }

@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.multi_tf_scanner.check_recent_alert')
@patch('app.multi_tf_scanner.upsert_breakout_watchlist')
@patch('database.save_candidate')
@patch('database.save_alert_if_new')
@patch('database.get_pledge_map')
@patch('database.get_latest_weights')
@patch('database.mark_breakout_watchlist_cooldown')
def test_pipeline_e2e_happy_path(
    mock_cooldown,
    mock_weights,
    mock_pledge,
    mock_db_save_alert,
    mock_db_save_candidate,
    mock_upsert, 
    mock_check_recent, 
    mock_fetch, 
    mock_get_wl,
    base_history
):
    """
    E2E Test: A valid breakout goes through the entire pipeline:
    Scanner -> SL/Target -> Scoring -> OpportunityManager -> Funded Alert.
    Verifies that the audit trail is generated correctly.
    """
    # 1. Setup Watchlist
    mock_get_wl.return_value = [make_test_watchlist_item("TEST.NS", "ENTRY_READY", 100.0)]
    mock_check_recent.return_value = False
    mock_pledge.return_value = {"TEST.NS": 0.0}
    mock_weights.return_value = {"weights": {}}
    
    # 2. Setup Data (5m TF trigger)
    # The last candle triggers the breakout > 100.0
    history_5m = base_history.copy()
    prev_idx = history_5m.index[-2]
    last_idx = history_5m.index[-1]
    
    # Make previous candle red
    history_5m.loc[prev_idx, 'Open'] = 100.5
    history_5m.loc[prev_idx, 'High'] = 100.8
    history_5m.loc[prev_idx, 'Close'] = 99.8
    
    # Make current candle green and engulfing previous candle
    history_5m.loc[last_idx, 'Open'] = 99.5
    history_5m.loc[last_idx, 'Low'] = 99.0
    history_5m.loc[last_idx, 'Close'] = 101.5
    history_5m.loc[last_idx, 'High'] = 102.0
    history_5m.loc[last_idx, 'Volume'] = 500_000
    
    history_5m["RSI"] = 65.0
    history_5m["Volume"] = history_5m["Volume"] * 2  # Boost volume
    
    # Inject actual structural swing points earlier in the history
    # so apply_indicators calculates SWING_HIGH and SWING_LOW correctly
    swing_high_idx = history_5m.index[-6]
    history_5m.loc[swing_high_idx, 'High'] = 150.0
    
    swing_low_idx = history_5m.index[-10]
    history_5m.loc[swing_low_idx, 'Low'] = 95.0
    
    mock_fetch.return_value = {"TEST.NS": history_5m}
    
    # We must patch the delivery data and scoring engine internally if needed, 
    # but run_lower_tf_phase will just execute the whole chain.
    with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
        run_lower_tf_phase(run_once=True, is_test_mode=False)
        
    # 3. Verify Invariants
    # Invariant: Alert is created
    assert mock_db_save_alert.call_count == 1
    
    kwargs = mock_db_save_alert.call_args[1]
    assert kwargs['symbol'] == "TEST.NS"
    assert kwargs['scanner'] == "MULTI_TF"
    assert kwargs['stop_loss'] < 101.5 # SL must be lower than entry
    assert kwargs['target_1'] > 101.5  # Target must be higher than entry
    
    # Ensure audit trail / context was generated
    assert "portfolio_funded" in kwargs['context']
    assert kwargs['context']["portfolio_funded"] is True
    
    # Invariant: A candidate record should also have been created for the funded alert
    assert mock_db_save_candidate.call_count == 1
    
    cand_kwargs = mock_db_save_candidate.call_args[1]
    assert cand_kwargs['symbol'] == "TEST.NS"
    assert cand_kwargs['status'] == "FUNDED"


@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.multi_tf_scanner.check_recent_alert')
@patch('app.multi_tf_scanner.upsert_breakout_watchlist')
@patch('database.save_candidate')
@patch('database.save_alert_if_new')
@patch('database.get_pledge_map')
@patch('database.get_latest_weights')
@patch('database.mark_breakout_watchlist_cooldown')
def test_pipeline_e2e_invalid_market_data_blocked(
    mock_cooldown,
    mock_weights,
    mock_pledge,
    mock_db_save_alert,
    mock_db_save_candidate,
    mock_upsert, 
    mock_check_recent, 
    mock_fetch, 
    mock_get_wl,
    base_history
):
    """
    E2E Test: If market data is missing/invalid, the pipeline completely blocks it.
    No alert is emitted, no candidate is scored.
    """
    mock_get_wl.return_value = [make_test_watchlist_item("BAD_DATA.NS", "ENTRY_READY", 100.0)]
    
    # Return empty dictionary indicating data fetch failure
    mock_fetch.return_value = {}
    
    run_lower_tf_phase(run_once=True, is_test_mode=False)
    
    # 3. Verify Invariants
    assert mock_db_save_alert.call_count == 0
    assert mock_db_save_candidate.call_count == 0


@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.multi_tf_scanner.check_recent_alert')
@patch('app.multi_tf_scanner.upsert_breakout_watchlist')
@patch('database.save_candidate')
@patch('database.save_alert_if_new')
@patch('database.get_pledge_map')
@patch('database.get_latest_weights')
@patch('database.mark_breakout_watchlist_cooldown')
def test_pipeline_e2e_rejection_audit_trail(
    mock_cooldown,
    mock_weights,
    mock_pledge,
    mock_db_save_alert,
    mock_db_save_candidate,
    mock_upsert, 
    mock_check_recent, 
    mock_fetch, 
    mock_get_wl,
    base_history
):
    """
    E2E Test: A breakout that fails portfolio allocation is properly persisted 
    as a REJECTED_CAPITAL candidate for audit purposes.
    """
    mock_get_wl.return_value = [make_test_watchlist_item("WEAK.NS", "ENTRY_READY", 100.0)]
    mock_check_recent.return_value = False
    mock_pledge.return_value = {"WEAK.NS": 0.0}
    mock_weights.return_value = {"weights": {}}
    
    history_5m = base_history.copy()
    prev_idx = history_5m.index[-2]
    last_idx = history_5m.index[-1]
    
    history_5m.loc[prev_idx, 'Open'] = 100.5
    history_5m.loc[prev_idx, 'High'] = 100.8
    history_5m.loc[prev_idx, 'Close'] = 99.8
    
    history_5m.loc[last_idx, 'Open'] = 99.5
    history_5m.loc[last_idx, 'Low'] = 99.0
    history_5m.loc[last_idx, 'Close'] = 101.5
    history_5m.loc[last_idx, 'High'] = 102.0
    history_5m.loc[last_idx, 'Volume'] = 500_000
    
    history_5m["RSI"] = 65.0
    history_5m["Volume"] = history_5m["Volume"] * 2  # Boost volume
    
    swing_high_idx = history_5m.index[-6]
    history_5m.loc[swing_high_idx, 'High'] = 150.0
    
    swing_low_idx = history_5m.index[-10]
    history_5m.loc[swing_low_idx, 'Low'] = 95.0
    
    mock_fetch.return_value = {"WEAK.NS": history_5m}
    
    # Force portfolio rejection
    with patch('portfolio_engine.PortfolioEngine.execute_ranked_candidates') as mock_allocate:
        def reject_all(candidates, policy):
            for c in candidates:
                c['status'] = "QUALIFIED" # Left qualified means rejected by portfolio engine
        mock_allocate.side_effect = reject_all
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
    # Verify exactly 0 alerts
    assert mock_db_save_alert.call_count == 0
    
    # Verify exactly 1 rejection
    assert mock_db_save_candidate.call_count == 1
    kwargs = mock_db_save_candidate.call_args[1]
    assert kwargs['symbol'] == "WEAK.NS"
    assert kwargs['status'] == "REJECTED_CAPITAL"
