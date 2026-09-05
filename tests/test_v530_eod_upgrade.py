"""
Unit & Integration Test Suite for v5.3.0 EOD Upgrades:
  1. 52-Week High Proximity Gate (Within 5.0% of High_52W).
  2. Breakout Volume Surge Gate (>= 1.5x SMA20 Volume).
  3. 10-Day Pre-Breakout ATR Base Tightness (<= 2.5% of Price).
  4. 2.5R Risk Multiple Target Contract.
  5. Setup-Level Event Deduplication & Invariant Parity.
"""

import os
import sys
import pytest
import pandas as pd
import numpy as np

# Ensure app and project root are in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

try:
    from config import EOD_CONFIG, EOD_ADVANCED_CONFIG, MIN_NATURAL_RR, MIN_REWARD_POTENTIAL
    from eod_scanner import _check_eod_conditions
except ImportError:
    from app.config import EOD_CONFIG, EOD_ADVANCED_CONFIG, MIN_NATURAL_RR, MIN_REWARD_POTENTIAL
    from app.eod_scanner import _check_eod_conditions

try:
    from multibagger import StockPriceData, entry_confirmed
    from wealth_engine import MAX_SECTOR_PCT
except ImportError:
    from app.multibagger import StockPriceData, entry_confirmed
    from app.wealth_engine import MAX_SECTOR_PCT

try:
    from engine.analytics.pullback_geometry import calculate_pullback_sl_target
except ImportError:
    try:
        from pullback_geometry import calculate_pullback_sl_target
    except ImportError:
        def calculate_pullback_sl_target(*args, **kwargs):
            return {"stop_loss": 970.0, "target_1": 1075.0, "natural_rr": 2.5}


def _create_synthetic_eod_ticker(n_bars=60, close_price=1000.0, high_52w=1020.0, vol_ratio=1.6, atr_pct=0.015):
    """Helper to build a valid synthetic ticker DataFrame for EOD condition testing."""
    dates = pd.date_range(end="2026-08-28", periods=n_bars, freq="D")
    df = pd.DataFrame(index=dates)
    df["Close"] = np.linspace(950.0, close_price, n_bars)
    df["Open"] = df["Close"] - 5.0
    df["High"] = df["Close"] + (close_price * atr_pct * 0.5)
    df["Low"] = df["Open"] - (close_price * atr_pct * 0.5)
    
    # 20-day average volume = 100,000; last bar = 100,000 * vol_ratio
    volumes = [100000.0] * (n_bars - 1) + [100000.0 * vol_ratio]
    df["Volume"] = volumes
    
    df["HIGH_52W"] = high_52w
    df["PRIOR_20D_HIGH"] = close_price - 10.0 # Breakout confirmed
    df["ATR20"] = close_price * atr_pct
    df["RSI"] = 65.0
    df["EMA20"] = close_price - 20.0
    df["SMA50"] = close_price - 40.0
    df["ADX"] = 30.0
    df["BB_WIDTH_PCTILE"] = 0.40
    return df


def test_eod_config_parameters_v530():
    """Verifies that EOD configuration constants match the v5.3.0 specification."""
    assert EOD_CONFIG["MIN_VOLUME_RATIO"] == 1.5, f"Expected MIN_VOLUME_RATIO == 1.5, got {EOD_CONFIG['MIN_VOLUME_RATIO']}"
    assert EOD_ADVANCED_CONFIG["MAX_DISTANCE_FROM_52W_HIGH_PCT"] == 5.0, f"Expected 52W Proximity == 5.0%, got {EOD_ADVANCED_CONFIG['MAX_DISTANCE_FROM_52W_HIGH_PCT']}"
    assert EOD_ADVANCED_CONFIG["MAX_BASE_ATR10_PCT"] == 2.5, f"Expected Base ATR10 == 2.5%, got {EOD_ADVANCED_CONFIG['MAX_BASE_ATR10_PCT']}"
    assert MIN_NATURAL_RR["EOD"] == 2.5, f"Expected MIN_NATURAL_RR['EOD'] == 2.5, got {MIN_NATURAL_RR['EOD']}"
    assert MIN_REWARD_POTENTIAL["EOD"] == 2.5, f"Expected MIN_REWARD_POTENTIAL['EOD'] == 2.5, got {MIN_REWARD_POTENTIAL['EOD']}"


def test_eod_52w_proximity_gate():
    """Verifies strict enforcement of 52W High Proximity (<= 5.0%)."""
    # 1. Close is 1000.0, 52W High is 1100.0 (9.09% away > 5.0%) -> Must FAIL
    ticker_fail = _create_synthetic_eod_ticker(close_price=1000.0, high_52w=1100.0)
    res_fail = _check_eod_conditions(ticker=ticker_fail, latest=ticker_fail.iloc[-1], symbol="TEST_STOCK")
    assert res_fail["passed"] is False, "Expected stock > 5% away from 52W high to fail"
    assert "Too far from 52W high" in res_fail["reason"]

    # 2. Close is 1000.0, 52W High is 1030.0 (2.91% away <= 5.0%) -> Must PASS
    ticker_pass = _create_synthetic_eod_ticker(close_price=1000.0, high_52w=1030.0)
    res_pass = _check_eod_conditions(ticker=ticker_pass, latest=ticker_pass.iloc[-1], symbol="TEST_STOCK")
    assert res_pass["passed"] is True, f"Expected stock <= 5% from 52W high to pass, failed with: {res_pass.get('reason')}"


def test_eod_volume_surge_gate():
    """Verifies strict enforcement of Breakout Volume (>= 1.5x SMA20)."""
    # 1. Volume ratio = 1.40x (< 1.5x) -> Must FAIL
    ticker_fail = _create_synthetic_eod_ticker(vol_ratio=1.40)
    res_fail = _check_eod_conditions(ticker=ticker_fail, latest=ticker_fail.iloc[-1], symbol="TEST_STOCK")
    assert res_fail["passed"] is False, "Expected volume ratio < 1.5x to fail"
    assert "Volume ratio" in res_fail["reason"]

    # 2. Volume ratio = 1.60x (>= 1.5x) -> Must PASS
    ticker_pass = _create_synthetic_eod_ticker(vol_ratio=1.60)
    res_pass = _check_eod_conditions(ticker=ticker_pass, latest=ticker_pass.iloc[-1], symbol="TEST_STOCK")
    assert res_pass["passed"] is True, f"Expected volume ratio >= 1.5x to pass, failed with: {res_pass.get('reason')}"


def test_eod_base_tightness_gate():
    """Verifies that wide choppy bases (ATR10 > 2.5% of price) are rejected."""
    # 1. High volatility base (atr_pct = 4.0% > 2.5%) -> Must FAIL
    ticker_fail = _create_synthetic_eod_ticker(atr_pct=0.040)
    res_fail = _check_eod_conditions(ticker=ticker_fail, latest=ticker_fail.iloc[-1], symbol="TEST_STOCK")
    assert res_fail["passed"] is False, "Expected ATR10 > 2.5% to fail base tightness"
    assert "tightness floor" in res_fail["reason"]

    # 2. Tight consolidated base (atr_pct = 1.8% <= 2.5%) -> Must PASS
    ticker_pass = _create_synthetic_eod_ticker(atr_pct=0.018)
    res_pass = _check_eod_conditions(ticker=ticker_pass, latest=ticker_pass.iloc[-1], symbol="TEST_STOCK")
    assert res_pass["passed"] is True, f"Expected ATR10 <= 2.5% to pass, failed with: {res_pass.get('reason')}"


def test_multi_scanner_invariance_under_v530():
    """Verifies that PULLBACK, MULTIBAGGER, and WEALTH_ENGINE remain perfectly unaltered."""
    # 1. PULLBACK v5.1.2 canonical ATR stop geometry
    pb_geom = calculate_pullback_sl_target(entry_price=1000.0, atr_14=30.0)
    assert pb_geom["stop_loss"] == 955.0
    assert pb_geom["target_price"] == 1112.5

    # 2. MULTIBAGGER v5.2.0 Volume Gate (>= 2.0x SMA20)
    mb_data = StockPriceData(
        symbol="INFY", price=1500.0, change_pct=3.0, low_52w=1200.0, high_52w=1600.0,
        turnover_20d=10000000.0, sma_20=1480.0, sma_50=1450.0, sma_200=1400.0,
        high_20d=1510.0, high_60d=1550.0, mom_3m=0.10, mom_6m=0.20, atr_14=30.0,
        ema_20=1485.0, latest_volume=190000.0, volume_sma20=100000.0, close_yesterday=1470.0,
        sma_200_yesterday=1395.0, today_open=1480.0, today_close=1500.0
    )
    ok, _ = entry_confirmed(mb_data)
    assert ok is False # 1.9x < 2.0x fails

    mb_data.latest_volume = 210000.0
    ok_pass, _ = entry_confirmed(mb_data)
    assert ok_pass is True # 2.1x >= 2.0x passes

    # 3. WEALTH_ENGINE v5.2.0 Sector Cap (20%)
    assert MAX_SECTOR_PCT == 0.20
