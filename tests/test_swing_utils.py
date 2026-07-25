import pytest
import numpy as np
import pandas as pd
from decimal import Decimal
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core_enums import PivotKind, RejectionReason
from core_models import SwingPoint, ImpulseLeg, PullbackStructure, TriggerSignal, DataQualityError
from swing_utils import (
    detect_confirmed_pivots, 
    select_pullback_origin, 
    measure_pullback, 
    detect_resumption_trigger,
    check_data_quality,
    round_to_tick
)

def make_df(highs, lows=None, closes=None, opens=None, vols=None, adjusted=True):
    n = len(highs)
    if lows is None: lows = [h - 1 for h in highs]
    if closes is None: closes = [h - 0.5 for h in highs]
    if opens is None: opens = [h - 0.5 for h in highs]
    if vols is None: vols = [1000] * n
    
    df = pd.DataFrame({
        'Date': pd.date_range('2023-01-01', periods=n),
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': vols
    })
    df.attrs['adjusted'] = adjusted
    return df

def test_data_quality_unadjusted():
    df = make_df([100]*20, adjusted=False)
    with pytest.raises(DataQualityError) as exc_info:
        check_data_quality(df)
    assert exc_info.value.reason == RejectionReason.REJ_UNADJUSTED_DATA

def test_data_quality_nan():
    df = make_df([100]*20)
    df.loc[5, 'Close'] = np.nan
    with pytest.raises(DataQualityError) as exc_info:
        check_data_quality(df)
    assert exc_info.value.reason == RejectionReason.REJ_PRICE_NAN

def test_detect_pivots_standard():
    highs = [100, 105, 110, 115, 120, 125, 130, 150, 140, 130, 120, 110, 100, 90, 80]
    df = make_df(highs)
    pivots = detect_confirmed_pivots(df, lookback=5, confirmation_bars=3)
    assert len(pivots) == 1
    assert pivots[0].index == 7
    assert pivots[0].price == 150
    assert not pivots[0].is_plateau

def test_detect_pivots_equal_highs():
    highs = [100, 105, 110, 115, 120, 125, 130, 150, 140, 150, 120, 110, 100, 90, 80]
    df = make_df(highs)
    pivots = detect_confirmed_pivots(df, lookback=5, confirmation_bars=3)
    assert len(pivots) == 1
    assert pivots[0].index == 9
    assert pivots[0].is_plateau
    
def test_consecutive_equal_highs():
    highs = [100, 105, 110, 150, 150, 150, 140, 130, 120, 110, 100, 90, 80]
    df = make_df(highs)
    pivots = detect_confirmed_pivots(df, lookback=3, confirmation_bars=3)
    assert len(pivots) == 1
    assert pivots[0].index == 5
    assert pivots[0].is_plateau

def test_impulse_origin_preceding_swing_low():
    # Construct a historical min (low=10 at index 2) followed by a pivot high (high=100 at index 10)
    # Then a second pivot high (high=200 at index 20) with a local low (low=80 at index 15)
    # The upleg for the second pivot high MUST choose index 15 (low=80), NOT index 2 (global min=10)!
    highs = [50, 60, 70, 80, 85, 90, 95, 100, 90, 85, 90, 110, 130, 150, 170, 160, 170, 180, 190, 200, 190, 180, 170, 160]
    lows  = [40, 50, 10, 70, 75, 80, 85,  90, 85, 80, 85, 100, 120, 140, 160, 60,  150, 170, 180, 190, 180, 170, 160, 150]
    df = make_df(highs, lows=lows)
    
    pivots = detect_confirmed_pivots(df, lookback=3, confirmation_bars=3)
    assert len(pivots) >= 2
    
    impulse = select_pullback_origin(pivots, df, config={"MIN_IMPULSE_GAIN_PCT": 5.0, "MIN_IMPULSE_ATR": 1.0})
    assert impulse is not None
    # Prove it anchored to the local low preceding the second pivot high (index 15), NOT index 2 (global min=10)!
    assert impulse.start.index > 2
    assert impulse.start.index == 15

def test_structure_reset_new_high():
    highs  = [100]*20 + [150] + [160, 140, 130] # Close at bar 21 breaks 150!
    lows   = [90]*20  + [140] + [155, 130, 120]
    closes = [95]*20  + [145] + [158, 135, 125]
    df = make_df(highs, lows=lows, closes=closes)
    
    impulse = ImpulseLeg(
        start=SwingPoint(0, df.iloc[0].Date, 90, PivotKind.LOW, False),
        end=SwingPoint(20, df.iloc[20].Date, 150, PivotKind.HIGH, False),
        gain_pct=50.0, atr_multiple=5.0, median_volume=1000.0
    )
    ps = measure_pullback(df, impulse, config={})
    assert not ps.valid
    assert ps.rejection_reason == RejectionReason.REJ_STRUCTURE_RESET

def test_duration_boundaries():
    # 2 bars pullback -> fail (MIN_DURATION=3)
    highs_short = [100]*20 + [150] + [145, 142, 144]
    lows_short  = [90]*20  + [140] + [140, 135, 140]
    df_short = make_df(highs_short, lows=lows_short)
    
    impulse = ImpulseLeg(
        start=SwingPoint(0, df_short.iloc[0].Date, 90, PivotKind.LOW, False),
        end=SwingPoint(20, df_short.iloc[20].Date, 150, PivotKind.HIGH, False),
        gain_pct=50.0, atr_multiple=5.0, median_volume=1000.0
    )
    ps_short = measure_pullback(df_short, impulse, config={"MIN_DURATION": 3, "MAX_PB_VOLUME_RATIO": 2.0})
    assert not ps_short.valid
    assert ps_short.rejection_reason == RejectionReason.REJ_DURATION_SHORT

def test_round_to_tick():
    assert round_to_tick(100.03) == Decimal("100.05")
    assert round_to_tick(100.01) == Decimal("100.00")
    assert round_to_tick(250.77) == Decimal("250.75")

if __name__ == "__main__":
    pytest.main(["-v", __file__])
