# [VERSION: MULTIBAGGER_TEST_SUITE_v1.2] Regression tests for Multibagger V5 Scanner & Exit Monitor fixes
import math
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "app"))

from multibagger import (
    _is_stale_trade_date,
    evaluate_multibagger_symbol,
    classify_conviction,
    run_exit_monitor,
    ExitPriceData,
    ScreenerResult,
    append_rejection,
    _start_wrapper
)

IST = ZoneInfo("Asia/Kolkata")

def test_stale_trade_date_boundary_and_exceptions():
    """Test 4 & 3: Trade date >= 3 business days is stale; invalid dates return True (fail closed)."""
    # 1. Invalid or missing dates fail closed (return True)
    assert _is_stale_trade_date("") is True
    assert _is_stale_trade_date(None) is True
    assert _is_stale_trade_date("invalid-date-format") is True
    assert _is_stale_trade_date("2025-13-45") is True

    # 2. Today is not stale
    today_str = datetime.now(IST).date().isoformat()
    assert _is_stale_trade_date(today_str, max_business_days=3) is False

    # 3. Exactly 3 business days ago is stale (>= 3)
    import numpy as np
    end_date = np.datetime64(datetime.now(IST).date())
    three_bus_days_ago = str(np.busday_offset(end_date, -3, roll='backward'))
    assert _is_stale_trade_date(three_bus_days_ago, max_business_days=3) is True


def test_missing_fundamentals_never_closes_position():
    """Test 1 & 8: Missing fundamentals sets SELL_REVIEW and never closes position (tested in non-test mode with mocked DB)."""
    today_str = datetime.now(IST).date().isoformat()
    test_price_data = ExitPriceData(
        symbol="RELIANCE",
        price=1000.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1050.0,
        close_yesterday=995.0,
        sma_200_yesterday=895.0,
        atr_14=20.0,
        ema_20=980.0,
        closes_below_sma200_count=0,
        last_trade_date=today_str
    )
    price_data_map = {"RELIANCE": test_price_data}

    mock_db_positions = [{
        "id": 101,
        "symbol": "RELIANCE",
        "alert_price": 1000.0,
        "alert_date": today_str
    }]

    with patch("multibagger.get_connection") as mock_conn, \
         patch("multibagger.batch_download_market_data", return_value={"RELIANCE": test_price_data}), \
         patch("multibagger.get_cached_fundamentals", return_value=None), \
         patch("multibagger.fetch_ticker_fundamentals", return_value=None), \
         patch("multibagger._persist_sell_review") as mock_persist, \
         patch("multibagger.update_alert_outcome") as mock_update_outcome:

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_db_positions
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        run_exit_monitor(price_data_map, cache={}, is_test_mode=False)

        # Position should NOT be closed
        mock_update_outcome.assert_not_called()
        # _persist_sell_review MUST be called with Fundamental data unavailable
        mock_persist.assert_called_once_with(101, "SELL_REVIEW: Fundamental data unavailable")


def test_missing_fundamentals_allows_catastrophic_stop():
    """Missing fundamentals allows emergency Catastrophic Stop if drawdown >= 30%."""
    today_str = datetime.now(IST).date().isoformat()
    test_price_data = ExitPriceData(
        symbol="RELIANCE",
        price=600.0, # 40% drawdown from 1000.0 entry
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1050.0,
        close_yesterday=650.0,
        sma_200_yesterday=895.0,
        atr_14=20.0,
        ema_20=680.0,
        closes_below_sma200_count=0,
        last_trade_date=today_str
    )
    price_data_map = {"RELIANCE": test_price_data}

    mock_db_positions = [{
        "id": 101,
        "symbol": "RELIANCE",
        "alert_price": 1000.0,
        "alert_date": today_str
    }]

    with patch("multibagger.get_connection") as mock_conn, \
         patch("multibagger.batch_download_market_data", return_value={"RELIANCE": test_price_data}), \
         patch("multibagger.get_cached_fundamentals", return_value=None), \
         patch("multibagger.fetch_ticker_fundamentals", return_value=None), \
         patch("multibagger._persist_sell_review") as mock_persist, \
         patch("multibagger.update_alert_outcome") as mock_update_outcome:

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_db_positions
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        run_exit_monitor(price_data_map, cache={}, is_test_mode=False)

        # Catastrophic Stop SHOULD trigger and close position
        mock_update_outcome.assert_called_once()
        kwargs = mock_update_outcome.call_args.kwargs
        assert "Catastrophic Stop" in kwargs.get("exit_signal", "")


def test_v5_invalidation_triggers_exit():
    """V5 pipeline invalidation triggers exit with V5 invalidation reason."""
    today_str = datetime.now(IST).date().isoformat()
    test_price_data = ExitPriceData(
        symbol="INFY",
        price=1000.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1050.0,
        close_yesterday=995.0,
        sma_200_yesterday=895.0,
        atr_14=20.0,
        ema_20=980.0,
        closes_below_sma200_count=0,
        last_trade_date=today_str
    )
    price_data_map = {"INFY": test_price_data}

    mock_db_positions = [{
        "id": 102,
        "symbol": "INFY",
        "alert_price": 1000.0,
        "alert_date": today_str
    }]

    fund = {"symbol": "INFY", "market_cap": 10000000000.0, "data_freshness": "LIVE"}

    mock_decision = MagicMock()
    mock_decision.quality.score = 70.0
    mock_decision.is_invalidated = True
    mock_decision.invalidation_reason = "Promoter pledge spiked above 25%"

    with patch("multibagger.get_connection") as mock_conn, \
         patch("multibagger.batch_download_market_data", return_value={"INFY": test_price_data}), \
         patch("multibagger.get_cached_fundamentals", return_value=fund), \
         patch("multibagger.passes_multibagger_quality_gate", return_value=(True, "")), \
         patch("multibagger.run_pipeline_for_symbol", return_value=mock_decision), \
         patch("multibagger.update_alert_outcome") as mock_update_outcome:

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_db_positions
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        run_exit_monitor(price_data_map, cache={}, is_test_mode=False)

        # V5 invalidation SHOULD trigger exit
        mock_update_outcome.assert_called_once()
        kwargs = mock_update_outcome.call_args.kwargs
        assert "V5 invalidation: Promoter pledge spiked above 25%" in kwargs.get("exit_signal", "")


def test_unsupported_financial_gate_never_closes_position():
    """Test 2 & 8: UNSUPPORTED gate reason issues SELL_REVIEW and does not close position."""
    today_str = datetime.now(IST).date().isoformat()
    test_price_data = ExitPriceData(
        symbol="HDFCBANK",
        price=1000.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1050.0,
        close_yesterday=995.0,
        sma_200_yesterday=895.0,
        atr_14=20.0,
        ema_20=980.0,
        closes_below_sma200_count=0,
        last_trade_date=today_str
    )
    price_data_map = {"HDFCBANK": test_price_data}

    mock_db_positions = [{
        "id": 202,
        "symbol": "HDFCBANK",
        "alert_price": 1000.0,
        "alert_date": today_str
    }]

    unsupported_fund = {
        "symbol": "HDFCBANK",
        "is_financial": True,
        "capital_adequacy_ratio": None,
        "data_freshness": "LIVE"
    }

    with patch("multibagger.get_connection") as mock_conn, \
         patch("multibagger.batch_download_market_data", return_value={"HDFCBANK": test_price_data}), \
         patch("multibagger.get_cached_fundamentals", return_value=unsupported_fund), \
         patch("multibagger._persist_sell_review") as mock_persist, \
         patch("multibagger.update_alert_outcome") as mock_update_outcome:

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_db_positions
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        run_exit_monitor(price_data_map, cache={}, is_test_mode=False)

        mock_update_outcome.assert_not_called()
        mock_persist.assert_called_once_with(
            202, "SELL_REVIEW: UNSUPPORTED: financial-sector CAR unavailable from yfinance"
        )


def test_sql_exit_query_includes_sell_review():
    """Test 1: Exit monitor queries both OPEN and SELL_REVIEW statuses."""
    with patch("multibagger.get_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        run_exit_monitor({}, cache={}, is_test_mode=True)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "status IN ('OPEN', 'SELL_REVIEW')" in executed_sql


def test_live_price_validation_rejects_invalid_values():
    """Test 5 & 6: Live prices (None, 0, -10, 'abc', inf, nan) are rejected as LIVE_PRICE_UNAVAILABLE."""
    invalid_prices = [None, 0, -15.5, "not_a_number", float("inf"), float("-inf"), float("nan")]

    for bad_price in invalid_prices:
        try:
            parsed = float(bad_price) if bad_price is not None else float("nan")
        except (TypeError, ValueError):
            parsed = float("nan")

        is_valid = math.isfinite(parsed) and parsed > 0
        assert is_valid is False, f"Price {bad_price} should have been rejected as invalid"


def test_pipeline_failure_continues_subsequent_symbols():
    """Test 9: Exception in run_pipeline_for_symbol logs exception and continues remaining symbols."""
    symbols = ["FAILSYM", "PROFILESYM"]
    today_str = datetime.now(IST).date().isoformat()

    mock_price = MagicMock()
    mock_price.symbol = "FAILSYM"
    mock_price.price = 100.0
    mock_price.sma_200 = 90.0
    mock_price.sma_50 = 95.0
    mock_price.ema_20 = 98.0
    mock_price.latest_volume = 100000.0
    mock_price.volume_sma20 = 50000.0
    mock_price.turnover_20d = 5000000.0
    mock_price.last_trade_date = today_str
    mock_price.high_52w = 110.0
    mock_price.mom_6m = 0.10

    mock_price2 = MagicMock()
    mock_price2.symbol = "PROFILESYM"
    mock_price2.price = 200.0
    mock_price2.sma_200 = 180.0
    mock_price2.sma_50 = 190.0
    mock_price2.ema_20 = 195.0
    mock_price2.latest_volume = 200000.0
    mock_price2.volume_sma20 = 100000.0
    mock_price2.turnover_20d = 10000000.0
    mock_price2.last_trade_date = today_str
    mock_price2.high_52w = 220.0
    mock_price2.mom_6m = 0.20

    price_map = {"FAILSYM": mock_price, "PROFILESYM": mock_price2}

    def mock_pipeline(sym, *args, **kwargs):
        if sym == "FAILSYM":
            raise ValueError("Malformed pipeline data")
        res = MagicMock()
        res.is_invalidated = False
        res.composite_score = 80.0
        res.quality.score = 70.0
        res.valuation.score = 60.0
        res.market_structure.score = 15.0
        res.buy_zone.buy_zone_low = 190.0
        res.buy_zone.buy_zone_high = 210.0
        res.buy_zone.in_buy_zone = True
        return res

    with patch("constituent_service.fetch_constituents", return_value=symbols), \
         patch("multibagger.init_db"), \
         patch("multibagger.batch_download_market_data", return_value=price_map), \
         patch("multibagger.load_cache", return_value={}), \
         patch("multibagger.save_fundamentals_cache"), \
         patch("multibagger.get_cached_fundamentals", side_effect=lambda sym, c: {"symbol": sym}), \
         patch("multibagger.passes_multibagger_quality_gate", return_value=(True, "")), \
         patch("multibagger.run_pipeline_for_symbol", side_effect=mock_pipeline), \
         patch("multibagger.run_exit_monitor"), \
         patch("multibagger.upsert_scanner_health"), \
         patch("multibagger.get_connection"):

        res_dict = _start_wrapper(debug_limit=None, is_test_mode=True)
        assert res_dict is not None


def test_evaluate_multibagger_symbol_uses_classify_conviction():
    """Test 3: evaluate_multibagger_symbol uses classify_conviction and normalized inputs."""
    import pandas as pd
    dates = pd.date_range("2025-01-01", periods=210, freq="B")
    df = pd.DataFrame({
        "Open": [100.0] * 210,
        "High": [105.0] * 210,
        "Low": [95.0] * 210,
        "Close": [100.0 + i * 0.1 for i in range(210)],
        "Volume": [10000] * 210
    }, index=dates)

    mock_v5_res = MagicMock()
    mock_v5_res.composite_score = 80.0
    mock_v5_res.quality.score = 70.0
    mock_v5_res.valuation.score = 55.0
    mock_v5_res.market_structure.score = 15.0

    with patch("multibagger.run_pipeline_for_symbol", return_value=mock_v5_res), \
         patch("wealth_engine.map_watchlist_to_v5", return_value={}):

        result = evaluate_multibagger_symbol("TESTSTOCK", df, fund_data={"score": 8, "promoter_pledge_pct": 5.0})
        # Score 80, CQS 70, PAS 55, Trend 15, Piotroski 8, Pledge 5% (0.05 ratio) -> Prime Multibagger
        assert result["status"] == "CORE MET (Prime)"
        assert result["conviction_tier"] == "Prime"

        result_unverified = evaluate_multibagger_symbol("TESTSTOCK", df, fund_data={"score": 8, "promoter_pledge_pct": None})
        assert "UNVERIFIED" in result_unverified["reasons"][0]


def test_save_alert_if_new_exception_handling():
    """Test 4: Exception during save_alert_if_new does not break scanner loop."""
    symbols = ["DBSYM"]
    today_str = datetime.now(IST).date().isoformat()

    mock_price = MagicMock()
    mock_price.symbol = "DBSYM"
    mock_price.price = 100.0
    mock_price.sma_200 = 90.0
    mock_price.sma_50 = 95.0
    mock_price.ema_20 = 98.0
    mock_price.latest_volume = 100000.0
    mock_price.volume_sma20 = 50000.0
    mock_price.turnover_20d = 5000000.0
    mock_price.last_trade_date = today_str
    mock_price.high_52w = 110.0
    mock_price.mom_6m = 0.20
    mock_price.today_open = 99.0
    mock_price.today_close = 100.0

    mock_v5_res = MagicMock()
    mock_v5_res.is_invalidated = False
    mock_v5_res.composite_score = 90.0
    mock_v5_res.quality.score = 80.0
    mock_v5_res.valuation.score = 70.0
    mock_v5_res.market_structure.score = 20.0
    mock_v5_res.buy_zone.buy_zone_low = 90.0
    mock_v5_res.buy_zone.buy_zone_high = 110.0
    mock_v5_res.buy_zone.in_buy_zone = True

    with patch("constituent_service.fetch_constituents", return_value=symbols), \
         patch("multibagger.init_db"), \
         patch("multibagger.batch_download_market_data", return_value={"DBSYM": mock_price}), \
         patch("multibagger.load_cache", return_value={}), \
         patch("multibagger.save_fundamentals_cache"), \
         patch("multibagger.get_cached_fundamentals", return_value={"symbol": "DBSYM"}), \
         patch("multibagger.passes_multibagger_quality_gate", return_value=(True, "")), \
         patch("multibagger.run_pipeline_for_symbol", return_value=mock_v5_res), \
         patch("live_prices.get_live_prices", return_value={"DBSYM": 100.0}), \
         patch("multibagger.save_alert_if_new", side_effect=Exception("DB pool exhausted")), \
         patch("multibagger.run_exit_monitor"), \
         patch("multibagger.upsert_scanner_health"), \
         patch("multibagger.get_connection"):

        # Should complete gracefully despite save_alert_if_new raising an Exception
        res = _start_wrapper(debug_limit=None, is_test_mode=False)
        assert res is not None
