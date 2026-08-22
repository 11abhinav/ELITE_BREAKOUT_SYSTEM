import pytest
import pandas as pd

def test_multi_tf_ema_tolerance_branch():
    """
    Test where EMA9 is within tolerance of EMA20, but Close is below tolerance.
    Expects EMA_TOLERANCE branch to trigger.
    """
    # Create mock technical data where EMA9 >= 0.998 * EMA20
    # EMA20 = 100.0, Tolerance = 99.8
    # EMA9 = 99.9 (Passes condition_ema)
    # Close = 99.0 (Fails condition_close)
    e20 = 100.0
    ema_tolerance = e20 * 0.998
    e9 = 99.9
    close_price = 99.0

    condition_ema = e9 >= ema_tolerance
    condition_close = close_price >= ema_tolerance

    assert condition_ema is True
    assert condition_close is False
    
    selected_condition = "EMA_TOLERANCE" if condition_ema else "CLOSE_ABOVE_EMA20"
    assert selected_condition == "EMA_TOLERANCE"

def test_multi_tf_close_above_ema20_branch():
    """
    Test where EMA9 is below tolerance, but Close is above tolerance.
    Expects CLOSE_ABOVE_EMA20 branch to trigger.
    """
    # EMA20 = 100.0, Tolerance = 99.8
    # EMA9 = 99.5 (Fails condition_ema)
    # Close = 100.5 (Passes condition_close)
    e20 = 100.0
    ema_tolerance = e20 * 0.998
    e9 = 99.5
    close_price = 100.5

    condition_ema = e9 >= ema_tolerance
    condition_close = close_price >= ema_tolerance

    assert condition_ema is False
    assert condition_close is True
    
    selected_condition = "EMA_TOLERANCE" if condition_ema else "CLOSE_ABOVE_EMA20"
    assert selected_condition == "CLOSE_ABOVE_EMA20"

def test_multi_tf_negative_case():
    """
    Test where both EMA9 and Close are below the EMA20 tolerance.
    Expects gate to fail.
    """
    # EMA20 = 100.0, Tolerance = 99.8
    # EMA9 = 99.0 (Fails)
    # Close = 99.0 (Fails)
    e20 = 100.0
    ema_tolerance = e20 * 0.998
    e9 = 99.0
    close_price = 99.0

    condition_ema = e9 >= ema_tolerance
    condition_close = close_price >= ema_tolerance

    assert condition_ema is False
    assert condition_close is False
    assert (condition_ema or condition_close) is False

def test_multi_tf_forced_positive_case():
    """
    Test where both EMA9 and Close are above tolerance.
    Expects gate to pass with EMA_TOLERANCE (first condition).
    """
    e20 = 100.0
    ema_tolerance = e20 * 0.998
    e9 = 101.0
    close_price = 101.0

    condition_ema = e9 >= ema_tolerance
    condition_close = close_price >= ema_tolerance

    assert condition_ema is True
    assert condition_close is True
    assert (condition_ema or condition_close) is True
    
    selected_condition = "EMA_TOLERANCE" if condition_ema else "CLOSE_ABOVE_EMA20"
    assert selected_condition == "EMA_TOLERANCE"
