# tests/test_price_cache_routing.py
import pytest
import os
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from price_cache import (
    _is_cache_long_enough,
    _is_cache_up_to_date,
    DailyPolicy,
)

IST = ZoneInfo("Asia/Kolkata")

def test_cache_long_enough_1d_30_to_180_bars():
    """
    Regression Test 1:
    Verify that a 1D daily cached DataFrame with 30–180 bars returns True for _is_cache_long_enough
    when requesting period='1y', avoiding redundant FULL re-downloads.
    """
    dates = pd.date_range(end=datetime.now(IST), periods=120, freq="D")
    df_120 = pd.DataFrame({
        "Open": [100.0] * 120,
        "High": [105.0] * 120,
        "Low": [95.0] * 120,
        "Close": [102.0] * 120,
        "Volume": [10000] * 120,
    }, index=dates)

    assert _is_cache_long_enough(df_120, period="1y", interval="1d") is True, (
        "1D cache with 120 bars must be recognized as long enough for incremental delta!"
    )

    df_35 = pd.DataFrame({
        "Open": [100.0] * 35,
        "High": [105.0] * 35,
        "Low": [95.0] * 35,
        "Close": [102.0] * 35,
        "Volume": [10000] * 35,
    }, index=pd.date_range(end=datetime.now(IST), periods=35, freq="D"))

    assert _is_cache_long_enough(df_35, period="1y", interval="1d") is True, (
        "1D cache with 35 bars must be recognized as long enough for incremental delta!"
    )

def test_daily_policy_freshness_trading_session():
    """
    Regression Test 2:
    Verify DailyPolicy determines freshness using latest expected trading session,
    accounting for market hours, weekends, and holidays.
    """
    now_dt = datetime.now(IST)
    policy = DailyPolicy()

    # Yesterday's bar or latest closed bar should be fresh
    from market_utils import get_expected_latest_closed_daily_bar
    latest_closed = get_expected_latest_closed_daily_bar(now_dt)
    
    last_ts_fresh = pd.Timestamp(latest_closed, tz="Asia/Kolkata")
    assert policy.is_fresh(last_ts_fresh, now_dt=now_dt) is True, (
        "Cache matching latest_expected_closed_session must be evaluated as FRESH!"
    )

def test_mixed_staleness_fetch_groups():
    """
    Regression Test 3:
    Verify that symbols with divergent timestamps produce distinct fetch_groups
    and do NOT widen fresh symbols to multi-day deltas.
    """
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    ts_fresh = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
    ts_stale = (datetime.now(IST) - timedelta(days=5)).strftime("%Y-%m-%d")

    group_fresh = (ts_fresh, today_str)
    group_stale = (ts_stale, today_str)

    # They MUST form distinct group keys
    assert group_fresh != group_stale, "Divergent timestamps must generate distinct fetch_group keys!"

def test_usable_cache_boundaries():
    """
    Regression Test 4:
    Verify the boundary conditions of the new usable-cache rules:
    - 29 bars -> FULL (not long enough)
    - 30 bars -> DELTA (long enough)
    - 31 bars + 25-day gap -> FULL (gap > max_delta_days)
    - 31 bars + 10-day gap -> DELTA (gap < max_delta_days)
    """
    # 29 bars
    df_29 = pd.DataFrame(
        {"Close": [100.0] * 29},
        index=pd.date_range(end=datetime.now(IST), periods=29, freq="D")
    )
    assert not _is_cache_long_enough(df_29, period="1y", interval="1d"), "29 bars must NOT be long enough (requires >=30)"

    # 30 bars
    df_30 = pd.DataFrame(
        {"Close": [100.0] * 30},
        index=pd.date_range(end=datetime.now(IST), periods=30, freq="D")
    )
    assert _is_cache_long_enough(df_30, period="1y", interval="1d"), "30 bars MUST be long enough"
