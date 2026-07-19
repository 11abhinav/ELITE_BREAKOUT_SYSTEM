import pytest
from app.sl_target_helper import compute_sl_and_target

def test_sl_target_invariants_happy_path():
    """
    Input: Standard breakout entry.
    Expected: Stop < Entry < Target 1.
    Decision/Reason: Proper risk management requires targets to be structurally sound.
    """
    res = compute_sl_and_target(
        entry_price=100.0,
        atr=2.0,
        candle_range=2.5,
        mode="EOD",
        engine_version="v1.0",
        swing_low=97.0,
        swing_high=125.0,
        volume_ratio=2.0
    )
    
    assert res.get("is_rejected", False) == False, "Valid structural setup should not be rejected"
    
    stop_loss = res["stop_loss"]
    t1 = res["target_1"]
    
    assert stop_loss < 100.0, "Stop loss must be strictly below entry for a long trade"
    assert t1 > 100.0, "Target 1 must be strictly above entry for a long trade"
    
    # Minimum R:R for EOD is 2.5x per config
    rr = res["natural_rr"]
    assert rr >= 2.5, f"Expected RR to satisfy minimum (2.5), got {rr}"

def test_sl_target_rejection_poor_rr():
    """
    Input: Setup where swing low is very far away, making risk huge.
    Expected: Rejected due to poor R:R.
    Decision/Reason: NO_VALID_STRUCTURAL_TARGET
    """
    res = compute_sl_and_target(
        entry_price=100.0,
        atr=2.0,
        candle_range=2.5,
        mode="EOD",
        engine_version="v1.0",
        swing_low=70.0,  # Extremely wide stop (risk = 30)
        swing_high=110.0, # Target only 10 away
        volume_ratio=2.0
    )
    
    assert res.get("is_rejected") is True, "Trade with terrible RR must be rejected"
    assert "rejection_reason" in res
    assert "NO_VALID_STRUCTURAL_TARGET" in res["rejection_reason"]

def test_sl_target_divide_by_zero_protection():
    """
    Input: Pathological setup where stop_loss == entry_price.
    Expected: Handled safely, doesn't throw ZeroDivisionError.
    """
    # This shouldn't normally happen because stop buffer lowers the stop,
    # but we force it by making ATR 0 and pivot exactly at entry
    try:
        res = compute_sl_and_target(
            entry_price=100.0,
            atr=0.0,
            candle_range=0.0,
            mode="EOD",
            engine_version="v1.0",
            swing_low=100.0,
            swing_high=110.0
        )
        
        # As long as it didn't throw ZeroDivisionError, we pass the structural test.
        # It should either reject the setup or create a safe fallback stop.
        if not res.get("is_rejected"):
            assert res["stop_loss"] < 100.0, "Fallback stop must be placed below entry even if ATR is 0"
            
    except ZeroDivisionError:
        pytest.fail("Engine failed with ZeroDivisionError on 0-risk setup")

def test_sl_target_v2_engine_happy_path():
    """
    Input: V2 Engine with standard parameters.
    Expected: Full institutional metrics (kelly fraction, position sizing).
    """
    res = compute_sl_and_target(
        entry_price=100.0,
        atr=2.0,
        candle_range=2.5,
        mode="BREAKOUT",
        engine_version="v2.0",
        swing_low=95.0,
        swing_high=110.0,
        adx=30.0,
        rsi=60.0
    )
    
    assert res["engine_version"] == "2.0"
    
    sl = res["risk"]["stop_loss"]
    t1 = res["targets"]["t1"]["price"]
    
    assert sl < 100.0, "Stop loss must be below entry"
    assert t1 > 100.0, "Target must be above entry"
    
    assert "position_size_pct" in res["risk"]
    assert res["risk"]["position_size_pct"] > 0, "Position size should be properly calculated"
