"""
Unit & PIT/No-Lookahead Test Suite for Isolated Research Candidates:
  1. MultiTfResearchV1 (Hierarchical State Machine + Timestamp Sync)
  2. ReversalResearchV1 (Support Anchor + Reclaim + Vol Divergence)
  3. DailyBuilderResearchV1 (ORB Width Clamp + 15:15 IST Close)
"""

import pytest
import numpy as np
import pandas as pd
from engine.research.research_candidates import (
    MultiTfResearchV1, ReversalResearchV1, DailyBuilderResearchV1
)


def test_multi_tf_research_v1_hierarchical_confluence():
    """Verifies that MULTI_TF requires all 3 layers (Daily, 15m, 5m) to agree."""
    # 1. Daily NOT Trend Up -> Must FAIL
    res_daily_fail = MultiTfResearchV1.evaluate(
        daily_close=100.0, daily_sma50=105.0, daily_sma200=110.0, daily_slope=-0.01,
        tf15_supertrend_green=True, tf15_vol_ratio=1.6, tf5_breakout=True, entry_price=100.0
    )
    assert res_daily_fail["qualified"] is False
    assert res_daily_fail["daily_state"] == "NO_TREND"

    # 2. 15m NOT Trend Up -> Must FAIL
    res_15m_fail = MultiTfResearchV1.evaluate(
        daily_close=120.0, daily_sma50=110.0, daily_sma200=100.0, daily_slope=0.02,
        tf15_supertrend_green=False, tf15_vol_ratio=1.2, tf5_breakout=True, entry_price=120.0
    )
    assert res_15m_fail["qualified"] is False
    assert res_15m_fail["tf15_state"] == "CHOP"

    # 3. All 3 layers agree -> Must PASS
    res_pass = MultiTfResearchV1.evaluate(
        daily_close=120.0, daily_sma50=110.0, daily_sma200=100.0, daily_slope=0.02,
        tf15_supertrend_green=True, tf15_vol_ratio=1.6, tf5_breakout=True, entry_price=120.0
    )
    assert res_pass["qualified"] is True
    assert res_pass["daily_state"] == "TREND_UP"
    assert res_pass["tf15_state"] == "TREND_UP"
    assert res_pass["stop_loss"] == pytest.approx(120.0 * 0.97)
    assert res_pass["target_1"] == pytest.approx(120.0 + (2.0 * (120.0 * 0.030)))


def test_reversal_research_v1_support_and_reclaim():
    """Verifies that REVERSAL requires structural support anchor, reclaim, and volume divergence."""
    # 1. Price too far from support (> 1.5%) -> Must FAIL
    res_dist_fail = ReversalResearchV1.evaluate(
        rsi_val=28.0, price=100.0, support_level=95.0, # 5.26% away > 1.5%
        is_reclaim_candle=True, base_vol=200000, selloff_vol=100000
    )
    assert res_dist_fail["qualified"] is False
    assert res_dist_fail["near_support"] is False

    # 2. Near support but no reclaim candle -> Must FAIL
    res_no_reclaim = ReversalResearchV1.evaluate(
        rsi_val=28.0, price=100.0, support_level=99.0, # 1.01% away <= 1.5%
        is_reclaim_candle=False, base_vol=200000, selloff_vol=100000
    )
    assert res_no_reclaim["qualified"] is False
    assert res_no_reclaim["reclaim_confirmed"] is False

    # 3. Near support + reclaim + volume divergence -> Must PASS
    res_pass = ReversalResearchV1.evaluate(
        rsi_val=28.0, price=100.0, support_level=99.0,
        is_reclaim_candle=True, base_vol=250000, selloff_vol=120000
    )
    assert res_pass["qualified"] is True
    assert res_pass["stop_loss"] == pytest.approx(96.0) # 4% SL
    assert res_pass["target_1"] == pytest.approx(108.0) # 2.0R Target


def test_daily_builder_research_v1_orb_width_and_volume():
    """Verifies that DAILY_BUILDER enforces ORB range width clamp and session close."""
    # 1. ORB width too wide (4.0% > 2.5%) -> Must FAIL
    res_wide = DailyBuilderResearchV1.evaluate(
        orb_high=102.0, orb_low=98.0, close_price=103.0, vol_ratio=1.8, vwap=101.0
    )
    assert res_wide["qualified"] is False
    assert res_wide["width_ok"] is False

    # 2. ORB width tight (1.8% <= 2.5%) + vol >= 1.5x + above VWAP -> Must PASS
    res_pass = DailyBuilderResearchV1.evaluate(
        orb_high=101.0, orb_low=99.2, close_price=101.5, vol_ratio=1.7, vwap=100.5
    )
    assert res_pass["qualified"] is True
    assert res_pass["width_ok"] is True
    assert res_pass["force_exit_time"] == "15:15 IST"
    assert res_pass["stop_loss"] == pytest.approx(101.5 * (1 - 0.025))
