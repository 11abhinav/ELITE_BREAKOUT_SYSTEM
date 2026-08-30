"""
Unit Tests for NextFixDecisionEngine & Automated Governance Eligibility Triggers.
"""

import pytest
import os
import pandas as pd
from engine.analytics.next_fix_decision_engine import NextFixDecisionEngine


def test_next_fix_decision_engine_initialization():
    engine = NextFixDecisionEngine()
    evals = engine.evaluate_all_scanners()
    
    assert len(evals) == 7
    scanners = [e["scanner"] for e in evals]
    assert "PULLBACK" in scanners
    assert "MULTIBAGGER" in scanners
    assert "WEALTH_ENGINE" in scanners
    assert "EOD" in scanners
    assert "DAILY_BUILDER" in scanners
    assert "MULTI_TF" in scanners
    assert "REVERSAL" in scanners


def test_zero_candidate_eligibility_under_frozen_baseline():
    """Verifies that with current sample sizes, zero scanners are marked as eligible modification candidates."""
    engine = NextFixDecisionEngine()
    evals = engine.evaluate_all_scanners()
    
    eligible = [e for e in evals if "🎯 FIRST ELIGIBLE" in e["eligibility"]]
    assert len(eligible) == 0, f"Expected 0 eligible candidates under current freeze, got {eligible}"


def test_dashboard_report_generation():
    engine = NextFixDecisionEngine()
    report = engine.generate_dashboard()
    
    assert "No scanner is currently eligible for modification" in report
    assert "v5.1.2 (FROZEN)" in report
    assert "PULLBACK" in report
    assert "REVERSAL" in report
