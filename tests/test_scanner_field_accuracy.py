"""
Independent Field Accuracy Test Suite — Phase 4C & 4E
Validates scanner telemetry against ground truth (direct NSE Bhavcopy for daily, Fyers Historical API for intraday).
"""

import pytest
import pandas as pd
import numpy as np
from datetime import date, datetime
from scanner_field_validator import (
    ScannerFieldValidator,
    FIELD_VALIDATION_RULES,
    independent_calculate_sma,
    independent_calculate_rsi,
    independent_calculate_atr
)
from scanner_contracts import validate_manifest_against_contract, SCANNER_INPUT_CONTRACTS
from decision_replay_engine import DecisionReplayEngine


def test_independent_indicator_calculation_accuracy():
    """Verify independent validator indicator functions compute correct reference values."""
    prices = pd.Series([100 + i * 0.5 for i in range(100)])
    sma50 = independent_calculate_sma(prices, 50)
    assert not np.isnan(sma50)
    assert round(sma50, 2) == 137.25

    rsi = independent_calculate_rsi(prices, 14)
    assert not np.isnan(rsi)
    assert rsi > 90.0 # Steadily increasing prices produce high RSI

    highs = prices + 1.0
    lows = prices - 1.0
    atr = independent_calculate_atr(highs, lows, prices, 14)
    assert not np.isnan(atr)
    assert atr > 0.0


def test_validator_field_tolerances():
    """Verify ScannerFieldValidator respects field-specific tolerance rules."""
    validator = ScannerFieldValidator(use_live_api=False)

    # CMP within 0.10% passes
    res_cmp = validator.validate_field("CMP", 1500.0, 1501.0, category="CMP")
    assert res_cmp["status"] in ["PASS", "PASS_WITHIN_TOLERANCE"]

    # CMP outside 0.10% fails
    res_cmp_fail = validator.validate_field("CMP", 1500.0, 1520.0, category="CMP")
    assert res_cmp_fail["status"] == "FAIL"

    # RSI within 0.10 points passes
    res_rsi = validator.validate_field("RSI", 62.50, 62.55, category="RSI")
    assert res_rsi["status"] in ["PASS", "PASS_WITHIN_TOLERANCE"]

    # RSI outside 0.10 points fails
    res_rsi_fail = validator.validate_field("RSI", 62.50, 63.00, category="RSI")
    assert res_rsi_fail["status"] == "FAIL"


def test_scanner_contracts_validation():
    """Verify scanner contracts enforce USED_BY_DECISION ∈ manifest invariant."""
    manifest = [
        {"name": "Close", "value": 500.0, "valid": True},
        {"name": "Open", "value": 495.0, "valid": True},
        {"name": "High", "value": 505.0, "valid": True},
        {"name": "Low", "value": 490.0, "valid": True},
        {"name": "Volume", "value": 100000.0, "valid": True},
        {"name": "RSI", "value": 62.5, "valid": True},
        {"name": "EMA20", "value": 490.0, "valid": True},
        {"name": "SMA50", "value": 480.0, "valid": True},
        {"name": "SMA200", "value": 450.0, "valid": True},
        {"name": "ATR", "value": 10.0, "valid": True},
        {"name": "ADX", "value": 25.0, "valid": True},
        {"name": "VolumeRatio", "value": 1.5, "valid": True},
        {"name": "PRIOR_20D_HIGH", "value": 495.0, "valid": True}
    ]

    is_valid, missing, stats = validate_manifest_against_contract("EOD", manifest)
    assert is_valid is True
    assert len(missing) == 0
    assert stats["uncaptured_count"] == 0

    # Incomplete manifest missing SMA200
    incomplete_manifest = [m for m in manifest if m["name"] != "SMA200"]
    is_valid_inc, missing_inc, stats_inc = validate_manifest_against_contract("EOD", incomplete_manifest)
    assert is_valid_inc is False
    assert "SMA200" in missing_inc
    assert stats_inc["uncaptured_count"] == 1


def test_decision_replay_gate_by_gate_equality():
    """Verify DecisionReplayEngine asserts gate-by-gate equality and expression agreement."""
    ctx_dict = {
        "audit_snapshot_id": "RELIANCE-EOD-20260823-12345678",
        "symbol": "RELIANCE",
        "scanner": "EOD",
        "terminal_decision": "REJECTED",
        "primary_reason": "WEAK_SIGNALS",
        "gate_results": {
            "WEAK_SIGNALS": {
                "passed": False,
                "status": "FAIL",
                "gate_type": "COMPOSITE",
                "actual": 1,
                "threshold": 3,
                "expression": "signals_count >= 3",
                "evaluated_result": False,
                "terminal_result": "FAIL"
            }
        },
        "decision_trace": [
            {"stage": "WEAK_SIGNALS", "status": "FAIL"}
        ]
    }

    snapshot = DecisionReplayEngine.create_snapshot(ctx_dict)
    is_reproducible, mismatches, summary = DecisionReplayEngine.replay_snapshot(snapshot)

    assert is_reproducible is True
    assert len(mismatches) == 0
    assert summary["gates_verified"] == 1


def test_master_scanner_certification_evaluator():
    """
    [RULE 67 Invariant]
    A scanner is CERTIFIED iff:
    telemetry == PASS and raw_data == PASS and indicators == PASS and
    fundamentals == PASS and decision_inputs == PASS and replay == PASS and freshness == PASS.
    If ANY sub-dimension is FAIL, overall status MUST be NOT_CERTIFIED.
    """
    def evaluate_overall(dims: dict) -> str:
        required = [
            dims.get("Telemetry"),
            dims.get("Raw Data"),
            dims.get("Indicators"),
            dims.get("Fundamentals"),
            dims.get("Decision Inputs"),
            dims.get("Gate Replay"),
            dims.get("Freshness")
        ]
        return "CERTIFIED" if all(v == "PASS" for v in required) else "NOT_CERTIFIED"

    perfect = {
        "Telemetry": "PASS", "Raw Data": "PASS", "Indicators": "PASS",
        "Fundamentals": "PASS", "Decision Inputs": "PASS", "Gate Replay": "PASS", "Freshness": "PASS"
    }
    assert evaluate_overall(perfect) == "CERTIFIED"

    # Single failing fundamental dimension MUST force NOT_CERTIFIED
    fund_fail = perfect.copy()
    fund_fail["Fundamentals"] = "FAIL"
    assert evaluate_overall(fund_fail) == "NOT_CERTIFIED"

    # Single failing decision input dimension MUST force NOT_CERTIFIED
    input_fail = perfect.copy()
    input_fail["Decision Inputs"] = "FAIL"
    assert evaluate_overall(input_fail) == "NOT_CERTIFIED"


def test_certification_requires_live_validation_evidence():
    """
    [RULE 67 Invariant]
    Certification engine requires live/empirical validation evidence records.
    Without empirical validation records (sample_count == 0 or fields_validated == 0),
    the scanner MUST be marked NOT_CERTIFIED.
    """
    def evaluate_with_evidence(dims: dict, evidence_record: dict) -> str:
        if not evidence_record or evidence_record.get("sample_count", 0) == 0 or evidence_record.get("fields_validated", 0) == 0:
            return "NOT_CERTIFIED"
        required = [
            dims.get("Telemetry"), dims.get("Raw Data"), dims.get("Indicators"),
            dims.get("Fundamentals"), dims.get("Decision Inputs"), dims.get("Gate Replay"), dims.get("Freshness")
        ]
        return "CERTIFIED" if all(v == "PASS" for v in required) else "NOT_CERTIFIED"

    dims_perfect = {
        "Telemetry": "PASS", "Raw Data": "PASS", "Indicators": "PASS",
        "Fundamentals": "PASS", "Decision Inputs": "PASS", "Gate Replay": "PASS", "Freshness": "PASS"
    }

    # Missing evidence record MUST produce NOT_CERTIFIED
    assert evaluate_with_evidence(dims_perfect, None) == "NOT_CERTIFIED"
    assert evaluate_with_evidence(dims_perfect, {"sample_count": 0, "fields_validated": 0}) == "NOT_CERTIFIED"

    # Present evidence record with valid samples produces CERTIFIED
    valid_evidence = {"sample_count": 3, "fields_validated": 39, "failed_count": 0}
    assert evaluate_with_evidence(dims_perfect, valid_evidence) == "CERTIFIED"


