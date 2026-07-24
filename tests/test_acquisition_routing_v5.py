# =====================================================================================
# tests/test_acquisition_routing_v5.py
# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Comprehensive acquisition & routing tests
# =====================================================================================

import sys
import os
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from data_providers.provider_selector import selector
from data_provider import AutoSwitchingFetcher
from price_cache import validate_ohlcv_structure, compute_ohlcv_hash, CACHE_SCHEMA_VERSION, INDICATOR_VERSION
from indicator_executor import IndicatorExecutor
from pipeline_telemetry import PipelineTelemetry
import config

def test_provider_selector_routing():
    # 1. Dataset-level resolution
    daily_providers = selector.get_providers("price_1d")
    assert daily_providers[0] == "yahoo"

    intraday_providers = selector.get_providers("price_15m")
    assert intraday_providers[0] == "fyers"

    live_providers = selector.get_providers("live_quotes", fetch_type="live_quotes")
    assert live_providers[0] == "fyers"

def test_provider_capabilities_filtering():
    # Capability check for bulk operations
    bulk_providers = selector.get_providers("price_1d", required_capability="bulk")
    assert "yahoo" in bulk_providers
    assert "bse" in bulk_providers

def test_ohlcv_structure_validation():
    dates = pd.date_range("2025-01-01", periods=10, freq="D")
    valid_df = pd.DataFrame({
        "Date": dates,
        "Open": [100.0] * 10,
        "High": [105.0] * 10,
        "Low": [95.0] * 10,
        "Close": [102.0] * 10,
        "Volume": [1000] * 10
    })
    
    is_valid, reason = validate_ohlcv_structure(valid_df)
    assert is_valid is True
    assert reason == "VALID"

    # Test non-monotonic timestamp error
    reversed_df = valid_df.iloc[::-1].reset_index(drop=True)
    is_valid_mono, reason_mono = validate_ohlcv_structure(reversed_df)
    assert is_valid_mono is False
    assert reason_mono == "NON_MONOTONIC_TIMESTAMPS"

    # Test High < Low error
    invalid_price_df = valid_df.copy()
    invalid_price_df.loc[0, "High"] = 90.0
    is_valid_price, reason_price = validate_ohlcv_structure(invalid_price_df)
    assert is_valid_price is False
    assert reason_price == "HIGH_LESS_THAN_LOW"

def test_ohlcv_hash_generation():
    dates = pd.date_range("2025-01-01", periods=5, freq="D")
    df1 = pd.DataFrame({"Date": dates, "Close": [10, 20, 30, 40, 50]})
    df2 = pd.DataFrame({"Date": dates, "Close": [10, 20, 30, 40, 50]})
    df3 = pd.DataFrame({"Date": dates, "Close": [10, 20, 30, 40, 55]})

    hash1 = compute_ohlcv_hash(df1)
    hash2 = compute_ohlcv_hash(df2)
    hash3 = compute_ohlcv_hash(df3)

    assert len(hash1) > 0
    assert hash1 == hash2
    assert hash1 != hash3

def test_indicator_executor_jobs():
    dates = pd.date_range("2025-01-01", periods=50, freq="D")
    df = pd.DataFrame({
        "Open": np.random.randn(50).cumsum() + 100,
        "High": np.random.randn(50).cumsum() + 105,
        "Low": np.random.randn(50).cumsum() + 95,
        "Close": np.random.randn(50).cumsum() + 100,
        "Volume": np.random.randint(1000, 100000, 50)
    }, index=dates)

    executor = IndicatorExecutor(mode="sequential")
    jobs = [
        {"symbol": "TEST1", "timeframe": "1d", "dataframe": df.copy()},
        {"symbol": "TEST2", "timeframe": "1d", "dataframe": df.copy()}
    ]

    res = executor.execute(jobs)
    assert "TEST1" in res
    assert "TEST2" in res
    assert "EMA20" in res["TEST1"].columns
    assert "RSI" in res["TEST1"].columns

def test_pipeline_telemetry_budgets():
    tel = PipelineTelemetry("UnitTest")
    tel.record_stage("download", 1.2)
    tel.record_stage("indicators", 4.5)
    
    evals = tel.evaluate_budgets()
    assert "download" in evals
    assert evals["download"]["status"] in ("PASS", "WARNING", "FAIL")
    assert evals["TOTAL"]["status"] in ("PASS", "WARNING", "FAIL")

def test_telemetry_manager_log_session_timeline():
    from telemetry_manager import telemetry
    # Test 1-argument invocation (used across main.py and application_context.py)
    telemetry.log_session_timeline("Started Test Event Cycle")
    # Test 2-argument invocation
    telemetry.log_session_timeline("2026-07-24 07:45:00 IST", "Started Test Event Cycle")
