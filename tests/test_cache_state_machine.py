import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import json

from app.price_cache import (
    fetch_watchlist_data,
    _cache,
    _is_cache_long_enough,
    _is_cache_up_to_date
)
from app.core_enums import ProviderResult
from app.validation import MarketData
from tests.factories import make_price_history

IST = ZoneInfo("Asia/Kolkata")

@pytest.fixture(autouse=True)
def setup_test_env(tmp_path):
    """Reset the global cache and use a temporary directory for Parquet files.
    
    Patches both DB bundle functions to prevent the background HistoryUpload thread
    (spawned by _download_all_robust) from attempting a real DB connection in tests.
    """
    _cache.clear()
    
    with patch("app.price_cache.DATA_DIR", str(tmp_path)), \
         patch("app.price_cache.upsert_fetch_error"), \
         patch("app.price_cache.delete_fetch_error_on_success", create=True), \
         patch("app.data_fetch_status.mark_failure"), \
         patch("app.data_fetch_status.mark_success"), \
         patch("app.database.upload_history_bundle_to_db"), \
         patch("app.database.restore_history_bundle_from_db"):
        yield tmp_path
        
    _cache.clear()

@pytest.fixture
def mock_watchlist():
    return pd.DataFrame({"Stock": ["TEST1.NS", "TEST2.NS"]})

@pytest.fixture
def base_history():
    df = make_price_history("TEST1.NS").with_start_date("2023-01-01").with_periods(300).build()
    df.index.name = "Date"
    return df

@pytest.fixture
def base_history_2():
    df = make_price_history("TEST2.NS").with_start_date("2023-01-01").with_periods(300).build()
    df.index.name = "Date"
    return df

@patch("app.price_cache.get_fetcher")
def test_no_cache_full_fetch_healthy(mock_get_fetcher, mock_watchlist, base_history, base_history_2):
    """
    State Transition: No Cache -> Full fetch succeeds -> Healthy Cache
    """
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    # Provider returns good data
    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=base_history, quality_report=MagicMock(quality_score=95, row_count=300), source="yf", stale=False, used_fallback=False),
        "TEST2.NS": MarketData(dataframe=base_history_2, quality_report=MagicMock(quality_score=95, row_count=300), source="yf", stale=False, used_fallback=False)
    }

    result = fetch_watchlist_data(mock_watchlist, period="1y", interval="1d")

    # 1. Fetcher called without range_from (FULL fetch)
    call_kwargs = mock_fetcher.get_batch_ohlcv.call_args[1]
    assert call_kwargs.get("range_from") is None
    
    # 2. Results returned correctly
    assert len(result) == 2
    assert "TEST1.NS" in result
    assert "TEST2.NS" in result
    
    # 3. Cache populated at unified 2y key (period '1y' → '2y' via UNIFIED_2Y_CACHE_v1.0)
    cache_key = ("1d", "2y")
    assert cache_key in _cache
    assert not _cache[cache_key]["TEST1.NS"]["data"].empty

@patch("app.price_cache.get_fetcher")
def test_healthy_cache_delta_fetch(mock_get_fetcher, mock_watchlist, base_history, setup_test_env):
    """
    State Transition: Healthy Cache -> Delta succeeds -> Healthy Cache
    """
    # 1. Setup healthy cache missing 1 day
    now = datetime.now(IST)
    # Ensure it's treated as market open/closed correctly for the test by simulating a time
    # We will simulate that the cache's last date is yesterday
    yesterday = now - timedelta(days=1)

    # Modify base history to end yesterday
    dates = pd.date_range(end=yesterday, periods=300, freq="D", tz=IST)
    cached_df = pd.DataFrame({
        "Open": [100.0] * 300,
        "High": [105.0] * 300,
        "Low": [95.0] * 300,
        "Close": [100.0] * 300,
        "Volume": [1000] * 300
    }, index=dates)
    cached_df.index.name = "Date"
    
    # Save to mock DATA_DIR
    history_dir = setup_test_env / "history" / "1d"
    history_dir.mkdir(parents=True, exist_ok=True)
    cached_df.to_parquet(history_dir / "TEST1.NS.parquet")
    
    watchlist = pd.DataFrame({"Stock": ["TEST1.NS"]})
    
    # 2. Setup mock fetcher for DELTA
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    delta_dates = pd.date_range(start=yesterday, end=now, freq="D", tz=IST)
    delta_df = pd.DataFrame({"Close": [105.0] * len(delta_dates)}, index=delta_dates)
    delta_df.index.name = "Date"
    
    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=delta_df, quality_report=MagicMock(quality_score=95, row_count=len(delta_df)), source="yf", stale=False, used_fallback=False)
    }

    # 3. Execute
    with patch("app.price_cache._is_cache_up_to_date", return_value=False):
        result = fetch_watchlist_data(watchlist, period="1y", interval="1d")

    # 4. Verify DELTA fetch was requested
    call_kwargs = mock_fetcher.get_batch_ohlcv.call_args[1]
    assert call_kwargs.get("range_from") is not None
    
    # 5. Verify Merge
    merged_df = result["TEST1.NS"]
    assert len(merged_df) > 300 # Should contain old + new
    assert merged_df["Close"].iloc[-1] == 105.0 # Latest price updated

@patch("app.price_cache.get_fetcher")
def test_healthy_cache_already_up_to_date(mock_get_fetcher, setup_test_env):
    """
    State Transition: Healthy Cache -> Already Up-to-Date -> No Fetch
    """
    now = datetime.now(IST)
    dates = pd.date_range(end=now, periods=300, freq="D", tz=IST)
    cached_df = pd.DataFrame({
        "Open": [100.0] * 300,
        "High": [105.0] * 300,
        "Low": [95.0] * 300,
        "Close": [100.0] * 300,
        "Volume": [1000] * 300
    }, index=dates)
    cached_df.index.name = "Date"
    
    history_dir = setup_test_env / "history" / "1d"
    history_dir.mkdir(parents=True, exist_ok=True)
    cached_df.to_parquet(history_dir / "TEST1.NS.parquet")
    
    watchlist = pd.DataFrame({"Stock": ["TEST1.NS"]})
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    with patch("app.price_cache._is_cache_up_to_date", return_value=True), \
         patch("app.price_cache._is_cache_long_enough", return_value=True):
        result = fetch_watchlist_data(watchlist, period="1y", interval="1d")
        
    # Verify no API call made
    mock_fetcher.get_batch_ohlcv.assert_not_called()
    assert not result["TEST1.NS"].empty
    
@patch("app.price_cache.get_fetcher")
def test_provider_returns_empty_dataset(mock_get_fetcher):
    """
    Failure Mode: Provider returns empty dataset -> Return None (if no cache) or Stale Cache
    """
    watchlist = pd.DataFrame({"Stock": ["TEST1.NS"]})
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    # Return empty DataFrame
    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=pd.DataFrame(), quality_report=None, source="yf", stale=False, used_fallback=False)
    }
    
    result = fetch_watchlist_data(watchlist, period="1y", interval="1d")
    
    assert result["TEST1.NS"] is None

@patch("app.price_cache.get_fetcher")
def test_provider_returns_one_row_cache_repaired(mock_get_fetcher, base_history, setup_test_env):
    """
    Recovery Path: Healthy Cache -> Bad fetch (1 row) -> Keep Cache (stale) -> Next Fetch Healthy -> Cache Repaired
    """
    # 1. Start with healthy cache
    base_history.index = pd.to_datetime(base_history.index).tz_localize(IST)
    history_dir = setup_test_env / "history" / "1d"
    history_dir.mkdir(parents=True, exist_ok=True)
    base_history.to_parquet(history_dir / "TEST1.NS.parquet")
    
    watchlist = pd.DataFrame({"Stock": ["TEST1.NS"]})
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    # 2. Bad fetch (1 row)
    bad_df = pd.DataFrame({"Close": [110.0]}, index=[base_history.index[-1] + timedelta(days=1)])
    bad_df.index.name = "Date"
    
    class MockQR:
        quality_score = 10
        row_count = 1
        is_valid = True
        status = "OK"
        warnings = []

    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=bad_df, quality_report=MockQR(), source="yf", stale=False, used_fallback=False)
    }
    
    with patch("app.price_cache._is_cache_up_to_date", return_value=False), \
         patch("app.price_cache._is_cache_long_enough", return_value=False):
        result = fetch_watchlist_data(watchlist, period="1y", interval="1d")
        
    # Should retain the old cache, marked as stale
    df = result["TEST1.NS"]
    assert len(df) == 300
    assert df.attrs.get('is_stale') is True
    
    # 3. Next Fetch Healthy
    good_df = pd.DataFrame({"Close": [110.0, 115.0]}, index=[base_history.index[-1] + timedelta(days=1), base_history.index[-1] + timedelta(days=2)])
    good_df.index.name = "Date"
    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=good_df, quality_report=MagicMock(quality_score=100, row_count=2), source="yf", stale=False, used_fallback=False)
    }
    
    # Reset TTL to trigger fetch again (set it far in the past)
    # [VERSION: UNIFIED_2Y_CACHE_v1.0] period '1y' is standardized to '2y' internally
    _cache[("1d", "2y")]["TEST1.NS"]["ts"] = -1000000
    
    with patch("app.price_cache._is_cache_up_to_date", return_value=False):
        result2 = fetch_watchlist_data(watchlist, period="1y", interval="1d")
        
    df2 = result2["TEST1.NS"]
    assert len(df2) == 302
    assert df2.attrs.get('is_stale', False) is False # No longer stale
    
@patch("app.price_cache.get_fetcher")
def test_partial_symbol_failures(mock_get_fetcher):
    """
    Failure Mode: Partial symbol failures (95 succeed, 5 fail).
    Verify that succeeding symbols are merged, failing symbols return None/stale cache.
    """
    watchlist = pd.DataFrame({"Stock": ["GOOD.NS", "BAD.NS"]})
    mock_fetcher = MagicMock()
    mock_get_fetcher.return_value = mock_fetcher
    
    good_df = make_price_history("GOOD.NS").with_periods(100).build()
    
    # Good succeeds, Bad fails (empty)
    mock_fetcher.get_batch_ohlcv.return_value = {
        "GOOD.NS": MarketData(dataframe=good_df, quality_report=MagicMock(quality_score=95, row_count=100), source="yf", stale=False, used_fallback=False),
        "BAD.NS": MarketData(dataframe=pd.DataFrame(), quality_report=None, source="yf", stale=False, used_fallback=False)
    }
    
    result = fetch_watchlist_data(watchlist, period="1y", interval="1d")
    
    assert not result["GOOD.NS"].empty
    assert result["BAD.NS"] is None

def test_cache_delta_vs_full_classification():
    """
    Verify DELTA vs FULL classification logic based on cache length and dates.
    """
    now = datetime.now(IST)
    
    # 1. Empty dataframe -> Not long enough -> FULL
    assert _is_cache_long_enough(pd.DataFrame(), "1y") == False
    
    # 2. Missing many days (e.g. 100 bars for 1y request) -> FULL
    dates = pd.date_range(end=now, periods=100, freq="D", tz=IST)
    df_short = pd.DataFrame({"Close": [100.0] * 100}, index=dates)
    assert _is_cache_long_enough(df_short, "1y") == False
    
    # 3. Missing a few days (e.g. 250 bars for 1y request) -> DELTA
    dates_long = pd.date_range(end=now, periods=250, freq="D", tz=IST)
    df_long = pd.DataFrame({"Close": [100.0] * 250}, index=dates_long)
    assert _is_cache_long_enough(df_long, "1y") == True

def test_cache_integrity(base_history, setup_test_env):
    """
    Cache Invariants: Existing history is never lost during successful merge.
    Duplicate rows are not introduced. Data remains ordered correctly.
    """
    from app.price_cache import _download_all_robust
    
    # Setup cache
    base_history.index = pd.to_datetime(base_history.index).tz_localize(IST)
    history_dir = setup_test_env / "history" / "1d"
    history_dir.mkdir(parents=True, exist_ok=True)
    base_history.to_parquet(history_dir / "TEST1.NS.parquet")
    
    watchlist = pd.DataFrame({"Stock": ["TEST1.NS"]})
    
    # Create overlapping new data (last 5 days + 2 new days)
    new_dates = pd.date_range(start=base_history.index[-5], periods=7, freq="D", tz=IST)
    new_df = pd.DataFrame({"Close": [101.0] * 7}, index=new_dates) # New values
    new_df.index.name = "Date"
    
    mock_fetcher = MagicMock()
    mock_fetcher.get_batch_ohlcv.return_value = {
        "TEST1.NS": MarketData(dataframe=new_df, quality_report=MagicMock(quality_score=95, row_count=7), source="yf", stale=False, used_fallback=False)
    }
    
    with patch("app.price_cache.get_fetcher", return_value=mock_fetcher), \
         patch("app.price_cache._is_cache_up_to_date", return_value=False), \
         patch("app.price_cache._is_cache_long_enough", return_value=True):
        
        # Test the robust download directly to observe the merge
        result = _download_all_robust(watchlist, "1y", "1d")
        
    merged_df = result["TEST1.NS"]
    
    # Verify no duplicates
    assert not merged_df.index.duplicated().any()
    
    # Verify length (300 base - 5 overlap + 7 new = 302)
    assert len(merged_df) == 302
    
    # Verify ordering
    assert merged_df.index.is_monotonic_increasing
    
    # Verify overwrite (the last 5 days should have the new value 101.0, not the old value)
    assert merged_df["Close"].iloc[-1] == 101.0
    assert merged_df["Close"].iloc[-6] == 101.0


def test_institutional_trading_calendar_freshness():
    """
    Test edge cases for get_expected_latest_closed_daily_bar & DailyPolicy:
    1. Monday 09:20 AM (market open) -> expected closed bar is Friday
    2. Tuesday 08:45 AM (pre-market) -> expected closed bar is Monday
    3. Tuesday 15:45 PM (post-market) -> expected closed bar is Tuesday
    4. Saturday 11:00 AM (weekend) -> expected closed bar is Friday
    """
    from app.market_utils import get_expected_latest_closed_daily_bar
    from app.price_cache import DailyPolicy

    # 1. Monday 09:20 AM (market open) - 2026-08-03 was Monday
    mon_open = datetime(2026, 8, 3, 9, 20, tzinfo=IST)
    assert get_expected_latest_closed_daily_bar(mon_open) == datetime(2026, 7, 31, tzinfo=IST).date()

    # 2. Tuesday 08:45 AM (pre-market) - 2026-08-04 was Tuesday
    tue_pre = datetime(2026, 8, 4, 8, 45, tzinfo=IST)
    assert get_expected_latest_closed_daily_bar(tue_pre) == datetime(2026, 8, 3, tzinfo=IST).date()

    # 3. Tuesday 15:45 PM (post-market)
    tue_post = datetime(2026, 8, 4, 15, 45, tzinfo=IST)
    assert get_expected_latest_closed_daily_bar(tue_post) == datetime(2026, 8, 4, tzinfo=IST).date()

    # 4. Saturday 11:00 AM (weekend) - 2026-08-08 was Saturday
    sat_noon = datetime(2026, 8, 8, 11, 0, tzinfo=IST)
    assert get_expected_latest_closed_daily_bar(sat_noon) == datetime(2026, 8, 7, tzinfo=IST).date()

    # Test DailyPolicy is_fresh
    policy = DailyPolicy()
    fri_ts = pd.Timestamp("2026-07-31")
    assert policy.is_fresh(fri_ts, mon_open) is True
