"""
tests/test_accumulation_scanner.py

Comprehensive Unit Test Suite for ACCUMULATION_SCANNER_V1,
app/accumulation_sl_target.py, app/accumulation_control.py, and contracts.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from accumulation_sl_target import compute_accumulation_sl_target, evaluate_accumulation_exit
from accumulation_contracts import validate_accumulation_manifest, ACCUMULATION_INPUT_CONTRACT
from accumulation_config import FUNDAMENTAL_FLOOR_CONFIG, STATE_THRESHOLDS
from accumulation_scanner import AccumulationScanner


def test_accumulation_sl_target_calculation():
    cmp = 100.0
    resistance = 105.0
    recent_swing_low = 94.0
    range_low = 93.0
    nearest_support = 92.0
    atr = 2.0
    high_52w = 120.0
    base_height = 10.0

    res = compute_accumulation_sl_target(
        cmp=cmp,
        resistance=resistance,
        recent_swing_low=recent_swing_low,
        range_low=range_low,
        nearest_support=nearest_support,
        atr=atr,
        high_52w=high_52w,
        base_height=base_height
    )

    # 1. Entry zone checks
    assert abs(res["breakout_level"] - 105.53) < 0.05
    assert abs(res["entry_zone_low"] - 98.5) < 0.05
    assert abs(res["entry_zone_high"] - 105.53) < 0.05

    # 2. Structural SL checks: max(94, 93, 92) - 0.5*2.0 = 94.0 - 1.0 = 93.0
    assert res["stop_loss"] == 93.0
    assert "STRUCTURAL_SUPPORT ₹94.00" in res["sl_reason"]

    # 3. Target 1 checks
    assert abs(res["target_1"] - 110.8) < 0.05

    # 4. Risk / Reward checks
    # Entry price = (98.5 + 105.53) / 2 = 102.015 -> ~102.02
    # Risk per share = 102.02 - 93.0 = 9.02
    # Reward 1 = 110.81 - 102.02 = 8.79
    # RR 1 = 8.79 / 9.02 = ~0.97 < 2.0 -> NOT Tradable
    assert res["rr_1"] < 2.0
    assert res["tradable"] is False
    assert "INSUFFICIENT_INITIAL_RR" in res["tradability_reason"]


def test_accumulation_sl_target_tradable_setup():
    cmp = 100.0
    resistance = 105.0
    recent_swing_low = 99.0
    range_low = 98.5
    nearest_support = 98.0
    atr = 1.0
    high_52w = 130.0
    base_height = 15.0

    res = compute_accumulation_sl_target(
        cmp=cmp,
        resistance=resistance,
        recent_swing_low=recent_swing_low,
        range_low=range_low,
        nearest_support=nearest_support,
        atr=atr,
        high_52w=high_52w,
        base_height=base_height
    )

    # Structural support = 99.0 - 0.5 = 98.5
    # Risk per share = ~102.02 - 98.5 = 3.52
    # Reward 1 = ~110.81 - 102.02 = 8.79 -> RR 1 = ~2.50 >= 2.0
    assert res["rr_1"] >= 2.0
    assert res["tradable"] is True
    assert res["tradability_reason"] == "PASSED_MIN_RR"


def test_accumulation_exit_evaluator():
    position = {
        "stop_loss": 90.0,
        "target_1": 110.0,
        "target_2": 120.0,
        "target_3": 130.0,
        "initial_accumulation_score": 80.0,
        "breakout_level": 105.0,
        "time_stop_days": 40
    }

    # 1. Stop loss hit
    exit_sl = evaluate_accumulation_exit(position, {"close": 89.0})
    assert exit_sl["exit_signal"] == "STOP_LOSS"
    assert exit_sl["should_exit"] is True

    # 2. Structural breakdown
    exit_struct = evaluate_accumulation_exit(position, {"close": 94.0, "range_support": 95.0})
    assert exit_struct["exit_signal"] == "STRUCTURE_INVALIDATED"
    assert exit_struct["should_exit"] is True

    # 3. Thesis collapse
    exit_score = evaluate_accumulation_exit(position, {"close": 100.0, "accumulation_score": 50.0})
    assert exit_score["exit_signal"] == "ACCUMULATION_INVALIDATED"
    assert exit_score["should_exit"] is True

    # 4. Target 1
    exit_t1 = evaluate_accumulation_exit(position, {"close": 111.0})
    assert exit_t1["exit_signal"] == "TARGET_1"
    assert exit_t1["should_exit"] is False

    # 5. Time stop
    exit_time = evaluate_accumulation_exit(position, {"close": 102.0, "days_held": 40})
    assert exit_time["exit_signal"] == "TIME_STOP"
    assert exit_time["should_exit"] is True


def test_accumulation_contracts_manifest():
    manifest = [
        {"name": field, "valid": True} for field in ACCUMULATION_INPUT_CONTRACT["REQUIRED"]
    ]
    is_valid, missing, stats = validate_accumulation_manifest(manifest)
    assert is_valid is True
    assert len(missing) == 0
    assert stats["missing_required"] == 0


def test_accumulation_scanner_evaluation():
    scanner = AccumulationScanner()
    
    # Generate synthetic 60-day bullish accumulation OHLCV data with tight compression
    dates = pd.date_range("2026-01-01", periods=60)
    close = np.full(60, 100.0)
    close[-20:] = np.linspace(104.5, 105.0, 20)  # Very tight 20D compression near high
    high = close + 0.2
    low = close - 0.2
    high.flags.writeable = True
    high[10] = 108.0  # Resistance at 108
    open_p = close - 0.05
    volume = np.linspace(50000, 250000, 60)

    df = pd.DataFrame({"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)

    fund_data = {
        "ROE": 18.0,
        "ROCE": 22.0,
        "DebtEquity": 0.2,
        "SalesGrowth": 15.0,
        "PATGrowth": 20.0
    }

    res = scanner.evaluate_symbol(
        symbol="TESTSTOCK",
        df=df,
        fund_data=fund_data,
        nifty_20d_ret=1.0,
        run_id="test_run_1"
    )

    if res["status"] != "QUALIFIED":
        print("EVALUATION RESULT:", res)

    assert res["status"] == "QUALIFIED"
    assert res["symbol"] == "TESTSTOCK"
    assert res["score"] >= STATE_THRESHOLDS["ACCUMULATION_WATCH"]
    assert "sl_target" in res
    assert "audit_snapshot_id" in res
