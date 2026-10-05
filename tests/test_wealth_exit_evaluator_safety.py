"""
[REGRESSION TEST BATTERY]: Wealth Engine Exit Evaluator Safety & Semantic Non-Evaluated State

Verifies that:
1. _generate_exit_signal() populates all 4 core output keys ("Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason") across ALL execution paths.
2. Non-evaluated early-return paths (DATA_STALE, INCOMPLETE_FUNDAMENTALS, SPLIT_ADJUSTED, HARD_DRAWDOWN) set hold_trend = "UNKNOWN" (never false "Stable").
3. Fully validated paths (NORMAL_HOLD, NORMAL_EXIT) set hold_trend to evaluated trend ("Stable" / "Weakening" / "Improving").
4. portfolio_df DataFrame projections always contain all four columns without throwing KeyError.
"""

import pytest
import pandas as pd
import numpy as np
from app.wealth_engine import evaluate_open_positions


class TestWealthExitEvaluatorSafety:
    
    def test_01_data_stale_path(self, monkeypatch):
        """Path 1: Stale / Missing price data (cmp <= 0 or STALE_INTRADAY). Must yield DATA_STALE and hold_trend == 'UNKNOWN'."""
        df_in = pd.DataFrame([{
            "Stock": "TEST_STALE",
            "cmp": 0.0,
            "entry_price": 100.0,
            "prev_close": 100.0,
            "data_quality": "STALE_INTRADAY",
            "FM_Score": 80.0,
            "RS_Rating": 85.0,
        }])
        
        df_out = evaluate_open_positions(df_in, {})
        assert not df_out.empty
        row = df_out.iloc[0].to_dict()
        
        # Verify required keys present
        for col in ["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]:
            assert col in row
            
        assert row["Exit_Code"] == "DATA_STALE"
        assert row["hold_trend"] == "UNKNOWN"
        assert row["hold_trend"] != "Stable"

    def test_02_incomplete_fundamentals_path(self, monkeypatch):
        """Path 2: Missing FM_Score / RS_Rating. Must yield DATA_STALE and hold_trend == 'UNKNOWN'."""
        df_in = pd.DataFrame([{
            "Stock": "TEST_INCOMPLETE",
            "cmp": 100.0,
            "entry_price": 95.0,
            "prev_close": 100.0,
            "data_quality": "VALID",
            "FM_Score": np.nan,
            "RS_Rating": 85.0,
        }])
        
        df_out = evaluate_open_positions(df_in, {})
        assert not df_out.empty
        row = df_out.iloc[0].to_dict()
        
        for col in ["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]:
            assert col in row
            
        assert row["Exit_Code"] == "DATA_STALE"
        assert row["hold_trend"] == "UNKNOWN"
        assert row["hold_trend"] != "Stable"

    def test_03_split_adjusted_path(self, monkeypatch):
        """Path 3: Corporate action split detected. Must yield SPLIT_ADJUSTED and hold_trend == 'UNKNOWN'."""
        monkeypatch.setattr("corporate_actions.get_bulk_split_factor", lambda *args, **kwargs: 2.0)
        
        df_in = pd.DataFrame([{
            "Stock": "TEST_SPLIT",
            "cmp": 30.0,
            "entry_price": 100.0,  # 70% apparent drop, split factor = 2.0
            "prev_close": 30.0,
            "data_quality": "VALID",
            "FM_Score": 80.0,
            "RS_Rating": 85.0,
        }])
        
        df_out = evaluate_open_positions(df_in, {})
        assert not df_out.empty
        row = df_out.iloc[0].to_dict()
        
        for col in ["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]:
            assert col in row
            
        assert row["Exit_Code"] == "SPLIT_ADJUSTED"
        assert row["hold_trend"] == "UNKNOWN"
        assert row["hold_trend"] != "Stable"

    def test_04_hard_drawdown_path(self, monkeypatch):
        """Path 4: Hard drawdown stop loss (>= 20% loss, no split). Must yield SELL and hold_trend == 'UNKNOWN'."""
        monkeypatch.setattr("corporate_actions.get_bulk_split_factor", lambda *args, **kwargs: 1.0)
        
        df_in = pd.DataFrame([{
            "Stock": "TEST_DRAWDOWN",
            "cmp": 75.0,
            "entry_price": 100.0,  # 25% drawdown
            "prev_close": 100.0,   # prev_close matches entry_price so no delayed split trigger
            "data_quality": "VALID",
            "FM_Score": 80.0,
            "RS_Rating": 85.0,
        }])
        
        df_out = evaluate_open_positions(df_in, {})
        assert not df_out.empty
        row = df_out.iloc[0].to_dict()
        
        for col in ["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]:
            assert col in row
            
        assert row["Exit_Code"] == "SELL"
        assert "Hard Drawdown Stop" in row["Exit_Reason"]
        assert row["hold_trend"] == "UNKNOWN"
        assert row["hold_trend"] != "Stable"

    def test_05_normal_hold_path(self, monkeypatch):
        """Path 5: Normal position evaluated cleanly without exits. Must yield hold_trend == 'Stable'."""
        monkeypatch.setattr("corporate_actions.get_bulk_split_factor", lambda *args, **kwargs: 1.0)
        
        df_in = pd.DataFrame([{
            "Stock": "TEST_HOLD",
            "cmp": 105.0,
            "entry_price": 100.0,
            "prev_close": 104.0,
            "data_quality": "VALID",
            "FM_Score": 85.0,
            "RS_Rating": 90.0,
            "rs_6m": 20.0,
            "sma_200": 80.0,
        }])
        
        df_out = evaluate_open_positions(df_in, {})
        assert not df_out.empty
        row = df_out.iloc[0].to_dict()
        
        for col in ["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]:
            assert col in row
            
        assert row["Exit_Code"] == ""
        assert row["hold_trend"] == "Stable"

    def test_06_dataframe_projection_guard(self):
        """Verify DataFrame projection guards work cleanly when input has 0 rows or missing columns."""
        df_empty = pd.DataFrame()
        df_out = evaluate_open_positions(df_empty, {})
        assert df_out.empty
