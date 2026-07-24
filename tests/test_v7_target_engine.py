import pytest
from app.sl_target_helper import compute_sl_and_target

def test_eod_target_cluster_nearest_valid_invariant():
    """Verify that EOD mode evaluates target clusters (t1, t2, t3) and selects the FIRST cluster meeting min_rr."""
    # Test case 1: t1 (prev_day_high=114.5) R:R < 2.5, t2 (high_52w=125.0) R:R >= 2.5 -> should select t2 (125.0)
    res_t2 = compute_sl_and_target(
        100.0,                 # entry_price
        2.0,                   # atr
        3.0,                   # candle_range
        "EOD",                 # mode
        swing_low=95.0,        # SL around 93.40 -> risk = 6.60
        prev_day_high=114.5,   # t1 R:R = (114.5 - 100) / 6.60 = 2.20x (< 2.5x)
        high_52w=125.0,        # t2 R:R = (125.0 - 100) / 6.60 = 3.79x (>= 2.5x)
    )
    assert not res_t2.get("is_rejected"), f"Expected pass, got rejection: {res_t2.get('rejection_reason')}"
    assert res_t2.get("target_1") == 125.0, f"Expected t2 (125.0), got {res_t2.get('target_1')}"

    # Test case 2: t1 (prev_day_high=120.0) R:R >= 2.5 -> should select t1 (nearest valid wins, not highest R:R)
    res_t1 = compute_sl_and_target(
        100.0,                 # entry_price
        2.0,                   # atr
        3.0,                   # candle_range
        "EOD",                 # mode
        swing_low=95.0,        # SL around 93.40 -> risk = 6.60
        prev_day_high=120.0,   # t1 R:R = (120.0 - 100) / 6.60 = 3.03x (>= 2.5x)
        high_52w=140.0,        # t2 R:R = 6.06x
    )
    assert not res_t1.get("is_rejected")
    assert res_t1.get("target_1") == 120.0, f"Expected nearest valid t1 (120.0), got {res_t1.get('target_1')}"

def test_invalid_stop_placement_rejection():
    """Verify that raw_sl >= entry price explicitly triggers INVALID_STOP_PLACEMENT or NO_VALID_STRUCTURAL_STOP rejection."""
    res = compute_sl_and_target(
        100.0,                 # entry_price
        2.0,                   # atr
        3.0,                   # candle_range
        "EOD",                 # mode
        swing_low=105.0,       # Invalid swing low above entry price
    )
    assert res.get("is_rejected")
    assert res.get("rejection_reason") in ("INVALID_STOP_PLACEMENT", "NO_VALID_STRUCTURAL_STOP")
