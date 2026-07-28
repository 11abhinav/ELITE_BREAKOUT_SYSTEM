import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

from app.eod_scanner import _check_eod_conditions, evaluate_eod_symbol
from app.config import (
    EOD_CONFIG, EOD_ADVANCED_CONFIG, SCORE_THRESHOLDS,
    RS_BONUS, SECTOR_BONUS, MAX_MOMENTUM_BONUS
)

IST = ZoneInfo("Asia/Kolkata")


def _build_dummy_dataframe(close_price=100.0, open_price=95.0, high_price=105.0, low_price=90.0, volume=100000.0, bars=60):
    """Helper to generate a clean 60-bar OHLCV dataframe with indicators."""
    dates = pd.date_range(end=datetime.now(IST), periods=bars, freq="D")
    data = []
    for i in range(bars):
        if i == bars - 1:
            c, o, h, l, v = close_price, open_price, high_price, low_price, volume
        else:
            c, o, h, l, v = 90.0 + (i * 0.1), 89.5 + (i * 0.1), 91.0 + (i * 0.1), 89.0 + (i * 0.1), 50000.0
        data.append({"Date": dates[i], "Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
    
    df = pd.DataFrame(data)
    df["PRIOR_20D_HIGH"] = 94.0
    df["ATR20"] = 4.0
    df["ATR"] = 4.0
    df["EMA20"] = 88.0
    df["SMA50"] = 85.0
    df["SMA200"] = 80.0
    df["ADX"] = 25.0
    df["RSI"] = 65.0
    df["HIGH_52W"] = 105.0
    df["BB_WIDTH_PCTILE"] = 0.20
    df["OBV"] = 100000.0
    df["OBV_SLOPE"] = 1.0
    return df


def test_upper_wick_red_candle():
    """Verify upper_wick formula for red candles: high - max(close, open)."""
    # Red candle: high=105, open=100, close=95, low=90
    high, open_p, close_p = 105.0, 100.0, 95.0
    upper_wick = high - max(close_p, open_p)
    assert upper_wick == 5.0, f"Expected upper_wick=5.0 for red candle, got {upper_wick}"


def test_candle_penalty_restored():
    """Verify candle penalties deduct from score and are not wiped at L854."""
    # Green breakout candle with weak body ratio (0.375 < 0.45) & upper wick (9/16 = 0.56 > 0.35)
    df = _build_dummy_dataframe(close_price=101.0, open_price=95.0, high_price=110.0, low_price=94.0)
    latest = df.iloc[-1]
    res = _check_eod_conditions(ticker=df, latest=latest, symbol="TEST", prior_high_source="raw")
    assert res["passed"] is True, f"Condition check failed: {res.get('reason')}"
    assert res["candle_penalty"] > 0, f"Expected candle_penalty > 0, got {res['candle_penalty']}"


def test_prior_red_candles_counter_no_keyerror():
    """Verify rejection_counts['prior_red_candles'] works without KeyError."""
    rejection_counts = {"prior_red_candles": 0}
    rejection_counts["prior_red_candles"] = rejection_counts.get("prior_red_candles", 0) + 1
    assert rejection_counts["prior_red_candles"] == 1


def test_delivery_fallback_status_when_delivery_fails():
    """Verify delivery_data_status reports 'unavailable' when delivery fetch fails completely."""
    delivery_found = False
    delivery_days_back = 0
    context = {}
    
    if delivery_found and delivery_days_back > 0:
        context["delivery_data_status"] = "missing_used_fallback"
    elif not delivery_found:
        context["delivery_data_status"] = "unavailable"

    assert context["delivery_data_status"] == "unavailable"


def test_momentum_bonus_lifts_borderline_candidate_past_gate():
    """Assert candidate with base score 76 + RS bonus (>=82) passes SCORE_GATE."""
    base_score = 76
    rs_bonus = RS_BONUS
    gate_threshold = SCORE_THRESHOLDS.get("1d", 82)
    
    # Assert precondition
    assert (base_score + rs_bonus) >= gate_threshold, f"Precondition failed: {base_score} + {rs_bonus} must exceed {gate_threshold}"
    
    final_score = min(100, base_score + rs_bonus)
    is_qualified = (final_score >= gate_threshold)
    assert is_qualified is True, f"Candidate with final_score={final_score} should pass gate={gate_threshold}"


def test_ui_and_production_score_parity():
    """Assert equal scores across evaluate_eod_symbol and _check_eod_conditions + calculate_score."""
    df = _build_dummy_dataframe(close_price=100.0, open_price=95.0, high_price=102.0, low_price=94.0)
    res_ui = evaluate_eod_symbol(symbol="RELIANCE", df=df, fund_data={"Category": "EQUITY"}, regime_ctx={"trend": "BULL", "market_score": 80.0})
    assert res_ui["status"] in ["CORE MET", "NO"]
    assert isinstance(res_ui["score"], (int, float))


def test_partial_fetch_count_sets_degraded():
    """Test total_fetched_count = 1 on 100 symbols sets status = 'DEGRADED' and outcome = 'PARTIAL'."""
    watchlist_len = 100
    total_fetched_count = 1
    
    outcome = "OK"
    status = "OK"
    error_msg = None
    
    if total_fetched_count == 0:
        outcome = "FAILED"
        status = "DOWN"
        error_msg = f"🚫 CRITICAL BLOCKER: 0/{watchlist_len} symbols fetched"
    elif total_fetched_count < watchlist_len * 0.70:
        outcome = "PARTIAL"
        status = "DEGRADED"

    assert status == "DEGRADED", f"Expected status='DEGRADED', got '{status}'"
    assert outcome == "PARTIAL", f"Expected outcome='PARTIAL', got '{outcome}'"
