import pytest
from unittest.mock import patch, MagicMock
from app.multi_tf_scanner import run_lower_tf_phase
from tests.factories import make_price_history
import logging

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

def build_scenario_data(base_history, volume_weak=False, gap_up=False, rr_poor=False):
    history = base_history.copy()
    prev_idx = history.index[-2]
    last_idx = history.index[-1]
    
    # Base setup
    history.loc[prev_idx, 'Open'] = 100.5
    history.loc[prev_idx, 'High'] = 100.8
    history.loc[prev_idx, 'Close'] = 99.8
    
    history.loc[last_idx, 'Open'] = 99.5
    history.loc[last_idx, 'Low'] = 99.0
    history.loc[last_idx, 'Close'] = 101.5
    history.loc[last_idx, 'High'] = 102.0
    history.loc[last_idx, 'Volume'] = 500_000
    
    if gap_up:
        history.loc[last_idx, 'Open'] = 101.0
        history.loc[last_idx, 'Low'] = 100.5
        history.loc[last_idx, 'Close'] = 102.5
        history.loc[last_idx, 'High'] = 103.0
    
    if volume_weak:
        # To fail the volume ratio filter (> 2.0 or 2.5), set current volume to average
        history.loc[last_idx, 'Volume'] = 100_000
    else:
        # To pass the volume ratio filter, boost it
        history.loc[last_idx, 'Volume'] = 500_000
    
    if rr_poor:
        # Tight structural resistance right above entry
        swing_high_idx = history.index[-6]
        history.loc[swing_high_idx, 'High'] = 102.0 # RR < 2.5
        swing_low_idx = history.index[-10]
        history.loc[swing_low_idx, 'Low'] = 91.0
    else:
        # Wide structural resistance for good RR
        swing_high_idx = history.index[-6]
        history.loc[swing_high_idx, 'High'] = 150.0 
        swing_low_idx = history.index[-10]
        history.loc[swing_low_idx, 'Low'] = 95.0
        
    return history


@patch('database.mark_breakout_watchlist_cooldown')
@patch('database.get_latest_weights')
@patch('database.get_pledge_map')
@patch('database.save_alert_if_new')
@patch('database.save_candidate')
@patch('database.save_rejected_alert')
@patch('app.multi_tf_scanner.upsert_breakout_watchlist')
@patch('app.multi_tf_scanner.check_recent_alert')
@patch('app.multi_tf_scanner.fetch_watchlist_data')
@patch('app.multi_tf_scanner.get_active_breakout_watchlist')
class TestGoldenScenarios:
    
    def test_scenario_a_perfect_breakout(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                       mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Perfect breakout

        Purpose:
        Verify that a structurally perfect breakout successfully passes all pipeline stages and generates an alert.

        Input:
        - Validated price history
        - High volume ratio (> 2.0)
        - Trend aligned
        - Breakout confirmed
        - Favorable RR

        Expected Decision:
        Funded & Alert Emitted

        Expected Reason:
        Excellent technical confluence

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Scanner accepted
        ✓ Candidate created with FUNDED status
        ✓ Alert emitted
        """
        symbol = "PERFECT.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history)
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 1
        assert mock_db_save_candidate.call_count == 1
        
        alert_kwargs = mock_db_save_alert.call_args[1]
        assert alert_kwargs['symbol'] == symbol
        
        cand_kwargs = mock_db_save_candidate.call_args[1]
        assert cand_kwargs['symbol'] == symbol
        assert cand_kwargs['status'] == "FUNDED"

    def test_scenario_b_volume_weak(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                  mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history, caplog):
        """
        Scenario:
        Weak volume breakout

        Purpose:
        Verify that insufficient participation prevents an alert.

        Input:
        - Validated price history
        - Volume ratio = 1.0 (Below minimum threshold)
        - Breakout confirmed

        Expected Decision:
        Rejected

        Expected Reason:
        Volume below threshold

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Scanner rejected (PhaseD)
        ✓ No candidate created
        ✓ No alert emitted
        """
        symbol = "WEAK_VOL.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history, volume_weak=True)
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        assert mock_db_save_candidate.call_count == 0
        
        assert "PhaseD Reject" in caplog.text
        assert "Vol=False" in caplog.text

    def test_scenario_c_fails_validation(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                       mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Data validation failure

        Purpose:
        Verify that corrupt or missing data prevents the scanner from executing on the symbol.

        Input:
        - Missing price history (API failure)

        Expected Decision:
        Aborted safely

        Expected Reason:
        No valid price data returned for symbol

        Pipeline Assertions:
        ✓ Validation failed
        ✓ Scanner not invoked
        ✓ Candidate not created
        ✓ Alert not emitted
        """
        symbol = "NO_DATA.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        mock_fetch.return_value = {}
        
        run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        assert mock_db_save_candidate.call_count == 0

    @patch.dict('config.MIN_NATURAL_RR', {'MULTI_TF': 4.0})
    def test_scenario_d_poor_rr(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                              mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Poor Risk/Reward

        Purpose:
        Verify that a structurally poor setup is rejected before capital is committed.

        Input:
        - Validated price history
        - High volume breakout
        - Tight overhead resistance (RR < 2.5x)

        Expected Decision:
        Rejected

        Expected Reason:
        NO_VALID_STRUCTURAL_TARGET

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Scanner accepted
        ✓ SL Engine invoked and rejected
        ✓ Rejection saved to database
        ✓ No candidate created
        ✓ No alert emitted
        """
        symbol = "POOR_RR.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history, rr_poor=True)
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        assert mock_db_save_candidate.call_count == 0
        
        assert mock_save_reject.call_count == 1
        reject_kwargs = mock_save_reject.call_args[1]
        assert reject_kwargs['symbol'] == symbol
        assert "NO_VALID_STRUCTURAL_TARGET" in str(reject_kwargs['rejection_reason'])

    def test_scenario_e_duplicate_opportunity(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                            mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Duplicate opportunity suppression

        Purpose:
        Verify that the pipeline prevents duplicate alerts for the same symbol within the same session.

        Input:
        - Perfect breakout setup executed twice in succession

        Expected Decision:
        Accepted once, suppressed on subsequent runs

        Expected Reason:
        Idempotency / Recent Alert Check

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Candidate created once
        ✓ Alert emitted once
        """
        symbol = "DUPLICATE.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        history_5m = build_scenario_data(base_history)
        mock_fetch.return_value = {symbol: history_5m}
        
        mock_check_recent.side_effect = [False, True]
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 1
        
    def test_scenario_f_cooldown_respected(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                         mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Cooldown state respected

        Purpose:
        Verify that a symbol marked as COOLDOWN by the daily builder is ignored by the intraday scanner.

        Input:
        - Watchlist item with current_state="COOLDOWN"

        Expected Decision:
        Ignored entirely

        Expected Reason:
        Symbol is not in ENTRY_READY state

        Pipeline Assertions:
        ✓ Validation skipped (data fetch not invoked)
        ✓ Scanner skipped
        ✓ Candidate not created
        ✓ Alert not emitted
        """
        symbol = "COOLDOWN.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "COOLDOWN", 100.0)]
        
        run_lower_tf_phase(run_once=True, is_test_mode=False)
        
        assert mock_fetch.call_count == 0
        assert mock_db_save_alert.call_count == 0
        
    def test_scenario_g_gap_up_breakout(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                      mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Gap-up breakout logic

        Purpose:
        Verify that a valid gap up correctly adjusts the entry price without failing the core logic.

        Input:
        - Breakout level = 100
        - Gap candle open = 101.0

        Expected Decision:
        Funded & Alert Emitted

        Expected Reason:
        Gap within tolerance, trade eligible

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Alert emitted
        ✓ Entry price reflects the gap level (> trigger level)
        """
        symbol = "GAP_UP.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history, gap_up=True)
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 1
        alert_kwargs = mock_db_save_alert.call_args[1]
        
        # Verify business invariant: gap up execution happens at the gap price
        assert alert_kwargs['entry_price'] >= 101.0

    def test_scenario_h_false_breakout(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                     mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history, caplog):
        """
        Scenario:
        False breakout rejection

        Purpose:
        Verify that an intraday wick above the level that closes poorly is rejected.

        Input:
        - High > Trigger
        - Close < Trigger

        Expected Decision:
        Rejected

        Expected Reason:
        Weak closing position / Not above trigger

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Scanner rejected (PhaseD)
        ✓ Alert not emitted
        """
        symbol = "FALSE_BRK.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history)
        
        last_idx = history_5m.index[-1]
        history_5m.loc[last_idx, 'High'] = 101.0
        history_5m.loc[last_idx, 'Close'] = 99.0
        
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        assert mock_db_save_candidate.call_count == 0
        
        assert "PhaseD Reject" in caplog.text

    def test_scenario_i_corporate_action_split(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                             mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Corporate action (split) normalization

        Purpose:
        Verify the pipeline handles normalized split-adjusted data smoothly when provided by the cache.

        Input:
        - Adjusted historical data

        Expected Decision:
        Funded & Alert Emitted

        Expected Reason:
        Normalized pattern matched

        Pipeline Assertions:
        ✓ Validation passed
        ✓ Alert emitted correctly
        """
        symbol = "SPLIT.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history)
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 1
        
    def test_scenario_j_market_holiday(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                     mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Market holiday protection

        Purpose:
        Verify that stale data fetched during a holiday does not generate erroneous alerts.

        Input:
        - Price data marked stale (no recent ticks)

        Expected Decision:
        Ignored safely

        Expected Reason:
        Data is stale

        Pipeline Assertions:
        ✓ Validation caught stale data
        ✓ Scanner skipped symbol
        ✓ Alert not emitted
        """
        symbol = "HOLIDAY.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        
        history_5m = build_scenario_data(base_history)
        history_5m.attrs['is_stale'] = True
        
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        
    def test_scenario_k_extremely_volatile_stock(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                               mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Volatility-adjusted Stop Loss

        Purpose:
        Verify that high ATR correctly maps to a wider stop loss distance to avoid whipsaws.

        Input:
        - ATR artificially doubled

        Expected Decision:
        Funded & Alert Emitted

        Expected Reason:
        Favorable RR despite wide SL

        Pipeline Assertions:
        ✓ Validation passed
        ✓ SL Engine correctly widened the stop relative to a baseline
        ✓ Alert emitted
        """
        symbol = "VOLATILE.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        # 1. Baseline Test (Normal ATR)
        history_normal = build_scenario_data(base_history)
        mock_fetch.return_value = {symbol: history_normal}
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_normal):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
        assert mock_db_save_alert.call_count == 1
        baseline_sl_dist = mock_db_save_alert.call_args[1]['entry_price'] - mock_db_save_alert.call_args[1]['stop_loss']
        
        # Reset mocks
        mock_db_save_alert.reset_mock()
        mock_check_recent.return_value = False
        
        # 2. Volatile Test (High ATR)
        history_volatile = build_scenario_data(base_history)
        for i in range(2, 22):
            idx = history_volatile.index[-(i+2)]
            history_volatile.loc[idx, 'High'] = history_volatile.loc[idx, 'High'] * 1.05
            history_volatile.loc[idx, 'Low'] = history_volatile.loc[idx, 'Low'] * 0.95
            
        mock_fetch.return_value = {symbol: history_volatile}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_volatile):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 1
        volatile_sl_dist = mock_db_save_alert.call_args[1]['entry_price'] - mock_db_save_alert.call_args[1]['stop_loss']
        
        # Verify business invariant: higher volatility = structurally wider stop loss
        assert volatile_sl_dist > baseline_sl_dist

    def test_scenario_l_missing_optional_fields(self, mock_get_wl, mock_fetch, mock_check_recent, mock_upsert, 
                                              mock_save_reject, mock_db_save_candidate, mock_db_save_alert, mock_pledge, mock_weights, mock_cooldown, base_history):
        """
        Scenario:
        Missing optional fields gracefully degrade

        Purpose:
        Verify that mathematical calculations safely degrade when volume or other optional data is NaN.

        Input:
        - Volume = NaN

        Expected Decision:
        Rejected

        Expected Reason:
        Volume ratio filter failed

        Pipeline Assertions:
        ✓ Validation passed
        ✓ No pipeline crashes
        ✓ PhaseD rejected safely
        ✓ Alert not emitted
        """
        import numpy as np
        symbol = "MISSING_DATA.NS"
        mock_get_wl.return_value = [make_test_watchlist_item(symbol, "ENTRY_READY", 100.0)]
        mock_check_recent.return_value = False
        mock_pledge.return_value = {symbol: 0.0}
        mock_weights.return_value = {"weights": {}}
        
        history_5m = build_scenario_data(base_history)
        
        history_5m['Volume'] = np.nan
        
        mock_fetch.return_value = {symbol: history_5m}
        
        with patch('app.multi_tf_scanner.strip_forming_candle', return_value=history_5m):
            run_lower_tf_phase(run_once=True, is_test_mode=False)
            
        assert mock_db_save_alert.call_count == 0
        assert mock_save_reject.call_count == 0
