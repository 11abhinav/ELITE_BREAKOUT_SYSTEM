import pytest
from unittest.mock import patch, MagicMock
from app.multi_tf_scanner import run_lower_tf_phase
from tests.factories import make_price_history

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
@patch('app.market_utils.is_market_open')
@patch('app.multi_tf_scanner.OpportunityManager')
@patch('app.multi_tf_scanner.check_recent_alert')
@patch('app.multi_tf_scanner.upsert_breakout_watchlist')
@patch('app.multi_tf_scanner.strip_forming_candle')
@patch('sl_target_helper.compute_sl_and_target')
def test_scanner_promotion_happy_path(mock_compute_sl, mock_strip, mock_upsert, mock_check_recent, mock_om_class, mock_market_open, mock_fetch, mock_get_wl):
    """
    Input: ENTRY_READY stock with a perfect 5m breakout candle.
    Expected: Promoted to TRADE_ACTIVE, queued in OpportunityManager.
    Decision/Reason: Meets all technical requirements for a trigger.
    """
    mock_market_open.return_value = True
    mock_check_recent.return_value = False
    mock_strip.side_effect = lambda df, tf, now: df
    mock_compute_sl.return_value = {
        "is_rejected": False,
        "stop_loss": 95.0,
        "target_1": 120.0,
        "natural_rr": 3.0
    }
    
    # Setup watchlist
    mock_get_wl.return_value = [make_test_watchlist_item("TEST.NS", "ENTRY_READY")]
    
    # Setup 5m price history showing a breakout
    # Previous close was below breakout (99), current close is above (101) with volume
    df_5m = make_price_history("TEST.NS").with_base_price(99.0).build()
    df_5m.iloc[-2, df_5m.columns.get_loc('High')] = 100.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Close')] = 101.0
    df_5m.iloc[-1, df_5m.columns.get_loc('High')] = 101.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Low')] = 99.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Open')] = 99.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Volume')] = 200_000 # 2x volume
    df_5m["EMA9"] = 99.5
    df_5m["ATR20"] = 1.0
    df_5m["SWING_HIGH"] = 150.0
    df_5m["SWING_LOW"] = 95.0
    
    # Mock data fetch (it requests 30m, 15m, 5m, daily)
    def fetch_side_effect(symbols_df, period, interval, **kwargs):
        return {"TEST.NS": df_5m}
        
    mock_fetch.side_effect = fetch_side_effect
    
    mock_om_instance = MagicMock()
    mock_om_class.return_value = mock_om_instance

    run_lower_tf_phase(is_test_mode=False, run_once=True)
    
    # Assert opportunity was promoted (queued in manager)
    mock_om_instance.add.assert_called_once()
    added_opp = mock_om_instance.add.call_args[0][0]
    
    assert added_opp["symbol"] == "TEST.NS", "Correct symbol promoted"
    assert added_opp["technical_score"] > 0, "Candidate must be scored"

@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.market_utils.is_market_open')
@patch('app.multi_tf_scanner.OpportunityManager')
def test_scanner_rejection_low_volume(mock_om_class, mock_market_open, mock_fetch, mock_get_wl):
    """
    Input: ENTRY_READY stock but 5m breakout has no volume.
    Expected: Rejected (PD03), not queued.
    """
    mock_market_open.return_value = True
    mock_get_wl.return_value = [make_test_watchlist_item("TEST.NS", "ENTRY_READY")]
    
    df_5m = make_price_history("TEST.NS").with_base_price(99.0).build()
    df_5m.iloc[-1, df_5m.columns.get_loc('Close')] = 101.0
    df_5m.iloc[-1, df_5m.columns.get_loc('High')] = 101.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Volume')] = 10_000 # 0.1x volume, terrible!
    df_5m["EMA9"] = 99.5
    df_5m["ATR20"] = 1.0
    
    def fetch_side_effect(symbols_df, period, interval, **kwargs):
        if interval == "5m":
            return {"TEST.NS": df_5m}
        return {}
        
    mock_fetch.side_effect = fetch_side_effect
    
    mock_om_instance = MagicMock()
    mock_om_class.return_value = mock_om_instance

    run_lower_tf_phase(is_test_mode=False, run_once=True)
    
    # Must NOT be promoted
    mock_om_instance.add.assert_not_called()

@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.market_utils.is_market_open')
@patch('app.multi_tf_scanner.OpportunityManager')
@patch('app.multi_tf_scanner.check_recent_alert')
def test_scanner_duplicate_suppression(mock_check_recent, mock_om_class, mock_market_open, mock_fetch, mock_get_wl):
    """
    Input: Perfect breakout, but check_recent_alert says it recently fired.
    Expected: Handled as duplicate, suppressed, not queued.
    """
    mock_market_open.return_value = True
    mock_get_wl.return_value = [make_test_watchlist_item("TEST.NS", "ENTRY_READY")]
    
    df_5m = make_price_history("TEST.NS").with_base_price(99.0).build()
    df_5m.iloc[-1, df_5m.columns.get_loc('Close')] = 101.0
    df_5m.iloc[-1, df_5m.columns.get_loc('Volume')] = 200_000
    df_5m.iloc[-1, df_5m.columns.get_loc('High')] = 101.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Low')] = 99.5
    df_5m.iloc[-1, df_5m.columns.get_loc('Open')] = 99.5
    df_5m["EMA9"] = 99.5
    df_5m["ATR20"] = 1.0
    
    def fetch_side_effect(symbols_df, period, interval, **kwargs):
        if interval == "5m":
            return {"TEST.NS": df_5m}
        return {}
        
    mock_fetch.side_effect = fetch_side_effect
    
    # MOCK: It recently fired!
    mock_check_recent.return_value = True
    
    mock_om_instance = MagicMock()
    mock_om_class.return_value = mock_om_instance

    run_lower_tf_phase(is_test_mode=False, run_once=True)
    
    mock_om_instance.add.assert_not_called()
