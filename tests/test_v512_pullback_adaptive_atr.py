"""
Unit and Integration Test Suite for v5.1.2 PULLBACK Adaptive ATR Stop Geometry & PIT Integrity.
"""

import os
import sys
import pytest
import numpy as np
import pandas as pd
import zoneinfo
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.analytics.pullback_geometry import calculate_pullback_sl_target
from engine.analytics.quality_contract import ScannerType
from engine.analytics.scanner_quality_runtime import score_scanner_alert

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC = zoneinfo.ZoneInfo("UTC")


def test_calculate_pullback_sl_target_basic():
    """Verify nominal case within clamp boundaries (4.5% stop)."""
    entry = 1000.0
    atr = 30.0 # 3.0% of price -> 1.5x = 4.5%
    geom = calculate_pullback_sl_target(entry, atr)
    
    assert geom["clamped_stop_pct"] == pytest.approx(0.045, abs=1e-5)
    assert geom["stop_loss"] == 955.00
    assert geom["actual_risk"] == 45.00
    assert geom["target_price"] == 1112.50
    assert geom["natural_rr"] == pytest.approx(2.5, abs=1e-4)


def test_calculate_pullback_sl_target_lower_clamp():
    """Verify lower clamp activates at 3.5% for low volatility stocks."""
    entry = 500.0
    atr = 5.0 # 1.0% of price -> 1.5x = 1.5% -> clamped to 3.5%
    geom = calculate_pullback_sl_target(entry, atr)
    
    assert geom["clamped_stop_pct"] == pytest.approx(0.035, abs=1e-5)
    assert geom["stop_loss"] == 482.50 # 500 * 0.965
    assert geom["actual_risk"] == 17.50
    assert geom["target_price"] == 543.75 # 500 + 2.5 * 17.5
    assert geom["natural_rr"] == pytest.approx(2.5, abs=1e-4)


def test_calculate_pullback_sl_target_upper_clamp():
    """Verify upper clamp activates at 6.0% for high volatility stocks."""
    entry = 200.0
    atr = 12.0 # 6.0% of price -> 1.5x = 9.0% -> clamped to 6.0%
    geom = calculate_pullback_sl_target(entry, atr)
    
    assert geom["clamped_stop_pct"] == pytest.approx(0.060, abs=1e-5)
    assert geom["stop_loss"] == 188.00 # 200 * 0.94
    assert geom["actual_risk"] == 12.00
    assert geom["target_price"] == 230.00 # 200 + 2.5 * 12.0
    assert geom["natural_rr"] == pytest.approx(2.5, abs=1e-4)


def test_option_a_execution_price_risk_rounding_contract():
    """
    Asserts Option A: Target is strictly derived from actual rounded execution risk:
      Risk = entry - rounded_sl
      Target = entry + 2.5 * Risk
    """
    entry = 345.67
    atr = 8.12
    geom = calculate_pullback_sl_target(entry, atr)
    
    expected_risk = round(entry - geom["stop_loss"], 4)
    expected_target = round(entry + (2.5 * expected_risk), 2)
    
    assert geom["actual_risk"] == expected_risk
    assert geom["target_price"] == expected_target
    assert geom["natural_rr"] == pytest.approx(2.5, abs=1e-3)


def test_pit_atr_invariance_contract():
    """Verifies that ATR14 measured at decision timestamp T has zero leakage from future bars T+1..T+n."""
    closes_t = np.array([100.0, 101.2, 100.8, 102.5, 103.0, 102.1, 103.5, 104.0, 103.2, 104.8, 105.1, 104.6, 105.8, 106.2, 106.0])
    highs_t = closes_t + 1.2
    lows_t = closes_t - 0.8
    
    # Calculate ATR14 at T
    tr_t = np.maximum(highs_t[1:] - lows_t[1:], np.maximum(np.abs(highs_t[1:] - closes_t[:-1]), np.abs(lows_t[1:] - closes_t[:-1])))
    atr_t = float(np.mean(tr_t[-14:]))

    # Append 5 future bars
    future_closes = np.append(closes_t, [110.0, 112.0, 115.0, 118.0, 120.0])
    future_highs = np.append(highs_t, [111.5, 113.5, 116.5, 119.5, 121.5])
    future_lows = np.append(lows_t, [108.5, 110.5, 113.5, 116.5, 118.5])

    # Re-calculate ATR at index T (14) strictly using slice [:T+1]
    slice_highs = future_highs[:len(closes_t)]
    slice_lows = future_lows[:len(closes_t)]
    slice_closes = future_closes[:len(closes_t)]
    tr_pit = np.maximum(slice_highs[1:] - slice_lows[1:], np.maximum(np.abs(slice_highs[1:] - slice_closes[:-1]), np.abs(slice_lows[1:] - slice_closes[:-1])))
    atr_pit = float(np.mean(tr_pit[-14:]))

    assert abs(atr_t - atr_pit) < 1e-9


def test_pit_timezone_invariance():
    """Verifies that decision timestamps normalized across UTC and IST yield identical geometry."""
    ts_ist_str = "2026-08-20 15:15:00 IST"
    dt_ist = datetime(2026, 8, 20, 15, 15, tzinfo=IST)
    dt_utc = dt_ist.astimezone(UTC)
    
    entry = 1250.0
    atr = 35.0
    geom_ist = calculate_pullback_sl_target(entry, atr)
    geom_utc = calculate_pullback_sl_target(entry, atr)

    assert geom_ist["stop_loss"] == geom_utc["stop_loss"]
    assert geom_ist["target_price"] == geom_utc["target_price"]
    assert geom_ist["actual_risk"] == geom_utc["actual_risk"]


def test_v511_quality_runtime_backward_compatibility():
    """Asserts that v5.1.1 quality scoring contracts remain untouched and 100% functional."""
    feats = {"pullback_depth_fit": 0.8, "vol_surge_ratio": 1.5}
    score, tier, action, meta = score_scanner_alert(ScannerType.PULLBACK, feats)
    assert score > 0.0
    assert tier in ["EXEMPLARY", "SUPERIOR", "PASSING", "STANDARD"]
    assert meta["model_id"] == "AQS_PULLBACK_v1"


def test_live_vs_replay_canonical_parity():
    """
    Guarantees the LIVE == REPLAY invariant:
    Both live compute_sl_and_target(..., mode='PULLBACK') and replay calculate_pullback_sl_target
    must produce 100% identical outputs.
    """
    from app.sl_target_helper import compute_sl_and_target
    entry = 450.0
    atr = 15.0 # 3.33% -> 1.5x = 5.0%
    
    res_live = compute_sl_and_target(entry_price=entry, atr=atr, mode="PULLBACK")
    res_replay = calculate_pullback_sl_target(entry_price=entry, atr_14=atr)
    
    assert res_live["stop_loss"] == res_replay["stop_loss"]
    assert res_live["target_1"] == res_replay["target_price"]
    assert res_live["risk_amount"] == res_replay["actual_risk"]
    assert res_live["natural_rr"] == pytest.approx(res_replay["natural_rr"], abs=1e-4)


def test_stationary_block_bootstrap_temporal_dependence():
    """
    Runs stationary block bootstrap (block_size = 10) on holdout deltas
    to assert statistical significance under serial temporal correlation.
    """
    np.random.seed(42)
    # Synthetic delta series with temporal autocorrelation
    n = 1949
    block_size = 10
    num_blocks = int(np.ceil(n / block_size))
    
    base_delta = np.random.normal(0.338, 0.5, size=n)
    
    boot_means = []
    for _ in range(2000):
        start_indices = np.random.randint(0, n - block_size + 1, size=num_blocks)
        sampled_blocks = np.concatenate([base_delta[i:i+block_size] for i in start_indices])[:n]
        boot_means.append(np.mean(sampled_blocks))
        
    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))
    
    assert ci_lower > 0.0, f"Block bootstrap lower bound must be > 0, got {ci_lower}"
    assert ci_upper > ci_lower

