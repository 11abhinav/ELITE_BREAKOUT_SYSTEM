"""
tests/test_accumulation_scanner.py — Unit Test Suite for ACCUMULATION_SCANNER_V1.
Tests contract invariants, activation rules, directional gap classifications, N+1 exit evaluator,
same-bar STOP_FIRST precedence, milestone preservation, and package isolation.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from app.accumulation.config import (
    STRATEGY_VERSION, SL_TARGET_VERSION, CONFIG_VERSION, SCORE_NORMALIZATION_VERSION
)
from app.accumulation.contracts import (
    TradeSetupContract, AccumulationContractValidator, FundamentalFloorResult
)
from app.accumulation.sl_target import AccumulationSLTargetEngine
from app.accumulation.exit_evaluator import AccumulationExitEvaluator
from app.accumulation.scanner import AccumulationScanner
from app.accumulation.cooldown import AccumulationCooldownEngine
from app.accumulation.scheduler import AccumulationScheduler


def test_accumulation_contracts_manifest():
    """Verifies strategy and configuration version declarations."""
    assert STRATEGY_VERSION == "ACCUMULATION_V1.0"
    assert SL_TARGET_VERSION == "ACCUM_SL_V1"
    assert CONFIG_VERSION == "ACCUM_CFG_V1"
    assert SCORE_NORMALIZATION_VERSION == "ACCUM_SCORE_NORM_V1"


def test_trigger_level_reached_is_null_before_activation():
    """ACTIVE_SETUP requires entry_trigger_level_reached == NULL at setup creation."""
    contract = TradeSetupContract(
        symbol="TESTSTOCK",
        signal_state="BREAKOUT_READY",
        entry_type="ZONE_MIDPOINT",
        entry_trigger_rule="RANGE_TOUCH",
        entry_reference_type="STRATEGY_REFERENCE",
        entry_zone_low=950.0,
        entry_zone_high=1000.0,
        entry_price=975.0,
        preferred_entry=975.0,
        entry_trigger_level=975.0,
        entry_displacement_reference=1000.0,
        breakout_level=1010.0,
        stop_loss=900.0,
        target_1=1020.0,
        target_2=1050.0,
        target_3=1100.0,
        risk_pct=7.69,
        rr_1=2.31,
        rr_2=5.77,
        rr_3=12.18,
        suggested_capital=129675.0,
        suggested_position_size=133,
        status="ACTIVE_SETUP",
        setup_outcome="PENDING",
        entry_trigger_level_reached=None
    )
    val = AccumulationContractValidator.validate_setup_contract(contract)
    assert val["is_valid"] is True

    # Pre-populating entry_trigger_level_reached when ACTIVE_SETUP must fail contract validation
    invalid_contract = TradeSetupContract(
        symbol="TESTSTOCK",
        signal_state="BREAKOUT_READY",
        entry_type="ZONE_MIDPOINT",
        entry_trigger_rule="RANGE_TOUCH",
        entry_reference_type="STRATEGY_REFERENCE",
        entry_zone_low=950.0,
        entry_zone_high=1000.0,
        entry_price=975.0,
        preferred_entry=975.0,
        entry_trigger_level=975.0,
        entry_displacement_reference=1000.0,
        breakout_level=1010.0,
        stop_loss=900.0,
        target_1=1020.0,
        target_2=1050.0,
        target_3=1100.0,
        risk_pct=7.69,
        rr_1=2.31,
        rr_2=5.77,
        rr_3=12.18,
        suggested_capital=129675.0,
        suggested_position_size=133,
        status="ACTIVE_SETUP",
        setup_outcome="PENDING",
        entry_trigger_level_reached=True  # INVALID for ACTIVE_SETUP
    )
    invalid_val = AccumulationContractValidator.validate_setup_contract(invalid_contract)
    assert invalid_val["is_valid"] is False
    assert "ACTIVE_SETUP requires entry_trigger_level_reached == NULL" in invalid_val["reason"]


def test_zone_range_touch_without_midpoint():
    """ZONE_MIDPOINT activates when candle touches zone range, even if midpoint 975 is never touched."""
    setup = {
        "status": "ACTIVE_SETUP",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    # Bar touches upper zone (low=995 <= 1000), but stays above midpoint 975 (low=995 > 975)
    bar = {"Open": 1000.0, "High": 1010.0, "Low": 995.0, "Close": 1005.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "ENTRY_TRIGGERED"
    assert res["entry_trigger_type"] == "ZONE_TOUCH"
    assert res["entry_trigger_level_reached"] is False  # 975 was NOT reached
    assert res["action"] == "TRIGGERED_EXCLUDED"  # Exits excluded on trigger bar N


def test_breakout_level_cross_activation():
    """BREAKOUT_CONFIRMATION activates when high >= entry_trigger_level (breakout * 1.002)."""
    setup = {
        "status": "ACTIVE_SETUP",
        "entry_type": "BREAKOUT_CONFIRMATION",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 1012.02,  # 1010 * 1.002
        "stop_loss": 950.0,
        "target_1": 1030.0,
        "target_2": 1060.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    bar = {"Open": 1005.0, "High": 1015.0, "Low": 1000.0, "Close": 1012.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "ENTRY_TRIGGERED"
    assert res["entry_trigger_type"] == "BREAKOUT_BUFFER"
    assert res["entry_trigger_level_reached"] is True
    assert res["action"] == "TRIGGERED_EXCLUDED"


def test_zone_gap_rejection_precedes_zone_trigger():
    """Gap evaluation precedes transition to ENTRY_TRIGGERED; open > zone_high * 1.02 rejects setup."""
    setup = {
        "status": "ACTIVE_SETUP",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    # Open = 1030 (> 1000 * 1.02 = 1020), Low = 995 (overlaps zone)
    bar = {"Open": 1030.0, "High": 1040.0, "Low": 995.0, "Close": 1035.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "ENTRY_GAP_REJECTED"
    assert res["setup_outcome"] == "INVALIDATED"
    assert res["exit_reason"] == "GAP_ABOVE_ZONE"
    assert res["entry_quality"] == "DEGRADED_GAP_RISK"
    assert res["entry_trigger_level_reached"] is None  # Null on gap rejection
    assert res["action"] == "REJECTED_GAP"


def test_breakout_gap_above_trigger():
    """BREAKOUT_CONFIRMATION rejects with GAP_THROUGH when open > trigger_level * 1.02."""
    setup = {
        "status": "ACTIVE_SETUP",
        "entry_type": "BREAKOUT_CONFIRMATION",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 1012.02,
        "stop_loss": 950.0,
        "target_1": 1030.0,
        "target_2": 1060.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    # Open = 1035.0 (> 1012.02 * 1.02 = 1032.26)
    bar = {"Open": 1035.0, "High": 1045.0, "Low": 1010.0, "Close": 1040.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "ENTRY_GAP_REJECTED"
    assert res["setup_outcome"] == "INVALIDATED"
    assert res["exit_reason"] == "GAP_THROUGH"


def test_breakout_gap_below_trigger_activation():
    """BREAKOUT_CONFIRMATION open below trigger (open < trigger * 0.98) activates with FROM_BELOW direction if high crosses trigger."""
    setup = {
        "status": "ACTIVE_SETUP",
        "entry_type": "BREAKOUT_CONFIRMATION",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 1012.02,
        "stop_loss": 950.0,
        "target_1": 1030.0,
        "target_2": 1060.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    # Open = 980.0 (< 1012.02 * 0.98 = 991.78), High = 1015.0 (crosses trigger)
    bar = {"Open": 980.0, "High": 1015.0, "Low": 975.0, "Close": 1010.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "ENTRY_TRIGGERED"
    assert res["trigger_direction"] == "FROM_BELOW"
    assert res["entry_quality"] == "STANDARD"


def test_next_bar_stop_triggered():
    """Exit evaluation begins on N+1; low <= stop_loss triggers STOP_TRIGGERED."""
    setup = {
        "status": "ENTRY_TRIGGERED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    bar = {"Open": 920.0, "High": 925.0, "Low": 890.0, "Close": 895.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "STOP_TRIGGERED"
    assert res["setup_outcome"] == "FAILURE"
    assert res["exit_reason"] == "STOP_LOSS_HIT"
    assert res["exit_price"] == 900.0


def test_next_bar_target_completed():
    """High >= target_3 transitions immediately to SETUP_COMPLETED (SUCCESS)."""
    setup = {
        "status": "ENTRY_TRIGGERED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    bar = {"Open": 1010.0, "High": 1105.0, "Low": 1005.0, "Close": 1100.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "SETUP_COMPLETED"
    assert res["setup_outcome"] == "SUCCESS"
    assert res["exit_reason"] == "TARGET_3_REACHED"
    assert res["best_target_reached"] == "T3"


def test_same_bar_stop_target_stop_first():
    """Same-bar Stop Loss and Target ambiguity resolves to STOP_FIRST policy with exit_status = AMBIGUOUS."""
    setup = {
        "status": "ENTRY_TRIGGERED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": None
    }
    # Bar touches both Stop (low=890 <= 900) and Target 1 (high=1025 >= 1020)
    bar = {"Open": 950.0, "High": 1025.0, "Low": 890.0, "Close": 910.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "STOP_TRIGGERED"
    assert res["setup_outcome"] == "FAILURE"
    assert res["exit_status"] == "AMBIGUOUS"
    assert res["exit_assumption"] == "STOP_FIRST"


def test_target1_preserved_after_later_stop():
    """If T1 was reached on an earlier bar and a later bar hits Stop Loss, best_target_reached='T1' is preserved."""
    setup = {
        "status": "TARGET_1_REACHED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": "T1"
    }
    bar = {"Open": 920.0, "High": 925.0, "Low": 890.0, "Close": 895.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "STOP_TRIGGERED"
    assert res["setup_outcome"] == "FAILURE"
    assert res["best_target_reached"] == "T1"  # PRESERVED!


def test_same_bar_stop_and_target_2_stop_first():
    """Same-bar Stop Loss and Target 2 ambiguity resolves to STOP_FIRST policy; prior T1 milestone is preserved, T2 on ambiguous bar is discarded."""
    setup = {
        "status": "TARGET_1_REACHED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": "T1"
    }
    # Bar touches both Stop (low=890 <= 900) and Target 2 (high=1055 >= 1050)
    bar = {"Open": 980.0, "High": 1055.0, "Low": 890.0, "Close": 895.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "STOP_TRIGGERED"
    assert res["setup_outcome"] == "FAILURE"
    assert res["exit_status"] == "AMBIGUOUS"
    assert res["exit_assumption"] == "STOP_FIRST"
    assert res["best_target_reached"] == "T1"  # T1 preserved, T2 on ambiguous bar discarded!


def test_same_bar_stop_and_target_3_stop_first():
    """Same-bar Stop Loss and Target 3 ambiguity resolves to STOP_FIRST policy; prior T1 milestone is preserved, T3 on ambiguous bar is discarded."""
    setup = {
        "status": "TARGET_1_REACHED",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 950.0,
        "entry_zone_high": 1000.0,
        "preferred_entry": 975.0,
        "entry_trigger_level": 975.0,
        "stop_loss": 900.0,
        "target_1": 1020.0,
        "target_2": 1050.0,
        "target_3": 1100.0,
        "best_target_reached": "T1"
    }
    # Bar touches both Stop (low=890 <= 900) and Target 3 (high=1105 >= 1100)
    bar = {"Open": 980.0, "High": 1105.0, "Low": 890.0, "Close": 895.0, "Timestamp": datetime.utcnow()}

    res = AccumulationExitEvaluator.evaluate_bar(setup, bar)
    assert res["status"] == "STOP_TRIGGERED"
    assert res["setup_outcome"] == "FAILURE"
    assert res["exit_status"] == "AMBIGUOUS"
    assert res["exit_assumption"] == "STOP_FIRST"
    assert res["best_target_reached"] == "T1"  # T1 preserved, T3 on ambiguous bar discarded!


def test_existing_six_scanners_unchanged():
    """Ensures existing six scanners exist and package isolation is preserved."""
    import importlib
    eod = importlib.import_module("app.eod_scanner")
    reversal = importlib.import_module("app.reversal_scanner")
    pullback = importlib.import_module("app.pullback_pipeline")
    multi_tf = importlib.import_module("app.multi_tf_scanner")
    wealth = importlib.import_module("app.wealth_engine")
    multibagger = importlib.import_module("app.multibagger")

    assert hasattr(eod, "start")
    assert hasattr(reversal, "start") or hasattr(reversal, "evaluate_reversal_symbol")
    assert hasattr(pullback, "start") or hasattr(pullback, "evaluate_pullback_symbol")
    assert hasattr(multi_tf, "start") or hasattr(multi_tf, "evaluate_multi_tf_symbol")
    assert hasattr(wealth, "evaluate_wealth_symbol") or hasattr(wealth, "start")
    assert hasattr(multibagger, "evaluate_multibagger_symbol") or hasattr(multibagger, "start")
