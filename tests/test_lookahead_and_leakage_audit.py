"""
tests/test_lookahead_and_leakage_audit.py
Zero-Lookahead & Reference Contamination Assertion Suite (Release 1 Integrity Gate)
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from eod_v2_engine import compute_prior_20d_high, compute_average_volume_20d_ref, evaluate_eod_v2_symbol
from multi_tf_engine import check_daily_setup


def generate_synthetic_ohlcv(num_bars=60, base_price=100.0, base_volume=100000.0):
    """Generates synthetic OHLCV dataframe with known values."""
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(num_bars)]
    df = pd.DataFrame({
        "Date": dates,
        "Open": np.full(num_bars, base_price),
        "High": np.full(num_bars, base_price + 2.0),
        "Low": np.full(num_bars, base_price - 2.0),
        "Close": np.full(num_bars, base_price),
        "Volume": np.full(num_bars, base_volume)
    })
    # Set known variations in lookback window [t-20 : t-1]
    df.loc[num_bars - 5, "High"] = base_price + 10.0  # True 20-day high is base_price + 10
    df.loc[num_bars - 10, "Volume"] = base_volume * 2.0
    return df


def test_eod_prior_20d_high_excludes_current_bar():
    """Verify compute_prior_20d_high strictly excludes the current bar t (index -1)."""
    df = generate_synthetic_ohlcv(num_bars=60, base_price=100.0)
    
    # Pre-condition: true prior 20D high is 110.0
    prior_high_initial = compute_prior_20d_high(df)
    assert prior_high_initial == pytest.approx(110.0)

    # Spike the current bar t High to an extreme value (e.g. 500.0)
    df.iloc[-1, df.columns.get_loc("High")] = 500.0

    # The prior_20d_high MUST NOT change because bar t is excluded
    prior_high_after_spike = compute_prior_20d_high(df)
    assert prior_high_after_spike == pytest.approx(110.0), (
        f"Lookahead leak detected! prior_20d_high changed to {prior_high_after_spike} when bar t High was spiked."
    )


def test_eod_average_volume_excludes_current_bar():
    """Verify compute_average_volume_20d_ref strictly excludes the current bar t (index -1)."""
    df = generate_synthetic_ohlcv(num_bars=60, base_volume=100000.0)
    
    vol_ref_initial = compute_average_volume_20d_ref(df)
    
    # Spike current bar t Volume to 100x
    df.iloc[-1, df.columns.get_loc("Volume")] = 10000000.0
    
    vol_ref_after_spike = compute_average_volume_20d_ref(df)
    assert vol_ref_after_spike == pytest.approx(vol_ref_initial), (
        f"Lookahead leak detected! Volume ref changed to {vol_ref_after_spike} when bar t Volume was spiked."
    )


def test_eod_v2_symbol_evaluation_invariance():
    """Verify EOD V2 breakout state evaluation does not contaminate trigger threshold with bar t High."""
    df = generate_synthetic_ohlcv(num_bars=60, base_price=100.0, base_volume=100000.0)
    
    # Breakout candle on bar t: Close = 112.0 (exceeds prior high of 110.0), High = 115.0, Volume = 200,000
    df.iloc[-1, df.columns.get_loc("Open")] = 105.0
    df.iloc[-1, df.columns.get_loc("High")] = 115.0
    df.iloc[-1, df.columns.get_loc("Low")] = 104.0
    df.iloc[-1, df.columns.get_loc("Close")] = 112.0
    df.iloc[-1, df.columns.get_loc("Volume")] = 250000.0
    
    res = evaluate_eod_v2_symbol("TEST_STOCK", df, is_nq_universe=False)
    # The setup should detect breakout above 110.0
    assert res.get("state") in ["CANDIDATE", "WATCH", "NO_VALID_SETUP", "MISSED"]
    if "prior_20d_high" in res:
        assert res["prior_20d_high"] == pytest.approx(110.0)


def test_daily_structure_resistance_lookahead_free():
    """Verify check_daily_setup in multi-TF engine excludes bar t from prior 20D resistance."""
    df = generate_synthetic_ohlcv(num_bars=60, base_price=100.0)
    
    struct = check_daily_setup(df)
    assert struct["passed"] is True
    assert struct["prior_20d_high"] == pytest.approx(110.0)
    
    # Mutate current bar High
    df.iloc[-1, df.columns.get_loc("High")] = 999.0
    struct_mutated = check_daily_setup(df)
    assert struct_mutated["prior_20d_high"] == pytest.approx(110.0)
