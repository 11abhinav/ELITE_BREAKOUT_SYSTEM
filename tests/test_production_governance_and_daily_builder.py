#!/usr/bin/env python3
"""
UNIT & INTEGRATION TEST: PRODUCTION GOVERNANCE & DAILY BUILDER 2.0
==================================================================
Validates:
1. Scanner States & Decommission Invariants (No Shadow, No Provisional).
2. Certified Three-Regime Routing Matrix enforcement.
3. Database Alert Insertion Gate (fails closed, rejects uncertified/decommissioned).
4. Automated Production Safety Assertions (§9).
5. Daily Builder 2.0 Fundamental Wealth Engine (5 scores, normalized earnings, value trap guard).
6. 26-column Master Table schema and 8 distinct Daily Watchlists.
"""

import os
import sys
import pytest
import pandas as pd

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.governance_registry import (
    DECOMMISSIONED_SCANNERS,
    UNDER_CERTIFICATION_SCANNERS,
    CERTIFIED_PRODUCTION_SCANNERS,
    REGIME_ROUTING_MATRIX,
    get_scanner_governance_state,
    check_production_alert_permission,
    validate_production_safety_assertions,
    normalize_scanner_name
)

from app.fundamental_wealth_engine import (
    fundamental_wealth_engine,
    CATEGORY_HISTORY_FILE
)


def test_governance_states_and_decommission_invariants():
    """Verify scanner states and ensure all 6 failed families are permanently silenced."""
    decommissioned_families = [
        "SHORT_COVERING",
        "5M_BREAKOUT",
        "MOMENTUM_IGNITION",
        "MULTI_TF",
        "TECHNICAL_INTRADAY",
        "REVERSAL"
    ]
    for s in decommissioned_families + ["EOD", "PULLBACK", "ACCUMULATION"]:
        assert get_scanner_governance_state(s) == "DECOMMISSIONED"
        is_allowed, reason = check_production_alert_permission(s, "BULL")
        assert not is_allowed
        assert "DECOMMISSIONED" in reason

    # TECHNICAL is strictly UNDER_CERTIFICATION (zero alerts pending administrative lock)
    assert get_scanner_governance_state("TECHNICAL") == "UNDER_CERTIFICATION"
    is_allowed, reason = check_production_alert_permission("TECHNICAL", "BULL")
    assert not is_allowed
    assert "UNDER_CERTIFICATION" in reason

    # Unknown scanner must fail closed
    with pytest.raises(ValueError, match="CRITICAL GOVERNANCE FAILURE"):
        get_scanner_governance_state("UNKNOWN_EXPERIMENTAL_SCANNER")


def test_three_regime_routing_matrix():
    """Verify TECHNICAL remains in UNDER_CERTIFICATION and discarded scanners remain DECOMMISSIONED."""
    # 1. TECHNICAL: Under certification in BULL, not certified in SIDEWAYS/BEAR (zero live alerts)
    assert check_production_alert_permission("TECHNICAL", "BULL")[0] is False
    assert "UNDER_CERTIFICATION" in check_production_alert_permission("TECHNICAL", "BULL")[1]
    assert check_production_alert_permission("TECHNICAL", "SIDEWAYS")[0] is False
    assert check_production_alert_permission("TECHNICAL", "BEAR")[0] is False

    # 2. PULLBACK, ACCUMULATION, EOD: Decommissioned across all regimes (zero alerts)
    for sc in ["PULLBACK", "ACCUMULATION", "EOD"]:
        for reg in ["BULL", "SIDEWAYS", "BEAR"]:
            is_perm, reason = check_production_alert_permission(sc, reg)
            assert is_perm is False
            assert "DECOMMISSIONED" in reason


def test_production_safety_assertions():
    """Verify automated production safety assertions pass for zero active production scanners."""
    for regime in ["BULL", "SIDEWAYS", "BEAR"]:
        active_scanners = validate_production_safety_assertions(regime)
        # In under_certification state, active scanners must be empty (ZERO live alerts)
        assert len(active_scanners) == 0
        for s in active_scanners:
            assert s in CERTIFIED_PRODUCTION_SCANNERS
            assert s not in DECOMMISSIONED_SCANNERS
            assert s not in UNDER_CERTIFICATION_SCANNERS


def test_database_governance_gate():
    """Test that app/database.py save_alert_if_new gate blocks unauthorized alerts."""
    from app.database import save_alert_if_new

    # 1. Decommissioned scanner attempt (SHORT_COVERING, EOD, PULLBACK)
    for sc in ["SHORT_COVERING", "EOD", "PULLBACK"]:
        inserted, reason, alloc, shares = save_alert_if_new(
            symbol="TESTSYM",
            breakout_type="BREAKOUT",
            alert_time="2026-09-26 10:00:00",
            scanner=sc,
            bayesian_regime="BULL",
            entry_price=100.0,
            stop_loss=95.0
        )
        assert inserted is False
        assert "DECOMMISSIONED" in reason

    # 2. Under certification scanner attempt (TECHNICAL)
    inserted, reason, alloc, shares = save_alert_if_new(
        symbol="TESTSYM",
        breakout_type="BREAKOUT",
        alert_time="2026-09-26 10:00:00",
        scanner="TECHNICAL",
        bayesian_regime="BULL",
        entry_price=100.0,
        stop_loss=95.0
    )
    assert inserted is False
    assert "UNDER_CERTIFICATION" in reason


def test_fundamental_wealth_engine_scoring_and_normalized_earnings():
    """Test 5 independent scores, normalized earnings value, and fair value calculation."""
    fund_data = {
        "roe": 22.5,
        "roce": 24.0,
        "debt_equity": 0.15,
        "pe": 16.0,
        "pb": 2.2,
        "peg": 0.8,
        "revenue_cagr_3y": 20.0,
        "eps_cagr_3y": 22.0,
        "fcf": 1.0,
        "market_cap": 25000e7,
        "op_margin": 22.0,
        "net_margin": 15.0,
        "interest_coverage": 12.0
    }
    tech_data = {
        "technical_state": "PULLBACK_SETUP",
        "technical_source": "PULLBACK"
    }

    # In SIDEWAYS regime, PULLBACK is certified!
    rec = fundamental_wealth_engine.build_master_record(
        symbol="TITAN_TEST",
        company="Titan Company Ltd",
        macro_regime="SIDEWAYS",
        cmp=3200.0,
        fund_data=fund_data,
        tech_data=tech_data
    )

    # Verify all 26 required columns exist
    required_cols = [
        "symbol", "company", "macro_regime", "technical_state", "technical_source",
        "quality_score", "growth_score", "valuation_score", "wealth_score", "risk_score",
        "fundamental_category", "valuation_category", "current_price", "normalized_earnings",
        "fair_value_range", "valuation_discount", "earnings_growth", "FCF_yield",
        "ROCE", "ROE", "debt", "regime_permission", "production_alert_permission",
        "first_seen_category", "current_category", "category_history"
    ]
    for c in required_cols:
        assert c in rec, f"Missing required column: {c}"

    assert rec["quality_score"] >= 80.0
    assert rec["growth_score"] >= 70.0
    assert rec["fundamental_category"] in ("QUALITY_COMPOUNDER", "EARNINGS_BARGAIN", "QUALITY_VALUE")
    assert rec["production_alert_permission"] is False  # Zero alerts permitted while UNDER_CERTIFICATION


def test_value_trap_protection():
    """Verify that deteriorating companies are strictly blocked from investment categories."""
    trap_fund = {
        "roe": 2.1,
        "roce": 3.0,
        "debt_equity": 2.8,  # Highly leveraged
        "pe": 8.0,           # Appears optically cheap
        "pb": 0.8,
        "revenue_cagr_3y": -12.0,
        "eps_cagr_3y": -20.0,
        "fcf": -1.0,
        "interest_coverage": 0.8
    }
    tech_data = {"technical_state": "NONE", "technical_source": "NONE"}

    rec = fundamental_wealth_engine.build_master_record(
        symbol="TRAP_CO",
        company="Deteriorating Value Trap Ltd",
        macro_regime="BULL",
        cmp=50.0,
        fund_data=trap_fund,
        tech_data=tech_data
    )

    assert rec["fundamental_category"] == "VALUE_TRAP"
    assert rec["production_alert_permission"] is False

    # Ensure it enters VALUE_TRAP watchlist and is excluded from quality/value watchlists
    watchlists = fundamental_wealth_engine.generate_daily_watchlists([rec])
    assert len(watchlists["VALUE_TRAP"]) == 1
    assert len(watchlists["QUALITY_VALUE"]) == 0
    assert len(watchlists["QUALITY_COMPOUNDERS"]) == 0
    assert len(watchlists["BEAR_VALUE"]) == 0


def test_bear_value_engine_and_watchlists_generation():
    """Verify dedicated Bear Value Opportunities and 8 distinct watchlists."""
    healthy_bear_candidate = {
        "symbol": "BEAR_OPP_1",
        "company": "Resilient Value Corp",
        "macro_regime": "BEAR",
        "technical_state": "NONE",
        "technical_source": "NONE",
        "quality_score": 75.0,
        "growth_score": 68.0,
        "valuation_score": 72.0,
        "wealth_score": 72.0,
        "risk_score": 25.0,
        "fundamental_category": "QUALITY_VALUE",
        "valuation_category": "UNDERVALUED",
        "current_price": 500.0,
        "normalized_earnings": 35.0,
        "fair_value_range": "600.0 - 750.0",
        "valuation_discount": 25.0,
        "earnings_growth": 14.0,
        "FCF_yield": 4.5,
        "ROCE": 22.0,
        "ROE": 20.0,
        "debt": 0.2,
        "regime_permission": "BEAR_VALUE_FOCUSED",
        "production_alert_permission": False,
        "first_seen_category": "QUALITY_VALUE",
        "current_category": "QUALITY_VALUE",
        "category_history": "QUALITY_VALUE"
    }

    watchlists = fundamental_wealth_engine.generate_daily_watchlists([healthy_bear_candidate])
    assert len(watchlists["BEAR_VALUE"]) == 1
    assert watchlists["BEAR_VALUE"][0]["bear_value_archetype"] == "HIGH_QUALITY + CHEAP"
    assert len(watchlists["QUALITY_VALUE"]) == 1

    # Test saving master outputs
    saved_meta = fundamental_wealth_engine.save_master_builder_outputs([healthy_bear_candidate])
    assert saved_meta["master_count"] == 1
    assert os.path.exists("data/daily_builder_master_v2.parquet")
    assert os.path.exists("data/daily_builder_master_v2.csv")
    assert os.path.exists("data/watchlists/BEAR_VALUE.parquet")
    assert os.path.exists("data/watchlists/QUALITY_VALUE.parquet")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
