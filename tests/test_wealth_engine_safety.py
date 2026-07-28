import pytest
import pandas as pd
import numpy as np
from app.wealth_engine import (
    is_financial_sector,
    calculate_hold_score,
    evaluate_open_positions,
    watchlist_percent_to_ratio,
    ratio_to_percent
)

def test_financial_sector_false_positive_prevention():
    """Verify sector/industry matching does not misclassify based solely on symbol substrings like 'BANKA'."""
    non_fin = {"Sector": "Capital Goods", "Industry": "Industrial Machinery", "Stock": "BANKA"}
    assert is_financial_sector(non_fin) is False

    fin_bank = {"Sector": "Banks", "Industry": "Private Sector Bank", "Stock": "HDFCBANK"}
    assert is_financial_sector(fin_bank) is True

    fin_nbfc = {"Sector": "Financial Services", "Industry": "Non Banking Financial Company (NBFC)", "Stock": "BAJFINANCE"}
    assert is_financial_sector(fin_nbfc) is True

    fin_override = {"Sector": "", "Industry": "", "Stock": "SBIN"}
    assert is_financial_sector(fin_override) is True

def test_drawdown_exact_20_percent():
    """Verify drawdown >= 20.0% triggers SELL, while 19.99% does not."""
    port_dict = {}
    
    # 20.0% loss
    row_20 = pd.DataFrame([{
        "Stock": "TEST1", "entry_price": 100.0, "cmp": 80.0, "prev_close": 82.0,
        "data_quality": "LIVE", "used_fallback_data": False, "sma_200": 90.0, "rs_6m": 10.0
    }])
    res_20 = evaluate_open_positions(row_20, port_dict)
    assert res_20.iloc[0]["Exit_Code"] == "SELL"
    assert "Hard Drawdown Stop" in res_20.iloc[0]["Exit_Reason"]

    # 19.99% loss (80.01)
    row_19 = pd.DataFrame([{
        "Stock": "TEST2", "entry_price": 100.0, "cmp": 80.01, "prev_close": 82.0,
        "data_quality": "LIVE", "used_fallback_data": False, "sma_200": 90.0, "rs_6m": 10.0,
        "ema_20": 85.0, "sma_50": 88.0, "FM_Score": 70, "RS_Rating": 85, "AI_Confidence": 8
    }])
    res_19 = evaluate_open_positions(row_19, port_dict)
    assert res_19.iloc[0]["Exit_Code"] != "SELL"

def test_missing_prev_close_demotes_auto_sell():
    """Verify missing prev_close suppresses automated SELL signal."""
    row_no_prev = pd.DataFrame([{
        "Stock": "TEST_NO_PREV", "entry_price": 100.0, "cmp": 75.0, "prev_close": None,
        "data_quality": "LIVE", "used_fallback_data": False, "sma_200": 90.0, "rs_6m": 10.0
    }])
    res = evaluate_open_positions(row_no_prev, {})
    assert res.iloc[0]["Exit_Code"] == ""

def test_stale_intraday_suppresses_auto_sell():
    """Verify data_quality == 'STALE_INTRADAY' suppresses auto SELL."""
    row_stale = pd.DataFrame([{
        "Stock": "TEST_STALE", "entry_price": 100.0, "cmp": 70.0, "prev_close": 75.0,
        "data_quality": "STALE_INTRADAY", "used_fallback_data": False, "sma_200": 90.0, "rs_6m": 10.0
    }])
    res = evaluate_open_positions(row_stale, {})
    assert res.iloc[0]["Exit_Code"] == ""

def test_catastrophic_trend_priority():
    """Verify CMP < 0.75 * SMA200 triggers SELL (Catastrophic Trend Collapse) before hold score review."""
    row_collapse = pd.DataFrame([{
        "Stock": "TEST_COLLAPSE", "entry_price": 60.0, "cmp": 60.0, "prev_close": 62.0,
        "data_quality": "LIVE", "used_fallback_data": False, "sma_200": 100.0, "rs_6m": -10.0,
        "ema_20": 70.0, "sma_50": 80.0, "FM_Score": 40, "RS_Rating": 30, "AI_Confidence": 2
    }])
    # cmp (60) < 0.75 * sma200 (75) -> Catastrophic Trend Collapse SELL
    res = evaluate_open_positions(row_collapse, {})
    assert res.iloc[0]["Exit_Code"] == "SELL"
    assert "Catastrophic Trend Collapse" in res.iloc[0]["Exit_Reason"]

def test_explicit_unit_converters():
    """Verify context-specific unit converters."""
    assert watchlist_percent_to_ratio(20.0) == 0.20
    assert ratio_to_percent(0.20) == 20.0

def test_yoy_parser_ratio_and_percent_keys():
    """Verify key-level YoY unit parsing handles both V5 ratio dicts and Screener percent dicts deterministically."""
    from app.wealth_engine import _parse_yoy_percent
    
    # 1. Ratio key format (from map_watchlist_to_v5 dict) -> 0.20 ratio scale converted to 20.0 percent scale
    fd_ratio = {"yoy_revenue": 0.20}
    assert _parse_yoy_percent(fd_ratio, "yoy_revenue", "YOY Revenue %") == 20.0

    # 2. Percent key format (from raw Screener export) -> 20.0 percent scale passed as-is
    fd_pct = {"YOY Revenue %": 20.0}
    assert _parse_yoy_percent(fd_pct, "yoy_revenue", "YOY Revenue %") == 20.0

    # 3. Ratio key precedence when both exist
    fd_both = {"yoy_revenue": 0.25, "YOY Revenue %": 25.0}
    assert _parse_yoy_percent(fd_both, "yoy_revenue", "YOY Revenue %") == 25.0

def test_nifty_macro_unavailable_sets_proxy_flag():
    """Verify calculate_wealth_technicals returns rs_is_absolute_proxy=True when nifty_6m_ret is None."""
    from app.wealth_engine import calculate_wealth_technicals
    dates = pd.date_range("2024-01-01", periods=200, freq="D")
    df_hist = pd.DataFrame({
        "Close": np.linspace(100, 150, 200),
        "High": np.linspace(101, 152, 200),
        "Low": np.linspace(99, 148, 200),
        "Volume": [10000] * 200
    }, index=dates)
    
    res = calculate_wealth_technicals("TEST_MACRO", nifty_6m_ret=None, historical_cache={"TEST_MACRO": df_hist})
    assert res["rs_is_absolute_proxy"] is True
    assert res["rs_6m"] is not None
    assert isinstance(res["rs_6m"], float)

def test_none_bucket_emits_failed_quality_gates():
    """Verify Portfolio_Bucket = None (coerced to 'None') correctly emits 'Failed Bucket Quality Gates'."""
    from app.wealth_engine import generate_entry_signal
    df = pd.DataFrame([{
        "Stock": "TEST_NONE", "Portfolio_Bucket": None, "cmp": 100.0, "sma_200": 90.0,
        "FM_Score": 60, "Consistency_Score": 15, "Valuation_Score": 10,
        "momentum_confidence": "HIGH", "momentum_score": 30, "used_fallback_data": False,
        "candidate_complete_for_buy": True
    }])
    res = generate_entry_signal(df, buy_gate_active=False, suppression_reason="", open_symbols=[])
    assert res.iloc[0]["Signal_Code"] == "WAIT"
    assert res.iloc[0]["Signal_Reason"] == "Failed Bucket Quality Gates"

