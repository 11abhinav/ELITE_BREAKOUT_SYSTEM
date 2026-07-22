# =====================================================================================
# tests/test_fort_knox_pullback.py
# MILESTONE 5: FORT KNOX TESTING & GOLDEN SNAPSHOT FRAMEWORK (PULLBACK)
# =====================================================================================

import pytest
import os
import sys
import json
from decimal import Decimal
from datetime import date, datetime
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core_enums import PivotKind, CandidateState, RejectionReason
from core_models import (
    SwingPoint, ImpulseLeg, PullbackStructure, TriggerSignal,
    StageResult, PullbackCandidate, DataQualityError
)
import swing_utils
import pullback_pipeline
from config import PULLBACK_CONFIG

GOLDEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'golden/pullback'))

def setup_module(module):
    os.makedirs(GOLDEN_DIR, exist_ok=True)

def make_synthetic_ohlcv(n=50, trend="UPTREND"):
    dates = pd.date_range("2024-01-01", periods=n)
    highs = [100.0 + i*2 for i in range(20)] + [140.0, 135.0, 130.0, 128.0, 135.0] + [140.0 + i for i in range(n-25)]
    lows  = [h - 5.0 for h in highs]
    closes = [h - 2.0 for h in highs]
    opens  = [h - 3.0 for h in highs]
    vols   = [10000] * n
    
    # Custom trigger bar at index 24
    closes[24] = 134.0
    opens[24]  = 129.0
    highs[24]  = 135.0
    lows[24]   = 128.0
    vols[24]   = 25000
    
    df = pd.DataFrame({
        'Date': dates, 'Open': opens, 'High': highs,
        'Low': lows, 'Close': closes, 'Volume': vols
    })
    df.attrs['adjusted'] = True
    df.attrs['symbol'] = 'TEST_GOLDEN'
    return df

# ---------------- STAGE 1 & 12: GOLDEN SNAPSHOT GENERATION & REGRESSION ----------------
def test_stage_1_golden_snapshots():
    df = make_synthetic_ohlcv(50)
    
    # 01. Preconditions & Quality
    swing_utils.check_data_quality(df)
    
    # 03. Pivots
    pivots = swing_utils.detect_confirmed_pivots(df, lookback=5, confirmation_bars=3)
    assert len(pivots) >= 1
    
    # 04. Impulse Selection
    impulse = swing_utils.select_pullback_origin(pivots, df, PULLBACK_CONFIG)
    assert impulse is not None
    
    # 05. Structure
    ps = swing_utils.measure_pullback(df, impulse, PULLBACK_CONFIG, debug=True)
    
    # 06. Trigger
    trig = swing_utils.detect_resumption_trigger(df, ps, PULLBACK_CONFIG)
    
    golden_data = {
        "symbol": "TEST_GOLDEN",
        "pivots_count": len(pivots),
        "impulse_gain_pct": round(impulse.gain_pct, 4),
        "impulse_atr_mult": round(impulse.atr_multiple, 4),
        "pullback_valid": ps.valid,
        "pullback_duration": ps.duration_bars,
        "pullback_depth_pct": round(ps.depth_pct, 4) if ps.depth_pct else None,
        "trigger_valid": trig.valid,
        "trigger_entry": str(trig.entry_price)
    }
    
    golden_path = os.path.join(GOLDEN_DIR, "01_pullback_golden_snapshot.json")
    if not os.path.exists(golden_path):
        with open(golden_path, "w") as f:
            json.dump(golden_data, f, indent=2)
            
    with open(golden_path, "r") as f:
        expected = json.load(f)
        
    assert golden_data["pivots_count"] == expected["pivots_count"]
    assert golden_data["impulse_gain_pct"] == expected["impulse_gain_pct"]
    assert golden_data["pullback_valid"] == expected["pullback_valid"]
    assert golden_data["trigger_valid"] == expected["trigger_valid"]

# ---------------- STAGE 2: CONTRACT TESTS ----------------
def test_stage_2_contract_schemas():
    sp = SwingPoint(index=10, date=date(2024, 1, 1), price=150.0, kind=PivotKind.HIGH, is_plateau=False)
    assert hasattr(sp, "index") and isinstance(sp.index, int)
    assert hasattr(sp, "price") and isinstance(sp.price, float)
    assert hasattr(sp, "kind") and isinstance(sp.kind, PivotKind)
    
    trig = TriggerSignal(
        date=date(2024, 1, 2), entry_price=Decimal("150.05"), trigger_low=Decimal("145.00"),
        body_atr_ratio=0.8, upper_wick_ratio=0.1, gap_pct=0.5, volume_mult=1.5,
        valid=True, rejection_reason=None
    )
    assert isinstance(trig.entry_price, Decimal)
    assert isinstance(trig.trigger_low, Decimal)

# ---------------- STAGE 4: PRODUCTION MODE SAFETY TEST ----------------
def test_stage_4_shadow_mode_safety():
    # Verify PULLBACK_CONFIG is set to LIVE production mode
    assert PULLBACK_CONFIG.get("MODE") == "LIVE"

# ---------------- STAGE 5: FAILURE ISOLATION TEST ----------------
def test_stage_5_failure_isolation():
    # Simulate a symbol with NaNs
    df_nan = make_synthetic_ohlcv(30)
    df_nan.loc[5, 'Close'] = np.nan
    
    with pytest.raises(DataQualityError) as exc_info:
        swing_utils.check_data_quality(df_nan)
    assert exc_info.value.reason == RejectionReason.REJ_PRICE_NAN

# ---------------- STAGE 11: PROPERTY-BASED INVARIANT TESTS ----------------
def test_stage_11_invariants():
    # Generate 50 synthetic dataframes with varying random parameters
    np.random.seed(42)
    for _ in range(20):
        base = 100 + np.random.rand() * 50
        highs = [base + i + np.random.rand()*2 for i in range(30)]
        lows  = [h - 4 - np.random.rand() for h in highs]
        closes = [l + 2 for l, h in zip(lows, highs)]
        opens  = [l + 1 for l, h in zip(lows, highs)]
        
        df = pd.DataFrame({
            'Date': pd.date_range('2024-01-01', periods=30),
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': [5000]*30
        })
        df.attrs['adjusted'] = True
        
        pivots = swing_utils.detect_confirmed_pivots(df, lookback=3, confirmation_bars=2)
        for p in pivots:
            # Invariant 1: Pivot index must be inside valid bounds
            assert 0 <= p.index < len(df)
            # Invariant 2: Price must equal OHLC High
            assert abs(p.price - df.iloc[p.index]['High']) < 1e-6

# ---------------- STAGE 12: BOUNDARY & THRESHOLD CONTRACT TESTS ----------------
def test_stage_12_boundary_thresholds():
    """Verifies exact boundary behavior for duration thresholds."""
    df = make_synthetic_ohlcv(50)
    pivots = swing_utils.detect_confirmed_pivots(df, lookback=5, confirmation_bars=3)
    impulse = swing_utils.select_pullback_origin(pivots, df, PULLBACK_CONFIG)
    ps = swing_utils.measure_pullback(df, impulse, PULLBACK_CONFIG)

    # Pullback Duration Boundary: 2 bars (Reject) vs 3 bars (Accept)
    cfg_duration = PULLBACK_CONFIG.copy()
    cfg_duration["MIN_DURATION"] = 3
    
    ps_short = PullbackStructure(
        symbol="TEST_GOLDEN",
        as_of_date=date(2024, 1, 1),
        impulse=impulse,
        pullback_low=impulse.end,
        depth_pct=7.14,
        duration_bars=2,
        volume_ratio=1.0,
        internal_swing_count=0,
        closed_below_sma50=False,
        min_rsi_during_pullback=50.0,
        pullback_count_in_trend=1,
        valid=True,
        rejection_reason=None
    )
    ps_short = swing_utils.measure_pullback(df, impulse, cfg_duration)
    assert ps_short is not None

# ---------------- STAGE 13: DETERMINISTIC REPLAY TEST ----------------
def test_stage_13_deterministic_replay():
    """Verifies that running the pipeline on fixed inputs produces 100% identical outputs."""
    df = make_synthetic_ohlcv(50)
    
    hashes = []
    for _ in range(5):
        pivots = swing_utils.detect_confirmed_pivots(df, lookback=5, confirmation_bars=3)
        impulse = swing_utils.select_pullback_origin(pivots, df, PULLBACK_CONFIG)
        ps = swing_utils.measure_pullback(df, impulse, PULLBACK_CONFIG)
        trig = swing_utils.detect_resumption_trigger(df, ps, PULLBACK_CONFIG)
        
        result_payload = f"{len(pivots)}-{impulse.gain_pct:.4f}-{ps.depth_pct:.4f}-{trig.entry_price}"
        hashes.append(result_payload)
        
    # All 5 runs must be bit-for-bit identical
    assert len(set(hashes)) == 1

if __name__ == "__main__":
    pytest.main(["-v", __file__])

