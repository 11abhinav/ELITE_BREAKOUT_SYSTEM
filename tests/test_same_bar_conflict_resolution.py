"""
tests/test_same_bar_conflict_resolution.py
Same-Bar Collision & Intrabar Path Resolution Assertion Suite
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from alert_quality_engine import AlertQualityEngine


def test_conservative_same_bar_conflict_without_intraday():
    """Verify that when High >= T1 and Low <= SL on the same daily bar, conservative -1.0R is assigned."""
    # Entry = 100, SL = 95, T1 = 110. Bar has High = 112 (exceeds T1) and Low = 93 (breaches SL).
    prices = [
        (100.0, 112.0, 93.0, 105.0)
    ]
    dates = [datetime(2026, 1, 1)]
    df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close"], index=dates)

    res = AlertQualityEngine.evaluate_trade_outcome(
        entry_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        price_df=df,
        scanner="EOD"
    )

    assert res["same_bar_conflict"] is True
    assert res["exit_reason"] == "SAME_BAR_CONFLICT_SL"
    assert res["realized_rr"] == pytest.approx(-1.0)


def test_intraday_resolution_stop_first():
    """Verify intraday tick data correctly resolves STOP_FIRST on same-bar collision."""
    prices = [(100.0, 112.0, 93.0, 105.0)]
    dates = [pd.to_datetime("2026-01-01")]
    df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close"], index=dates)

    # Intraday 5m ticks: first drops to 94 (SL triggered), then rallies to 112
    intraday_ticks = [
        ("2026-01-01 09:15:00", 100.0, 101.0, 94.0, 95.0),
        ("2026-01-01 09:30:00", 95.0, 112.0, 95.0, 110.0)
    ]
    df_intra = pd.DataFrame(
        [(t[1], t[2], t[3], t[4]) for t in intraday_ticks],
        columns=["Open", "High", "Low", "Close"],
        index=[pd.to_datetime(t[0]) for t in intraday_ticks]
    )

    res = AlertQualityEngine.evaluate_trade_outcome(
        entry_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        price_df=df,
        scanner="EOD",
        intraday_df=df_intra
    )

    assert res["same_bar_conflict"] is True
    assert res["exit_reason"] == "SAME_BAR_CONFLICT_SL"
    assert res["realized_rr"] == pytest.approx(-1.0)


def test_intraday_resolution_target_first():
    """Verify intraday tick data correctly resolves TARGET_FIRST on same-bar collision."""
    prices = [(100.0, 112.0, 93.0, 105.0)]
    dates = [pd.to_datetime("2026-01-01")]
    df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close"], index=dates)

    # Intraday 5m ticks: first rallies to 112 (Target hit), then collapses to 93
    intraday_ticks = [
        ("2026-01-01 09:15:00", 100.0, 112.0, 100.0, 111.0),
        ("2026-01-01 14:30:00", 110.0, 110.0, 93.0, 94.0)
    ]
    df_intra = pd.DataFrame(
        [(t[1], t[2], t[3], t[4]) for t in intraday_ticks],
        columns=["Open", "High", "Low", "Close"],
        index=[pd.to_datetime(t[0]) for t in intraday_ticks]
    )

    res = AlertQualityEngine.evaluate_trade_outcome(
        entry_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        price_df=df,
        scanner="EOD",
        intraday_df=df_intra
    )

    assert res["same_bar_conflict"] is True
    assert res["exit_reason"] == "T1_HIT"
    assert res["realized_rr"] == pytest.approx(2.0)  # (110 - 100) / 5
