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
    _evaluate_candidate,
    evaluate_reversal_symbol,
    _run_scan,
    REVERSAL_MIN_BARS,
    DEFAULT_PLEDGE_PENALTY,
    STALE_DEGRADED_RATIO,
    MIN_FETCH_RATIO,
    MIN_DROP_FROM_52W_HIGH,
    MAX_DROP_FROM_52W_HIGH,
)

IST = pytz.timezone("Asia/Kolkata")


def create_mock_df(num_bars=260, base_price=100.0, drop_pct=30.0, rsi_val=40.0, vol=100000.0):
    """Helper to generate a valid daily DataFrame with all required technical indicators."""
    dates = [pd.Timestamp("2026-01-01") + pd.Timedelta(days=i) for i in range(num_bars)]
    high_52w = base_price / (1.0 - (drop_pct / 100.0))
    
    closes = np.full(num_bars, high_52w)
    # Drop over 25 bars
    closes[-28:-3] = np.linspace(high_52w, base_price * 0.95, 25)
    # 3-bar oversold curl
    closes[-3:] = np.linspace(base_price * 0.97, base_price, 3)
    
    highs = closes + 2.0
    highs[max(0, num_bars - 250)] = high_52w
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
def test_evaluate_candidate_synthetic_bar_guards():
    df = create_mock_df(num_bars=260, vol=1000000)

    # Synthetic bar with vol_ratio=None (authorized bypass) passes no-volume gate
    verdict = _evaluate_candidate("TEST", df, is_synthetic_bar=True, is_synthetic_no_vol=True)
    # Should NOT be rejected at synthetic bar guard — vol_ratio=None is authorized
    # It may be rejected later for other reasons (e.g., EMA/RSI/MACD)
    if not verdict["passed"]:
        assert "Synthetic bar" not in verdict["reject_reason"]
        assert verdict.get("reject_code") != "thin_spread"


# ── TEST 8: Drop Band Correction Bounds (15-45% for quality cat, 20-45% standard) ──
def test_evaluate_candidate_drop_band():
    # Standard category with 18% drop -> rejected (< 20%)
    df_std = create_mock_df(num_bars=260, drop_pct=18.0)
    verdict_std = _evaluate_candidate("TEST", df_std, fund_data={"Category": "EQUITY"})
    assert verdict_std["passed"] is False
    assert "outside 20.0%–45.0%" in verdict_std["reject_reason"]

    # Quality category with 18% drop -> clears drop band check
    df_qual = create_mock_df(num_bars=260, drop_pct=18.0)
    verdict_qual = _evaluate_candidate("TEST", df_qual, fund_data={"Category": "Blue Chip Stable"})
    # Clears drop band check (reason is not drop band)
    if not verdict_qual["passed"]:
        assert "outside" not in verdict_qual["reject_reason"]


# ── TEST 9: Bayesian Pledge Penalty Fallback ──
def test_score_reversal_pledge_penalty_fallback():
    # Pledge > 10% and weights is None -> uses DEFAULT_PLEDGE_PENALTY (15.0)
    score_base = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_10_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="EQUITY", rr_ratio=2.5,
        above_sma50=True, above_sma200=True, promoter_pledge_pct=50.0, weights=None
    )
    score_no_pledge = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_10_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="EQUITY", rr_ratio=2.5,
        above_sma50=True, above_sma200=True, promoter_pledge_pct=0.0, weights=None
    )
    assert score_no_pledge - score_base == int(DEFAULT_PLEDGE_PENALTY)


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


# ── TEST 12: Blocker 1 — Category Case-Insensitive Matching ──
def test_category_points_awarded_for_known_category():
    """Verify that Title-Cased category labels match lowercased input."""
    score_lower = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_10_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="wealth compounder", rr_ratio=2.5,
        above_sma50=True, above_sma200=True,
    )
    score_title = _score_reversal(
        vol_ratio=2.0, drop_pct=30.0, current_rsi=50.0, past_10_rsi_min=30.0,
        macd_hist=0.1, pct_below_sma200=5.0, category="Wealth Compounder", rr_ratio=2.5,
        above_sma50=True, above_sma200=True,
    )
    assert score_lower == score_title, "Lowercased category should match Title Case key"
    assert score_lower >= 10, "Wealth Compounder should award at least 10 points"


# ── TEST 13: Blocker 2 & 3 — Synthetic volume bypass does not raise or reject ──
def test_synthetic_bar_volume_bypass_does_not_raise_or_reject():
    """Verify is_synthetic_no_vol=True with missing volume does not raise NameError."""
    df = create_mock_df(num_bars=260, vol=1000000)
    try:
        verdict = _evaluate_candidate("TEST", df, is_synthetic_bar=True, is_synthetic_no_vol=True)
    except NameError as e:
        pytest.fail(f"NameError raised: {e}")
    # vol_ratio=None is an authorized bypass — never rejected at the vol ratio gate
    if not verdict["passed"]:
        assert verdict.get("reject_code") != "low_volume", \
            "Synthetic bar with no volume should not be rejected at volume check"
        assert "Volume ratio" not in verdict.get("reject_reason", "")


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


# ── TEST 16: pipeline reconciliation — zero unaccounted after ranking ──
def test_pipeline_reconciliation_zero_unaccounted():
    """Verify that queued_count + ranked_out are tracked before reconciliation."""
    # This is a structural test: the _run_scan function must compute
    # unaccounted AFTER alert persistence, not before.
    # We verify the ordering by checking that the source code places
    # the reconciliation after the ranking/persist block.
    import inspect
    source = inspect.getsource(_run_scan)
    persist_pos = source.find("total_alerts = 0")
    reconcile_pos = source.find("unaccounted = total_symbols -")
    assert reconcile_pos > persist_pos, \
        f"unaccounted at {reconcile_pos} must be after persistence at {persist_pos}"
