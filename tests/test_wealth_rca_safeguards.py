"""
Regression tests for Wealth Engine RCA Safeguards:
1. Winning trade guard blocks Catastrophic Trend Collapse in evaluate_open_positions
2. Quality Value Recovery uses Model D/E3 rules and bypasses 200 SMA / RS checks
3. database.close_position_atomic blocks Catastrophic Trend Collapse on winning trades
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from app.wealth_engine import evaluate_open_positions
from app.database import close_position_atomic


def test_wealth_evaluator_winning_trade_guard():
    """
    Ensure that if CMP > entry_price, Catastrophic Trend Collapse and RS Breakdown
    are strictly suppressed.
    """
    df = pd.DataFrame([{
        "Stock": "WINNING_TEST",
        "scanner": "QUALITY_COMPOUNDER",
        "entry_price": 100.0,
        "cmp": 110.0,        # +10% profit!
        "prev_close": 108.0, # Valid close
        "sma_200": 160.0,    # 110 < 0.75 * 160 (120) -> would have triggered Catastrophic Trend Collapse!
        "rs_6m": -50.0,
        "entry_date": "2026-09-01",
        "consecutive_closes_below_200sma": 0,
        "roce_avg": 20.0,
        "trailing_roce": 20.0,
        "sales_growth_3y": 15.0,
        "opm_trend": "EXPANDING",
        "der": 0.2,
        "rsi_14": 55.0,
        "atr_14": 3.0,
        "dist_from_52w_high": 10.0,
        "used_fallback_data": False,
        "data_quality": "VALID",
        "context": {}
    }])
    
    with patch("wealth_hold_tracking.HoldScoreTrendAnalyzer.analyze_trends_batch", return_value={}):
        with patch("macro_utils.get_macro_regime", return_value="BULL"):
            result_df = evaluate_open_positions(df, {"WINNING_TEST": {"scanner": "QUALITY_COMPOUNDER"}})
    
    row = result_df.iloc[0]
    exit_code = row.get("Exit_Code")
    exit_reason = row.get("Exit_Reason", "")
    assert "Catastrophic Trend Collapse" not in exit_reason, (
        f"Winning trade must NEVER trigger Catastrophic Trend Collapse! Got: {exit_code} - {exit_reason}"
    )


def test_wealth_evaluator_recovery_model_d_e3_rules():
    """
    Ensure that QUALITY_VALUE_RECOVERY evaluates under Model D/E3 rules,
    and ignores 200 SMA.
    """
    df = pd.DataFrame([{
        "Stock": "RECOVERY_TEST",
        "scanner": "QUALITY_VALUE_RECOVERY",
        "entry_price": 100.0,
        "cmp": 95.0,         # Down 5% (above 10% stop loss)
        "prev_close": 96.0,
        "sma_200": 150.0,    # Far below 200 SMA (95 < 0.75 * 150 = 112.5) -> would have triggered Trend Collapse!
        "rs_6m": -50.0,
        "entry_date": "2026-10-01",
        "consecutive_closes_below_200sma": 10,
        "roce_avg": 20.0,
        "trailing_roce": 20.0,
        "sales_growth_3y": 15.0,
        "opm_trend": "EXPANDING",
        "der": 0.2,
        "rsi_14": 40.0,
        "atr_14": 3.0,
        "dist_from_52w_high": 35.0,
        "used_fallback_data": False,
        "data_quality": "VALID",
        "context": {
            "current_ev_ebitda": 10.0,
            "ev_ebitda_3y_median": 15.0, # Not yet re-rated
            "debt_to_equity": 0.2
        }
    }])
    
    with patch("wealth_hold_tracking.HoldScoreTrendAnalyzer.analyze_trends_batch", return_value={}):
        with patch("macro_utils.get_macro_regime", return_value="BULL"):
            result_df = evaluate_open_positions(df, {"RECOVERY_TEST": {"scanner": "QUALITY_VALUE_RECOVERY"}})
            
    row = result_df.iloc[0]
    exit_code = row.get("Exit_Code")
    exit_reason = row.get("Exit_Reason", "")
    assert exit_code != "SELL", f"Healthy recovery trade should NOT trigger exit! Got: {exit_code} - {exit_reason}"
    assert "Catastrophic Trend Collapse" not in exit_reason


def test_close_position_atomic_safeguard():
    """
    Ensure close_position_atomic refuses to close a trade with positive return
    on Catastrophic Trend Collapse.
    """
    with patch("database.get_connection") as mock_conn:
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (100.0, "2026-10-01")
        
        # Call close_position_atomic with exit_price = 110.0 (+10% gain) and Catastrophic Trend Collapse
        res = close_position_atomic("TEST_SYM", 110.0, "Catastrophic Trend Collapse (CMP < 75% 200SMA)")
        assert res is False, "close_position_atomic must refuse to close winning trade on trend collapse!"
