import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

# Ensure app is in path
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from config import PULLBACK_CONFIG, SCANNER_DEDUP_ENTRY_TOLERANCE_PCT
from swing_utils import (
    measure_pullback, ImpulseLeg, SwingPoint, PivotKind, PullbackStructure, RejectionReason
)
from database import (
    canonicalize_scanner_name, generate_canonical_alert_fingerprint, save_alert_if_new
)

# ═══════════════════════════════════════════════════════════════════════
# 1. PULLBACK BOUNDARY & INVARIANT TESTS
# ═══════════════════════════════════════════════════════════════════════

def _create_mock_pullback_df(duration_bars: int, min_price: float, volume_ratio: float) -> tuple[pd.DataFrame, ImpulseLeg]:
    """Helper to generate a mock DataFrame and ImpulseLeg matching specific test metrics."""
    # Impulse from 100 to 200 (range = 100). Depth% = (200 - min_price) / 100 * 100 = 200 - min_price
    impulse_start = SwingPoint(index=0, date=date(2026, 8, 1), price=100.0, kind=PivotKind.LOW, is_plateau=False)
    impulse_end = SwingPoint(index=10, date=date(2026, 8, 15), price=200.0, kind=PivotKind.HIGH, is_plateau=False)
    impulse = ImpulseLeg(
        start=impulse_start, end=impulse_end, gain_pct=100.0,
        atr_multiple=5.0, median_volume=1000.0
    )
    
    # Total bars = 10 (impulse) + 1 + duration_bars + 1 (trigger)
    total_bars = 10 + 1 + duration_bars + 1
    dates = pd.date_range("2026-08-01", periods=total_bars, freq="D")
    
    df = pd.DataFrame({
        "Date": dates,
        "Open": [150.0] * total_bars,
        "High": [205.0] * total_bars,
        "Low": [140.0] * total_bars,
        "Close": [180.0] * total_bars,
        "Volume": [1000.0] * total_bars,
        "SMA50": [120.0] * total_bars,
        "RSI": [55.0] * total_bars,
    })
    df.attrs['symbol'] = "TESTSYM"
    
    # Configure the pullback leg (indices 11 to 10 + duration_bars)
    pb_start_idx = 11
    pb_end_idx = pb_start_idx + duration_bars
    
    # Set the minimum price at min_pb_idx
    if duration_bars > 0:
        for idx in range(pb_start_idx, pb_end_idx):
            df.loc[idx, "Low"] = 195.0
            df.loc[idx, "Close"] = 195.0
            df.loc[idx, "Volume"] = 1000.0 * volume_ratio
        df.loc[pb_start_idx, "Low"] = min_price
        df.loc[pb_start_idx, "Close"] = min_price
        
    return df, impulse


def test_pullback_depth_boundaries():
    """Verify exact min and max depth boundaries under PULLBACK_CONFIG."""
    config = dict(PULLBACK_CONFIG)
    # MIN_DEPTH_PCT = 10.0 -> min_price = 190.0 -> depth = 10.0% (PASS)
    df_pass_min, imp = _create_mock_pullback_df(duration_bars=5, min_price=190.0, volume_ratio=0.8)
    ps_pass_min = measure_pullback(df_pass_min, imp, config)
    assert ps_pass_min.valid is True, f"Expected 10.0% depth to pass, got rejected with {ps_pass_min.rejection_reason}"

    # Below floor: depth = 9.9% -> min_price = 190.1 (FAIL: REJ_DEPTH_TOO_SHALLOW)
    df_fail_min, imp = _create_mock_pullback_df(duration_bars=5, min_price=190.1, volume_ratio=0.8)
    ps_fail_min = measure_pullback(df_fail_min, imp, config)
    assert ps_fail_min.valid is False
    assert ps_fail_min.rejection_reason == RejectionReason.REJ_DEPTH_TOO_SHALLOW

    # MAX_DEPTH_PCT = 78.6 -> min_price = 121.4 -> depth = 78.6% (PASS)
    df_pass_max, imp = _create_mock_pullback_df(duration_bars=5, min_price=121.4, volume_ratio=0.8)
    ps_pass_max = measure_pullback(df_pass_max, imp, config)
    assert ps_pass_max.valid is True, f"Expected 78.6% depth to pass, got rejected with {ps_pass_max.rejection_reason}"

    # Above ceiling: depth = 78.7% -> min_price = 121.3 (FAIL: REJ_DEPTH_TOO_DEEP)
    df_fail_max, imp = _create_mock_pullback_df(duration_bars=5, min_price=121.3, volume_ratio=0.8)
    ps_fail_max = measure_pullback(df_fail_max, imp, config)
    assert ps_fail_max.valid is False
    assert ps_fail_max.rejection_reason == RejectionReason.REJ_DEPTH_TOO_DEEP
    print("✅ test_pullback_depth_boundaries passed!")


def test_pullback_volume_and_duration_boundaries():
    """Verify volume ratio <= 1.25 and duration <= 20 bars boundaries."""
    config = dict(PULLBACK_CONFIG)
    # Volume ratio 1.25 (inclusive boundary -> PASS)
    df_vol_pass, imp = _create_mock_pullback_df(duration_bars=5, min_price=160.0, volume_ratio=1.25)
    ps_vol_pass = measure_pullback(df_vol_pass, imp, config)
    assert ps_vol_pass.valid is True, f"Expected 1.25x volume to pass, got {ps_vol_pass.rejection_reason}"

    # Volume ratio 1.26 (exceeds boundary -> FAIL)
    df_vol_fail, imp = _create_mock_pullback_df(duration_bars=5, min_price=160.0, volume_ratio=1.26)
    ps_vol_fail = measure_pullback(df_vol_fail, imp, config)
    assert ps_vol_fail.valid is False
    assert ps_vol_fail.rejection_reason == RejectionReason.REJ_VOLUME_NOT_CONTRACTING

    # Duration 20 bars (inclusive boundary -> PASS)
    df_dur_pass, imp = _create_mock_pullback_df(duration_bars=20, min_price=160.0, volume_ratio=0.8)
    ps_dur_pass = measure_pullback(df_dur_pass, imp, config)
    assert ps_dur_pass.valid is True, f"Expected 20 bars duration to pass, got {ps_dur_pass.rejection_reason}"

    # Duration 21 bars (exceeds boundary -> FAIL)
    df_dur_fail, imp = _create_mock_pullback_df(duration_bars=21, min_price=160.0, volume_ratio=0.8)
    ps_dur_fail = measure_pullback(df_dur_fail, imp, config)
    assert ps_dur_fail.valid is False
    assert ps_dur_fail.rejection_reason == RejectionReason.REJ_DURATION_LONG
    print("✅ test_pullback_volume_and_duration_boundaries passed!")


def test_decision_vs_telemetry_threshold_invariant():
    """Mathematically assert that evaluator thresholds match telemetry reporting thresholds."""
    config = dict(PULLBACK_CONFIG)
    # Arbitrary test values to prove dynamic synchronization
    config["MAX_DEPTH_PCT"] = 72.5
    config["MAX_PB_VOLUME_RATIO"] = 1.15
    config["MAX_DURATION"] = 18

    # Force rejection by depth
    df, imp = _create_mock_pullback_df(duration_bars=5, min_price=120.0, volume_ratio=0.5) # depth = 80% > 72.5%
    ps = measure_pullback(df, imp, config)
    assert ps.valid is False
    
    # Telemetry extraction matching pullback_pipeline.py
    telemetry_req = {
        "min_depth_pct": float(config.get("MIN_DEPTH_PCT", 10.0)),
        "max_depth_pct": float(config.get("MAX_DEPTH_PCT", 78.6)),
        "max_volume_ratio": float(config.get("MAX_PB_VOLUME_RATIO", 1.25)),
        "max_duration_bars": int(config.get("MAX_DURATION", 20))
    }
    
    # Find depth gate in ps.stage_results
    depth_gate = next(g for g in ps.stage_results if g.gate == RejectionReason.REJ_DEPTH_TOO_DEEP.name)
    
    # Mathematical assertion of invariant
    assert depth_gate.threshold == telemetry_req["max_depth_pct"], \
        f"Evaluator threshold ({depth_gate.threshold}) != Telemetry threshold ({telemetry_req['max_depth_pct']})"
    print("✅ test_decision_vs_telemetry_threshold_invariant passed!")


# ═══════════════════════════════════════════════════════════════════════
# 2. CANONICAL ALERT FINGERPRINT & DEDUPLICATION MATRIX TESTS
# ═══════════════════════════════════════════════════════════════════════

def test_canonical_scanner_name_normalization():
    """Verify scanner canonicalization eliminates cross-scanner aliasing."""
    assert canonicalize_scanner_name("TECHNICAL", "PULLBACK") == "PULLBACK"
    assert canonicalize_scanner_name("PULLBACK", "TECHNICAL") == "PULLBACK"
    assert canonicalize_scanner_name("BREAKOUT", "STAGE2_BREAKOUT") == "EOD"
    assert canonicalize_scanner_name("EOD", "BREAKOUT") == "EOD"
    assert canonicalize_scanner_name("MULTI-TF", "CONFIRMED_BREAKOUT") == "MULTI_TF"
    assert canonicalize_scanner_name("MULTIBAGGER", "HIGH_QUALITY") == "MULTIBAGGER"
    print("✅ test_canonical_scanner_name_normalization passed!")


def test_canonical_alert_fingerprint_determinism():
    """Verify that identical setups on weekend sweeps produce the exact same fingerprint."""
    friday_date = date(2026, 9, 4)
    fp1 = generate_canonical_alert_fingerprint("ELGIEQUIP", "PULLBACK", friday_date, "LONG", "PULLBACK", 642.10, 0.5)
    fp2 = generate_canonical_alert_fingerprint("ELGIEQUIP", "PULLBACK", friday_date, "LONG", "PULLBACK", 642.10, 0.5)
    assert fp1 == fp2, "Fingerprints for identical setup must be identical"

    # Different setup type -> different fingerprint
    fp_diff_setup = generate_canonical_alert_fingerprint("ELGIEQUIP", "PULLBACK", friday_date, "LONG", "HAMMER_PULLBACK", 642.10, 0.5)
    assert fp1 != fp_diff_setup, "Different setup types must produce different fingerprints"

    # Different trading date -> different fingerprint
    monday_date = date(2026, 9, 7)
    fp_monday = generate_canonical_alert_fingerprint("ELGIEQUIP", "PULLBACK", monday_date, "LONG", "PULLBACK", 642.10, 0.5)
    assert fp1 != fp_monday, "Different trading dates must produce different fingerprints"
    print("✅ test_canonical_alert_fingerprint_determinism passed!")


def test_dedup_matrix_scenario_1_exact_weekend_duplicate():
    """Scenario 1: Friday alert saved -> Saturday sweep returns inserted=False (suppressed)."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock DB query finding the prior Friday alert by fingerprint or exact date/price
    existing_row = (101, date(2026, 9, 4), "2026-09-04 15:30:00+05:30", 642.10, "OPEN", "PULLBACK", "PULLBACK", date(2026, 9, 4))
    mock_cur.fetchone.return_value = existing_row

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="ELGIEQUIP", breakout_type="PULLBACK", alert_time="2026-09-05 11:42:00+05:30",
        scanner="PULLBACK", entry_price=642.10, stop_loss=610.0, target_1=680.0,
        score=85, source_trading_date=date(2026, 9, 4), conn=mock_conn
    )
    assert inserted is False
    assert "Duplicate: Adjusted alert already persisted" in reason
    print("✅ test_dedup_matrix_scenario_1_exact_weekend_duplicate passed!")


def test_dedup_matrix_scenario_2_friday_unsaved_saturday_saves():
    """Scenario 2: Friday alert was not saved -> Saturday first discovery returns inserted=True."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # 1st fetchone: prior_adjusted_alert -> None (no duplicate found)
    # 2nd fetchone: active open position -> None (no active position)
    # 3rd fetchone: INSERT RETURNING id -> (205,)
    mock_cur.fetchone.side_effect = [None, None, (205,)]

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="ELGIEQUIP", breakout_type="PULLBACK", alert_time="2026-09-05 11:42:00+05:30",
        scanner="PULLBACK", entry_price=642.10, stop_loss=610.0, target_1=680.0,
        score=85, source_trading_date=date(2026, 9, 4), conn=mock_conn
    )
    assert inserted is True
    print("✅ test_dedup_matrix_scenario_2_friday_unsaved_saturday_saves passed!")


def test_dedup_matrix_scenario_3_different_entry_price_outside_tolerance():
    """Scenario 3: Entry price deviates significantly (> tolerance) -> treated as distinct setup, inserted=True."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Deduplication query returns None because price delta > 0.5%
    # Next fetchone: no open trade -> None
    # Next fetchone: INSERT -> (206,)
    mock_cur.fetchone.side_effect = [None, None, (206,)]

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="ELGIEQUIP", breakout_type="PULLBACK", alert_time="2026-09-05 11:42:00+05:30",
        scanner="PULLBACK", entry_price=660.00, stop_loss=630.0, target_1=700.0,
        score=85, source_trading_date=date(2026, 9, 4), conn=mock_conn
    )
    assert inserted is True
    print("✅ test_dedup_matrix_scenario_3_different_entry_price_outside_tolerance passed!")


def test_dedup_matrix_scenario_4_open_trade_flows_to_trade_evolution():
    """Scenario 4: An active OPEN trade exists from a prior session -> flows directly to Trade Evolution."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # 1st fetch: prior_adjusted_alert check -> None (different session/setup)
    # 2nd fetch: existing OPEN trade found!
    open_trade = (
        50, "ELGIEQUIP", 600.0, 570.0, 650.0, 680.0, 720.0, "INITIAL_PULLBACK", 80,
        date(2026, 8, 20), "2026-08-20 10:00:00+05:30", {}, "INITIAL", 1, 1, "PULLBACK"
    )
    # 3rd fetch: last_event query from alert_events -> None (first re-trigger event)
    # 4th fetch: SELECT COUNT(*), COUNT(DISTINCT pattern) -> (1, 1)
    # 5th fetch: INSERT INTO alert_events ... RETURNING id -> (10,)
    mock_cur.fetchone.side_effect = [None, open_trade, None, (1, 1), (10,)]

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="ELGIEQUIP", breakout_type="PULLBACK", alert_time="2026-09-05 11:42:00+05:30",
        scanner="PULLBACK", entry_price=642.10, stop_loss=610.0, target_1=680.0,
        score=88, source_trading_date=date(2026, 9, 4), conn=mock_conn
    )
    # Trade evolution evaluates and records re-trigger event on existing OPEN position
    assert inserted is False
    assert "Re-trigger recorded" in reason
    print("✅ test_dedup_matrix_scenario_4_open_trade_flows_to_trade_evolution passed!")


if __name__ == "__main__":
    print("🚀 RUNNING HARDENED PULLBACK & DEDUPLICATION TESTS...")
    test_pullback_depth_boundaries()
    test_pullback_volume_and_duration_boundaries()
    test_decision_vs_telemetry_threshold_invariant()
    test_canonical_scanner_name_normalization()
    test_canonical_alert_fingerprint_determinism()
    test_dedup_matrix_scenario_1_exact_weekend_duplicate()
    test_dedup_matrix_scenario_2_friday_unsaved_saturday_saves()
    test_dedup_matrix_scenario_3_different_entry_price_outside_tolerance()
    test_dedup_matrix_scenario_4_open_trade_flows_to_trade_evolution()
    print("🎉 ALL 9 HARDENED UNIT TESTS PASSED SUCCESSFULLY!")
