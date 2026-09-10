"""
tests/test_alert_quality_engine.py
Institutional Alert Quality Engine & Excursion Metric Tests
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from alert_quality_engine import AlertQualityEngine


def create_ohlc_sequence(prices):
    """Creates synthetic price dataframe from list of (open, high, low, close) tuples."""
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close"], index=dates)
    return df


def test_quality_ladder_milestones_and_clean_target_hit():
    """Verify +1R, +1.5R, +2R ladder progression on clean winner."""
    # Entry = 100, SL = 95 (Risk = 5). +1R = 105, +1.5R = 107.5, +2R = 110. Target 1 = 110 (+2R)
    prices = [
        (100.0, 106.0, 99.0, 104.0),  # Hits +1R (106 >= 105)
        (104.0, 108.0, 103.0, 107.0), # Hits +1.5R (108 >= 107.5)
        (107.0, 111.0, 106.0, 110.0), # Hits +2R and Target 1 (111 >= 110)
    ]
    df = create_ohlc_sequence(prices)
    res = AlertQualityEngine.evaluate_trade_outcome(
        entry_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        price_df=df,
        scanner="EOD"
    )

    assert res["exit_reason"] == "T1_HIT"
    assert res["realized_rr"] == pytest.approx(2.0)
    assert res["r1_hit_before_sl"] is True
    assert res["r1_5_hit_before_sl"] is True
    assert res["r2_hit_before_sl"] is True
    assert res["max_favorable_excursion_r"] == pytest.approx(2.2)  # (111 - 100) / 5
    assert res["max_adverse_excursion_r"] == pytest.approx(0.2)   # (100 - 99) / 5


def test_post_sl_recovery_diagnostic():
    """Verify post-SL recovery tracking separates tight stop from bad signal."""
    # Entry = 100, SL = 95 (Risk = 5).
    # Bar 1 hits SL (Low = 94.0 -> min excursion -1.2R).
    # Bar 2-4 recover back to 103.0 (+0.6R) and reclaim Entry.
    prices = [
        (100.0, 101.0, 94.0, 96.0),   # Hits SL on bar 0
        (96.0, 98.0, 95.0, 97.0),
        (97.0, 101.0, 96.0, 100.5),   # Reclaims entry (100.5 > 100)
        (100.5, 104.0, 99.0, 103.0),  # High = 104 (+0.8R post-SL)
    ]
    df = create_ohlc_sequence(prices)
    res = AlertQualityEngine.evaluate_trade_outcome(
        entry_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        price_df=df,
        scanner="EOD"
    )

    assert res["exit_reason"] == "SL_HIT"
    assert res["realized_rr"] == pytest.approx(-1.0)
    assert res["post_sl_min_excursion_r"] == pytest.approx(-1.2)  # Deepest Low was 94.0
    assert res["post_sl_recovered_entry"] is True
    assert res["post_sl_max_recovery_r"] == pytest.approx(0.8)   # (104 - 100) / 5
    assert res["post_sl_recovery_bars"] == 2  # Reclaimed entry on 2nd bar after SL


def test_earnings_event_risk_preservation():
    """Verify earnings proximity preserves signal_generated while setting live_trade_allowed to False."""
    snapshot = AlertQualityEngine.build_signal_snapshot(
        symbol="INFY",
        scanner="EOD",
        score=88.0,
        regime="BULL",
        days_to_earnings=2  # Within <= 3 days!
    )

    assert snapshot["signal_generated"] is True
    assert snapshot["event_risk"] is True
    assert snapshot["live_trade_allowed"] is False
