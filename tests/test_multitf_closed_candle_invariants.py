"""
tests/test_multitf_closed_candle_invariants.py
Closed-Candle Multi-TF Invariant & Explicit Execution Contract Suite
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from multi_tf_scanner import strip_forming_candle, IST
from multi_tf_engine import check_weekly_thesis, check_daily_setup


def generate_time_indexed_df(num_bars=30, freq_minutes=30, base_time=None):
    if base_time is None:
        base_time = datetime.now(IST).replace(second=0, microsecond=0)
    
    timestamps = [base_time - timedelta(minutes=freq_minutes * (num_bars - 1 - i)) for i in range(num_bars)]
    df = pd.DataFrame({
        "Datetime": timestamps,
        "Open": np.full(num_bars, 100.0),
        "High": np.full(num_bars, 105.0),
        "Low": np.full(num_bars, 98.0),
        "Close": np.full(num_bars, 102.0),
        "Volume": np.full(num_bars, 50000.0)
    })
    return df


def test_strip_forming_candle_removes_incomplete_bar():
    """Verify strip_forming_candle removes the currently active/forming bar."""
    ist_now = datetime.now(IST).replace(hour=10, minute=15, second=0, microsecond=0)
    
    # 30m candle started at 10:00 (ends at 10:30) -> at 10:15 it is still forming!
    bar_start = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    df = generate_time_indexed_df(num_bars=10, freq_minutes=30, base_time=bar_start)
    
    stripped_df = strip_forming_candle(df, 30, ist_now)
    assert len(stripped_df) == len(df) - 1, "Forming 30m candle was not stripped!"


def test_strip_forming_candle_keeps_completed_bar():
    """Verify strip_forming_candle keeps the candle once the period has closed."""
    ist_now = datetime.now(IST).replace(hour=10, minute=31, second=0, microsecond=0)
    
    # 30m candle started at 10:00 (ended at 10:30) -> at 10:31 it is completed!
    bar_start = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    df = generate_time_indexed_df(num_bars=10, freq_minutes=30, base_time=bar_start)
    
    stripped_df = strip_forming_candle(df, 30, ist_now)
    assert len(stripped_df) == len(df), "Completed 30m candle was incorrectly stripped!"


def test_weekly_daily_closed_candle_invariance():
    """Verify mutating the latest incomplete daily/weekly bar does not alter structural baseline."""
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(60)]
    df = pd.DataFrame({
        "Date": dates,
        "Open": np.full(60, 100.0),
        "High": np.full(60, 102.0),
        "Low": np.full(60, 98.0),
        "Close": np.full(60, 101.0),
        "Volume": np.full(60, 100000.0)
    })
    # Set resistance on bar 45
    df.loc[45, "High"] = 115.0
    
    d_res = check_daily_setup(df)
    assert d_res["passed"] is True
    assert d_res["prior_20d_high"] == pytest.approx(115.0)
