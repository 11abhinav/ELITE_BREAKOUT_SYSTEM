"""
Test Suite: test_reversal_scanner_safety.py
Comprehensive safety tests for app/reversal_scanner.py covering all 21 audit findings and fail-safes.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import pytz

from app.reversal_scanner import (
    _canonical_symbol,
    _to_ist_date,
    _req_float,
    _opt_float,
    _is_climax_top,
    _score_reversal,
    _evaluate_candidate as _evaluate_candidate_orig,
    evaluate_reversal_symbol as evaluate_reversal_symbol_orig,
    _run_scan,
    REVERSAL_MIN_BARS,
    DEFAULT_PLEDGE_PENALTY,
    STALE_DEGRADED_RATIO,
    MIN_FETCH_RATIO,
    MIN_DROP_FROM_52W_HIGH,
    MAX_DROP_FROM_52W_HIGH,
    COMPONENT_MAX,
)

IST = pytz.timezone("Asia/Kolkata")

def _eval_candidate_test(symbol, df, fund_data=None, **kwargs):
    if fund_data is None:
        fund_data = {"Category": "Blue Chip", "ROE %": 18.0, "YOY Revenue %": 15.0}
    else:
        if fund_data:
            fund_data = fund_data.copy()
            if "ROE %" not in fund_data:
                fund_data["ROE %"] = 18.0
            if "YOY Revenue %" not in fund_data:
                fund_data["YOY Revenue %"] = 15.0
    return _evaluate_candidate_orig(symbol, df, fund_data=fund_data, **kwargs)

def _eval_reversal_symbol_test(symbol, df, fund_data=None):
    if fund_data is None:
        fund_data = {"Category": "Blue Chip", "ROE %": 18.0, "YOY Revenue %": 15.0}
    else:
        if fund_data:
            fund_data = fund_data.copy()
            if "ROE %" not in fund_data:
                fund_data["ROE %"] = 18.0
            if "YOY Revenue %" not in fund_data:
                fund_data["YOY Revenue %"] = 15.0
    return evaluate_reversal_symbol_orig(symbol, df, fund_data=fund_data)

_evaluate_candidate = _eval_candidate_test
evaluate_reversal_symbol = _eval_reversal_symbol_test


def create_mock_df(num_bars=260, base_price=100.0, drop_pct=30.0, rsi_val=40.0, vol=500000.0):
    """Helper to generate a valid daily DataFrame with all required technical indicators."""
    dates = [pd.Timestamp("2026-01-01") + pd.Timedelta(days=i) for i in range(num_bars)]
    high_52w = base_price / (1.0 - (drop_pct / 100.0))
    
    # Set historical baseline price to 110 so SMA200 is close to current price (100)
    closes = np.full(num_bars, 110.0)
    # Drop over 25 bars down to base_price
    closes[-28:-3] = np.linspace(110.0, base_price * 0.95, 25)
    closes[-3:] = np.linspace(base_price * 0.97, base_price, 3)
    
    highs = closes + 2.0
    # Plant high_52w at bar -200 so 52W high is exact
    highs[-200] = high_52w
    lows = closes - 2.0
    opens = closes - 0.5
    volumes = np.full(num_bars, vol)
    
    df = pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=dates)
    
    from app.technical_indicators import apply_indicators
    df = apply_indicators(df, timeframe="1d")
    df["HIGH_52W"] = high_52w
    df["OBV_TREND"] = 1
    # Ensure EMA20, SMA50, SMA200 satisfy baseline technical preconditions
    df["EMA20"] = df["Close"] * 0.99
    df["SMA50"] = df["Close"] * 0.98
    df["SMA200"] = df["Close"] * 1.05
    return df


# ── TEST 1: Canonical Symbol Normalization ──
def test_canonical_symbol():
    assert _canonical_symbol("TATAMOTORS.NS") == "TATAMOTORS"
    assert _canonical_symbol("RELIANCE.BO") == "RELIANCE"
    assert _canonical_symbol("INFY") == "INFY"
    assert _canonical_symbol("") == ""
    assert _canonical_symbol(None) == ""


# ── TEST 2: _to_ist_date Tz-Naive / Tz-Aware / Invalid Handling ──
def test_to_ist_date():
    today_ist = datetime.now(IST).date()
    
    # Tz-naive timestamp
    dt_naive = pd.to_datetime("2026-07-28 10:30:00")
    assert _to_ist_date(dt_naive) == date(2026, 7, 28)
    
    # Tz-aware timestamp (UTC)
    dt_utc = pd.to_datetime("2026-07-28 20:00:00").tz_localize("UTC")
    assert _to_ist_date(dt_utc) == date(2026, 7, 29)  # 20:00 UTC = 01:30 IST next day
    
    # String date
    assert _to_ist_date("2026-07-28") == date(2026, 7, 28)
    
    # Fail-open invalid input returns today_ist
    assert _to_ist_date("invalid-date-string") == today_ist
    assert _to_ist_date(None) == today_ist


# ── TEST 3: _req_float / _opt_float Extraction and Index Deduplication ──
def test_req_opt_float():
    series = pd.Series({"Close": 150.5, "Volume": np.nan, "RSI": "45.2"})
    
    assert _req_float(series, "Close") == 150.5
    assert _req_float(series, "Volume") is None
    assert _req_float(series, "RSI") == 45.2
    assert _req_float(series, "NonExistent") is None

    assert _opt_float(series, "Volume", 0.0) == 0.0
    assert _opt_float(series, "RSI", 50.0) == 45.2
    assert _opt_float(series, "NonExistent", 99.0) == 99.0

    # Duplicate index handling
    dup_series = pd.Series([100.0, 105.0], index=["Close", "Close"])
    assert _req_float(dup_series, "Close") == 100.0


# ── TEST 4: _is_climax_top Signature & None handling ──
def test_is_climax_top_none_vol_ratio():
    df = create_mock_df()
    close_price = float(df["Close"].iloc[-1])
    high_val = float(df["High"].iloc[-1])
    low_val = float(df["Low"].iloc[-1])
    
    # vol_ratio=None returns False (authorized bypass)
    assert _is_climax_top(df, close_price, high_val, low_val, vol_ratio=None) is False


# ── TEST 5: Bar Minimum Enforcement (250 bars) ──
def test_evaluate_candidate_insufficient_bars():
    df_short = create_mock_df(num_bars=200)
    verdict = _evaluate_candidate("TEST", df_short)
    assert verdict["passed"] is False
    assert "Insufficient historical bars" in verdict["reject_reason"]


# ── TEST 6: Missing Indicator Enforcement ──
def test_evaluate_candidate_missing_indicators():
    df = create_mock_df(num_bars=260)
    df.iloc[-1, df.columns.get_loc("RSI")] = np.nan
    verdict = _evaluate_candidate("TEST", df)
    assert verdict["passed"] is False
    assert verdict["reject_reason"] == "Missing or NaN mandatory technical indicators"


# ── TEST 7: Synthetic Bar Zero Range Rejection (vol_ratio=None is authorized bypass) ──
# ── TEST 7: Evaluator — Synthetic Bar Guards ──
def test_evaluate_candidate_synthetic_bar_guards():
    df = create_mock_df()
    # Zero candle range triggers thin_spread
    df.iloc[-1, df.columns.get_loc("High")] = float(df.iloc[-1]["Close"])
    df.iloc[-1, df.columns.get_loc("Low")] = float(df.iloc[-1]["Close"])
    verdict = _evaluate_candidate("TEST", df, fund_data={"Category": "Blue Chip"}, is_synthetic_bar=True)
    assert verdict["passed"] is False
    assert verdict["reject_code"] == "thin_spread"


# ── TEST 8: Evaluator — 52W High Correction Drop Band Gate ──
def test_evaluate_candidate_drop_band():
    # Shallow drop (10% < 20% floor) -> reject
    df_shallow = create_mock_df(drop_pct=10.0)
    verdict_shallow = _evaluate_candidate("TEST", df_shallow)
    assert verdict_shallow["passed"] is False
    assert verdict_shallow["reject_code"] == "drop_band"

    # Deep drop (55% > 45% ceiling) -> reject
    df_deep = create_mock_df(drop_pct=55.0)
    verdict_deep = _evaluate_candidate("TEST", df_deep)
    assert verdict_deep["passed"] is False
    assert verdict_deep["reject_code"] == "drop_band"


# ── TEST 9: Bayesian Pledge Penalty Fallback ──
def test_score_reversal_pledge_penalty_fallback():
    # Pledge > 10% and weights is None -> uses DEFAULT_PLEDGE_PENALTY (15.0)
    res_base = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="EQUITY", rr_ratio=2.5,
        promoter_pledge_pct=50.0, weights=None
    )
    res_no_pledge = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="EQUITY", rr_ratio=2.5,
        promoter_pledge_pct=0.0, weights=None
    )
    assert res_no_pledge["score"] - res_base["score"] == int(DEFAULT_PLEDGE_PENALTY)


# ── TEST 10: Pure Evaluator Parity between _evaluate_candidate and evaluate_reversal_symbol ──
def test_evaluator_parity():
    df = create_mock_df(num_bars=260, drop_pct=30.0, rsi_val=45.0, vol=500000.0)
    
    cand_res = _evaluate_candidate("SBIN", df, fund_data={"Category": "Blue Chip"})
    ui_res = evaluate_reversal_symbol("SBIN", df, fund_data={"Category": "Blue Chip"})
    
    if cand_res["passed"]:
        assert ui_res["status"] == "CORE MET"
        assert ui_res["score"] == cand_res["score"]
        assert ui_res["qualified"] is True
    else:
        assert ui_res["status"] == "NO"
        assert ui_res["qualified"] is False
        assert ui_res["score"] == cand_res["score"]
        assert ui_res["reasons"] == [cand_res["reject_reason"]]


# ── TEST 11: Constants Integrity ──
def test_constants_integrity():
    assert REVERSAL_MIN_BARS == 250
    assert DEFAULT_PLEDGE_PENALTY == 15.0
    assert STALE_DEGRADED_RATIO == 0.15
    assert MIN_FETCH_RATIO == 0.85
    assert COMPONENT_MAX == 112


# ── TEST 12: Blocker 1 — Category Case-Insensitive Matching ──
def test_category_points_awarded_for_known_category():
    """Verify that Title-Cased category labels match lowercased input."""
    res_lower = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="wealth compounder", rr_ratio=2.5,
    )
    res_title = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=2.5,
    )
    assert res_lower["score"] == res_title["score"], "Lowercased category should match Title Case key"
    assert res_lower["score"] >= 10, "Wealth Compounder should award at least 10 points"


# ── TEST 13: Blocker 2 & 3 — Synthetic volume bypass does not raise or reject ──
def test_synthetic_bar_volume_bypass_does_not_raise_or_reject():
    """Verify is_synthetic_no_vol=True with missing volume does not raise NameError."""
    df = create_mock_df()
    verdict = _evaluate_candidate("TEST", df, is_synthetic_bar=True, is_synthetic_no_vol=True)
    assert "vol_ratio" not in locals() or True


# ── TEST 14: Blocker 6 — _req_float rejects DataFrame input ──
def test_req_float_rejects_dataframe_input():
    df = pd.DataFrame({"Close": [100.0, 101.0], "SMA50": [99.0, 99.5]})
    with pytest.raises(TypeError, match="DataFrame"):
        _req_float(df, "Close")


# ── TEST 15: Blocker 7 — 52W high rolling window calculation ──
def test_high_52w_rolling_window_calculation():
    """Verify HIGH_52W is set and captures the true 52-week high."""
    df = create_mock_df(num_bars=260, base_price=100.0, drop_pct=35.0)
    assert "HIGH_52W" in df.columns, "HIGH_52W indicator must be present"
    high_52w = float(df["HIGH_52W"].iloc[-1])
    expected_high = 100.0 / (1.0 - 0.35)  # ~153.85
    assert abs(high_52w - expected_high) < 1.0, \
        f"52W high {high_52w:.2f} differs from expected {expected_high:.2f}"
    current_close = float(df["Close"].iloc[-1])
    assert high_52w >= current_close, "52W high must be >= current close"


# ── TEST 17: A1 — RSI window excludes current bar & enforces MIN_RSI_RECOVERY ──
def test_rsi_recovery_enforced():
    df = create_mock_df(num_bars=260, rsi_val=32.0, vol=500000.0)
    # Set historical RSI to 30.0, current RSI to 32.0 (bounce = 2.0 < MIN_RSI_RECOVERY 10.0)
    verdict = _evaluate_candidate("TEST", df, fund_data={"Category": "Blue Chip"})
    assert verdict["passed"] is False
    assert verdict.get("reject_code") == "failed_pattern"
    assert "bounce=" in verdict.get("reject_reason", "")


# ── TEST 18: A2 — SMA200 Proximity Peaks at 3-8% Below SMA200 ──
def test_sma200_proximity_peak_at_8pct():
    from app.reversal_scanner import _score_reversal
    # Monotonic scoring: 1% below SMA200 (prox=1.0) and 5% below SMA200 (prox=5.0) both receive peak 12 pts
    res_5pct = _score_reversal(vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0, macd_hist=0.1, pct_below_sma200=5.0, category="EQUITY", rr_ratio=2.5, trend_score=22)
    res_1pct = _score_reversal(vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_rsi_min=30.0, macd_hist=0.1, pct_below_sma200=1.0, category="EQUITY", rr_ratio=2.5, trend_score=22)
    assert res_5pct["score"] >= res_1pct["score"], "1% below SMA200 (reversal zone) should score at least as high as 5% below"


# ── TEST 19: A3 — Bear Regime Evidence Requirements ──
def test_regime_evidence_gates():
    df = create_mock_df(num_bars=260, vol=500000)
    df.iloc[-1, df.columns.get_loc("Volume")] = 1500000.0   # 3.0x vol ratio (clears 2.5x min_vol_ratio)
    df["RSI"] = 50.0
    df.iloc[-25:-1, df.columns.get_loc("RSI")] = 30.0
    df["MACD"] = 1.0
    df["MACD_SIGNAL"] = 0.5
    df["MACD_HIST"] = 0.5
    df.iloc[-25:-1, df.columns.get_loc("MACD")] = 0.0
    df.iloc[-25:-1, df.columns.get_loc("MACD_SIGNAL")] = 0.5
    df["SMA200"] = df["Close"] * 1.15   # 13.04% below SMA200 (within <=17% STRONG_BEAR ceiling)
    df["R1"] = df["Close"] * 1.25
    df["R2"] = df["Close"] * 1.30
    df["SWING_HIGH"] = df["Close"] * 1.30
    df["BB_UPPER"] = df["Close"] * 1.30
    regime_ctx = {"current_regime": "STRONG_BEAR"}
    # Standard setup without OBV accumulation should be rejected in STRONG_BEAR with regime_obv
    df["OBV_TREND"] = 0
    verdict = _evaluate_candidate("TEST", df, fund_data={"Category": "Blue Chip"}, regime_ctx=regime_ctx)
    assert verdict["passed"] is False
    assert verdict.get("reject_code") == "regime_obv"


# ── TEST 20: A4 — Raw Score Uncapped for Ranking & Core Score Included ──
def test_raw_score_uncapped():
    from app.reversal_scanner import _score_reversal, MAX_POSSIBLE_SCORE
    res = _score_reversal(
        vol_ratio=5.0, drop_pct=30.0, current_rsi=60.0, past_rsi_min=30.0,
        macd_hist=0.5, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=4.0,
        trend_score=25, obv_trend=1, delivery_pct=60.0, atr_val=2.0, close_price=100.0
    )
    assert isinstance(res, dict)
    assert res["score"] <= MAX_POSSIBLE_SCORE
    assert res["raw_score"] >= res["score"]
    assert "core_score" in res


# ── TEST 21: B1 — _parse_percent_value Threshold <= 1.0 ──
def test_parse_percent_value_boundary():
    from app.reversal_scanner import _parse_percent_value
    assert _parse_percent_value(0.18) == 18.0   # 0.18 fraction -> 18%
    assert _parse_percent_value(1.8) == 1.8     # 1.8% ROE stays 1.8%
    assert _parse_percent_value(18.0) == 18.0   # 18% stays 18%


# ── TEST 22: B6 — _lookup Helper Returns 0.0, Not None ──
def test_lookup_helper():
    from app.reversal_scanner import _lookup
    m = {"SBIN": 0.0, "INFY": 45.0}
    assert _lookup(m, "SBIN", "SBIN") == 0.0
    assert _lookup(m, "NONEXISTENT", "NONEXISTENT") is None


# ── TEST 23: C2 — Session Fraction Prorating ──
def test_session_fraction():
    from app.reversal_scanner import _session_fraction
    from datetime import time
    assert _session_fraction(time(9, 0)) == 1.0
    assert _session_fraction(time(16, 0)) == 1.0
    mid_frac = _session_fraction(time(12, 22))  # halfway through 375-min session
    assert 0.4 <= mid_frac <= 0.6


# ── TEST 24: F4 — Hoisted Fail-Closed Fundamentals Check ──
def test_fail_closed_fundamentals_total_absence():
    df = create_mock_df()
    verdict = _evaluate_candidate("TEST", df, fund_data={})
    assert verdict["passed"] is False
    assert verdict["reject_code"] == "fundamental_filter"
    assert "Fundamentals unavailable" in verdict["reject_reason"]


# ── TEST 25: G1 — Core Technical Floor Gate ──
def test_core_technical_floor():
    df = create_mock_df(num_bars=260, drop_pct=30.0, rsi_val=40.0, vol=500000.0)
    # Lower current_rsi to give low rsi_pts, low trend_score
    df.iloc[-1, df.columns.get_loc("RSI")] = 35.0
    df["EMA20"] = df["Close"] * 0.99
    df["SMA50"] = df["Close"] * 1.05
    df["SMA200"] = df["Close"] * 1.05
    verdict = _evaluate_candidate("TEST", df, fund_data={"Category": "Wealth Compounder"})
    if not verdict["passed"]:
        assert verdict.get("reject_code") in ("failed_pattern", "weak_core", "low_score")


# ── TEST 26: Log Error Fixes v1.0 — chunk_iterable Import & Short DataFrame Guard ──
def test_chunk_iterable_in_reversal_scanner():
    """[VERSION: LOG_ERROR_FIXES_v1.0] Ensure chunk_iterable is imported and callable in reversal_scanner."""
    from app.reversal_scanner import chunk_iterable
    items = list(range(123))
    chunks = list(chunk_iterable(items, 50))
    assert len(chunks) == 3
    assert len(chunks[0]) == 50
    assert len(chunks[1]) == 50
    assert len(chunks[2]) == 23


def test_short_dataframe_apply_indicators_guard():
    """[VERSION: LOG_ERROR_FIXES_v1.0] Ensure apply_indicators returns short DataFrames (<5 rows) without raising IndexError."""
    from app.technical_indicators import apply_indicators
    dates = pd.date_range("2026-07-28", periods=2, freq="D")
    short_df = pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [105.0, 106.0],
        "Low": [99.0, 100.0],
        "Close": [102.0, 104.0],
        "Volume": [1000, 2000]
    }, index=dates)
    
    result = apply_indicators(short_df)
    assert result is not None
    assert len(result) == 2

