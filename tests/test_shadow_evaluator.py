"""
Unit tests for engine/analytics/shadow_evaluator.py.
Verifies state invariance (zero side effects), asymmetric hypothesis evaluation,
and granular schema compliance.
"""

import pytest
import copy
from engine.analytics.shadow_evaluator import ShadowEvaluator


def test_shadow_evaluator_state_immutability():
    evaluator = ShadowEvaluator()
    sample_record = {
        "evaluation_id": "eval_123",
        "scanner": "EOD",
        "symbol": "TCS",
        "decision_timestamp": "2026-08-20 10:15:00 IST",
        "terminal_decision": "PASS",
        "entry_price": 3500.0,
        "sl_price": 3400.0,
        "target_price": 3700.0,
        "sector_status": "HEADWIND",
        "volume_ratio": 1.2,
        "macro_state": "BEAR",
        "macro_drop_pct": 0.65,
        "cf_realized_r": -1.0,
        "cf_mfe_r": 0.2,
        "cf_mae_r": 1.0,
        "trade_eligibility_status": "ELIGIBLE"
    }

    record_copy = copy.deepcopy(sample_record)

    telemetry = evaluator.evaluate_shadow_signal(
        sample_record,
        headwind_action="BLOCK",
        volume_threshold=1.5,
        macro_action="SIZE_REDUCE_50"
    )

    # Assert input was not mutated
    assert sample_record == record_copy

    # Assert shadow decisions and divergence
    assert telemetry["production_decision"] == "PASS"
    assert telemetry["shadow_action"] == "BLOCK"
    assert "RULE_SECTOR_HEADWIND" in telemetry["shadow_rules_triggered"]
    assert "RULE_LOW_VOLUME_RATIO_1.5x" in telemetry["shadow_rules_triggered"]
    assert "RULE_MACRO_DROP_GT_0.5PCT" in telemetry["shadow_rules_triggered"]
    assert telemetry["cluster_id"] == "TCS_2026-08-20"
    assert telemetry["net_trade_R"] == -1.05


def test_shadow_macro_sizing():
    evaluator = ShadowEvaluator()
    sample_record = {
        "scanner": "EOD",
        "symbol": "INFY",
        "decision_timestamp": "2026-08-20 11:30:00 IST",
        "terminal_decision": "PASS",
        "entry_price": 1800.0,
        "sl_price": 1750.0,
        "target_price": 1900.0,
        "sector_status": "TAILWIND",
        "volume_ratio": 2.2,
        "macro_drop_pct": 0.75,
        "cf_realized_r": 2.0,
        "trade_eligibility_status": "ELIGIBLE"
    }

    telemetry = evaluator.evaluate_shadow_signal(
        sample_record,
        headwind_action="BLOCK",
        volume_threshold=1.5,
        macro_action="SIZE_REDUCE_50",
        friction_r=0.05
    )

    assert telemetry["shadow_action"] == "SIZE_REDUCE_50"
    assert telemetry["shadow_position_size_multiplier"] == 0.5
    assert telemetry["gross_trade_R"] == 2.0
    assert telemetry["net_trade_R"] == 1.95
    assert telemetry["portfolio_weighted_R"] == pytest.approx(0.975, rel=1e-3)
