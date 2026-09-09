# tests/test_multi_tf_risk_repair.py
# Unit and regression test suite for Multi-TF Risk-Control & Structural Engine Repair

import sys
import os
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_APP_DIR = os.path.join(_ROOT_DIR, "app")
for _p in (_APP_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from sl_target_helper import TradeStructureValidator, compute_sl_and_target, _compute_structural_stop
from config import MULTI_TF_CONFIG, MIN_STOP_PCT
from multi_tf_scanner import evaluate_multi_tf_symbol


class TestTradeStructureInvariants:
    def test_directional_sl_long_valid(self):
        """Valid LONG setup: SL < Entry, Target > Entry, Risk >= 1.2%."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=98.0,
            target_1=104.0,
            min_rr=1.5,
            direction="LONG",
            min_risk_pct=1.2,
            max_rr=8.0
        )
        assert res["is_valid"] is True
        assert res["risk"] == 2.0
        assert res["risk_pct"] == 2.0
        assert res["natural_rr"] == 2.0

    def test_directional_sl_long_invalid(self):
        """Invalid LONG setup: SL >= Entry."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=102.0,
            target_1=108.0,
            direction="LONG"
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "LONG_SL_NOT_BELOW_ENTRY"

    def test_directional_sl_short_invalid(self):
        """Invalid SHORT setup: SL <= Entry."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=98.0,
            target_1=95.0,
            direction="SHORT"
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "SHORT_SL_NOT_ABOVE_ENTRY"

    def test_directional_target_long_invalid(self):
        """Invalid LONG target: Target 1 <= Entry (e.g. EIDPARRY legacy bug)."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=97.0,
            target_1=99.0,
            direction="LONG"
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "LONG_TARGET_NOT_ABOVE_ENTRY"

    def test_risk_too_tight_floor(self):
        """Risk below 1.2% floor (e.g. ABB 0.24% or GENUSPOWER 0.87%)."""
        res = TradeStructureValidator.validate(
            entry=7627.0,
            stop_loss=7608.50, # 0.24% risk
            target_1=7696.50,
            min_risk_pct=1.2
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "RISK_TOO_TIGHT"
        assert res["risk_pct"] < 1.2

    def test_outlier_target_rr_rejection(self):
        """Outlier R:R > 8.0x (e.g. WAAREERTL 11.49x R:R) is rejected explicitly, not clamped."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=98.0, # risk = 2.0
            target_1=120.0, # reward = 20.0 (10.0x R:R)
            min_rr=1.5,
            max_rr=8.0
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "OUTLIER_TARGET"

    def test_outlier_target_atr_dist_rejection(self):
        """Target distance > 10x ATR is rejected explicitly."""
        res = TradeStructureValidator.validate(
            entry=100.0,
            stop_loss=97.0,
            target_1=135.0,
            eff_atr=2.0,
            max_target_atr_mult=10.0 # max target dist = 20.0, actual = 35.0
        )
        assert res["is_valid"] is False
        assert res["rejection_code"] == "OUTLIER_TARGET"


class TestMultiTFStructuralStopEngine:
    def test_sub_one_percent_stop_re_anchors_or_rejects(self):
        """Sub-1% candidate stop must search lower structural support >= 1.2% or reject."""
        entry = 100.0
        eff_atr = 1.0
        # Support 1 gives 0.5% stop, Support 2 gives 1.8% stop
        supports = [
            (99.8, "5m Swing Low", 20),
            (98.5, "1H Swing Low", 35),
            (97.0, "S1", 15)
        ]
        res = _compute_structural_stop(entry, eff_atr, atr_pct=2.0, supports=supports, ctx={"mode": "MULTI_TF"})
        assert res["is_valid"] is True
        assert res["sl_pct"] >= 1.2
        assert res["raw_sl"] <= 98.8 # Must pick the valid structural support

    def test_all_tight_supports_rejects_risk_too_tight(self):
        """If all available supports are too tight (< 1.2%), explicitly reject with RISK_TOO_TIGHT."""
        entry = 100.0
        eff_atr = 0.2
        supports = [
            (99.8, "5m Swing Low", 20),
            (99.6, "15m Swing Low", 25)
        ]
        res = _compute_structural_stop(entry, eff_atr, atr_pct=1.0, supports=supports, ctx={"mode": "MULTI_TF"})
        assert res["is_valid"] is False
        assert res["rejection_code"] == "RISK_TOO_TIGHT"


class TestMultiTFScannerGates:
    @pytest.fixture
    def synthetic_candles(self):
        dates = pd.date_range("2026-07-01 09:15", periods=150, freq="1h")
        # Filter out weekends to satisfy assertion
        dates = dates[~dates.dayofweek.isin([5, 6])]
        df = pd.DataFrame({
            "Open": np.linspace(100, 150, len(dates)),
            "High": np.linspace(102, 152, len(dates)),
            "Low": np.linspace(99, 149, len(dates)),
            "Close": np.linspace(101, 151, len(dates)),
            "Volume": np.full(len(dates), 500_000)
        }, index=dates)
        return df

    def test_weekend_data_assertion(self, synthetic_candles):
        """Weekend candles must trigger an assertion or be safely stripped."""
        # Inject weekend candle
        weekend_dates = pd.date_range("2026-08-08 09:15", periods=2, freq="1h") # Saturday
        weekend_df = pd.DataFrame({
            "Open": [150, 150], "High": [152, 152], "Low": [149, 149],
            "Close": [151, 151], "Volume": [1000, 1000]
        }, index=weekend_dates)
        dirty_df = pd.concat([synthetic_candles, weekend_df])
        
        # evaluate_multi_tf_symbol strips weekend data and verifies assertion
        res = evaluate_multi_tf_symbol("TEST_WEEKEND", dirty_df, allow_live_fetch=False)
        assert isinstance(res, dict)

    def test_contextual_volume_exhaustion_gate(self, synthetic_candles):
        """Exhaustion candle (10x volume + 50% upper wick) is rejected as VOLUME_EXHAUSTION."""
        df = synthetic_candles.copy()
        # Inject 10x volume with massive upper wick (rejection at top)
        df.iloc[-1, df.columns.get_loc("Volume")] = 5_000_000 # 10x
        df.iloc[-1, df.columns.get_loc("High")] = 180.0
        df.iloc[-1, df.columns.get_loc("Low")] = 150.0
        df.iloc[-1, df.columns.get_loc("Close")] = 155.0 # Closed near bottom of range (upper wick = 25/30 = 83%)
        
        res = evaluate_multi_tf_symbol("TEST_EXHAUSTION", df, allow_live_fetch=False)
        assert res["qualified"] is False
        assert any("VOLUME_EXHAUSTION" in r for r in res.get("reasons", []))

    def test_clean_institutional_volume_passes(self, synthetic_candles):
        """7x volume candle closing near highs without exhaustion passes the volume gate."""
        df = synthetic_candles.copy()
        df.iloc[-1, df.columns.get_loc("Volume")] = 3_500_000 # 7x
        df.iloc[-1, df.columns.get_loc("High")] = 155.0
        df.iloc[-1, df.columns.get_loc("Low")] = 150.0
        df.iloc[-1, df.columns.get_loc("Close")] = 154.5 # Strong close near high (wick = 0.5/5 = 10%)
        
        res = evaluate_multi_tf_symbol("TEST_CLEAN_VOL", df, allow_live_fetch=False)
        # Volume exhaustion should NOT trigger
        assert not any("VOLUME_EXHAUSTION" in r for r in res.get("reasons", []))
