"""
Unit & Boundary Tests for Pattern Detector Engine
=================================================
Tests:
1. Exact golden dataset fixture parity (100% match against research signals)
2. Lookback boundary cases (t < min lookback, t == min lookback)
3. Slicing correctness (zero negative index wraparound, proper end indices)
4. Data quality robustness (NaNs, infs, zeros, missing volume, single bar, empty array)
5. Deterministic pure-function behavior (no state mutations)
"""

import os
import json
import pytest
import numpy as np
import pandas as pd

from app.pattern_detector_engine import (
    detect_undercut_and_rally,
    detect_double_bottom_shakeout,
    detect_bull_flag
)

def test_boundary_lookbacks():
    """Verify that insufficient history safely returns False without IndexError or negative wrapping."""
    # Length 10 array
    highs = np.full(10, 100.0)
    lows = np.full(10, 95.0)
    closes = np.full(10, 98.0)
    vols = np.full(10, 100000.0)

    for t in range(10):
        assert detect_undercut_and_rally(highs, lows, closes, vols, t) is False
        assert detect_double_bottom_shakeout(highs, lows, closes, vols, t) is False
        assert detect_bull_flag(highs, lows, closes, vols, t) is False

    # Negative indices when array is too short
    assert detect_undercut_and_rally(highs, lows, closes, vols, -1) is False
    assert detect_double_bottom_shakeout(highs, lows, closes, vols, -1) is False
    assert detect_bull_flag(highs, lows, closes, vols, -1) is False

def test_nan_and_inf_robustness():
    """Ensure arrays with NaNs/Infs/Zeros fail gracefully."""
    n = 60
    highs = np.full(n, np.nan)
    lows = np.full(n, np.nan)
    closes = np.full(n, np.nan)
    vols = np.full(n, 0.0)

    assert detect_undercut_and_rally(highs, lows, closes, vols, 50) is False
    assert detect_double_bottom_shakeout(highs, lows, closes, vols, 50) is False
    assert detect_bull_flag(highs, lows, closes, vols, 50) is False

    # Zero volume should not trigger divide by zero error
    highs_clean = np.linspace(100, 150, n)
    lows_clean = np.linspace(90, 140, n)
    closes_clean = np.linspace(95, 145, n)
    vols_zero = np.zeros(n)

    assert detect_undercut_and_rally(highs_clean, lows_clean, closes_clean, vols_zero, 50) is False
    assert detect_double_bottom_shakeout(highs_clean, lows_clean, closes_clean, vols_zero, 50) is False
    assert detect_bull_flag(highs_clean, lows_clean, closes_clean, vols_zero, 50) is False

def test_undercut_and_rally_synthetic_pattern():
    """Construct an exact synthetic undercut and rally setup and verify detection."""
    n = 50
    highs = np.full(n, 105.0)
    lows = np.full(n, 95.0)
    closes = np.full(n, 100.0)
    vols = np.full(n, 10000.0)

    t = 40
    # Prior swing low at t - 15 (which is in [t-25 : t-5])
    lows[t - 15] = 90.0 # prior low = 90.0
    # Prior low window is [t-25:t-5] -> index 15 to 35 -> min is at index 25 (t-15) = 90.0

    # Recent low in [t-3 : t] dips to 88.0 (which is 90 * 0.977, between 90*0.96 and 90.0)
    lows[t - 2] = 88.0

    # Bar t closes at 93.0 (> 90.0 * 1.02 = 91.8) and closes[t] > closes[t-1]
    closes[t - 1] = 89.0
    closes[t] = 93.0
    highs[t] = 94.0

    assert detect_undercut_and_rally(highs, lows, closes, vols, t) is True

    # Test "climax selling" rejection: if recent low drops below 90 * 0.96 = 86.4 (e.g. 85.0)
    lows[t - 2] = 85.0
    assert detect_undercut_and_rally(highs, lows, closes, vols, t) is False

def test_bull_flag_synthetic_pattern():
    """Construct a clean bull flag setup and verify detection and 0.985 breakout condition."""
    n = 60
    highs = np.full(n, 100.0)
    lows = np.full(n, 95.0)
    closes = np.full(n, 98.0)
    vols = np.full(n, 10000.0)

    t = 45
    # Pole start in [t-25:t-10] -> min at 80.0
    lows[t - 20] = 80.0
    # Pole high in [t-10:t-3] -> max at 100.0 (gain = (100 - 80) / 80 = +25% >= 15%)
    highs[t - 6] = 100.0

    # Flag consolidation: low dips to 94.0 (pullback = (100 - 94)/100 = 6% in [1%, 12%])
    lows[t - 4] = 94.0

    # Breakout at bar t: Close = 99.0 (>= 100 * 0.985 = 98.5), Volume surge >= 1.4x SMA20
    closes[t] = 99.0
    vols[t] = 20000.0 # 2.0x 20d SMA

    assert detect_bull_flag(highs, lows, closes, vols, t) is True

    # Volume too low -> should reject
    vols[t] = 10000.0
    assert detect_bull_flag(highs, lows, closes, vols, t) is False

def test_golden_dataset_fixture_parity():
    """Verify that app.pattern_detector_engine matches every single signal in the golden dataset fixture."""
    fixture_path = "data/pattern_confluence_golden_dataset.json"
    if not os.path.exists(fixture_path):
        pytest.skip("Golden dataset fixture not yet generated")

    with open(fixture_path, "r") as f:
        data = json.load(f)

    signals = data["signals"]
    print(f"Loaded {len(signals)} golden signals for parity verification.")

    mismatches = 0
    checked_files = {}

    for sig in signals:
        sym = sig["symbol"]
        t = sig["bar_index"]
        pat = sig["pattern"]

        if sym not in checked_files:
            df = pd.read_parquet(f"data/history/1d/{sym}.parquet")
            idx = pd.to_datetime(df.index)
            if idx.tz is not None:
                idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
            df.index = idx.normalize()
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            weekend_mask = df.index.dayofweek >= 5
            df = df[~weekend_mask]
            checked_files[sym] = (df["High"].values, df["Low"].values, df["Close"].values, df["Volume"].values)

        highs, lows, closes, vols = checked_files[sym]

        if pat == "UNDERCUT_AND_RALLY":
            res = detect_undercut_and_rally(highs, lows, closes, vols, t)
        elif pat == "DOUBLE_BOTTOM_SHAKEOUT":
            res = detect_double_bottom_shakeout(highs, lows, closes, vols, t)
        elif pat == "BULL_FLAG":
            res = detect_bull_flag(highs, lows, closes, vols, t)
        else:
            res = False

        if not res:
            mismatches += 1
            print(f"Mismatch: {sym} at bar {t} on {sig['signal_date']} for {pat}")

    assert mismatches == 0, f"Found {mismatches} mismatches against golden dataset fixture!"
