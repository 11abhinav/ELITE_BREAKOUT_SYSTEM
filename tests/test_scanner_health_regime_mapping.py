import pytest
from engine.production.governance_registry import (
    get_scanner_health_regime_info,
    CERTIFIED_PRODUCTION_SCANNERS,
    UNDER_CERTIFICATION_SCANNERS,
    DECOMMISSIONED_SCANNERS,
    can_scanner_emit_production_alert,
    assert_production_alert_permitted,
    REGIME_ROUTING_MATRIX
)
from app.database import save_alert_if_new

def test_technical_health_regime_mapping():
    info_bull = get_scanner_health_regime_info("TECHNICAL", current_macro_regime="BULL")
    assert info_bull["supported_regime"] == "BULL"
    assert info_bull["production_authorization_state"] == "LOCK REVIEW PENDING"
    assert info_bull["is_production_active"] is False
    assert info_bull["production_active_now"] == "NO"
    assert "Evidence-supported regime: BULL" in info_bull["display_message"]
    assert "Current production authorization: LOCK REVIEW PENDING" in info_bull["display_message"]
    assert "No live alert unless production governance is unlocked." in info_bull["display_message"]

    info_side = get_scanner_health_regime_info("TECHNICAL", current_macro_regime="SIDEWAYS")
    assert info_side["display_message"] == "Suppressed: TECHNICAL evidence supports BULL only; current regime is SIDEWAYS."
    assert info_side["is_production_active"] is False

    info_bear = get_scanner_health_regime_info("TECHNICAL", current_macro_regime="BEAR")
    assert info_bear["display_message"] == "Suppressed: TECHNICAL evidence supports BULL only; current regime is BEAR."
    assert info_bear["is_production_active"] is False


def test_pullback_health_regime_mapping():
    info_side = get_scanner_health_regime_info("PULLBACK", current_macro_regime="SIDEWAYS")
    assert info_side["supported_regime"] == "SIDEWAYS"
    assert info_side["is_production_active"] is False
    assert info_side["production_active_now"] == "NO"
    assert info_side["production_authorization_state"] == "DO NOT PERMANENTLY LOCK / CERTIFICATION PENDING"
    assert "Warning: 2025–26 temporal edge compression" in info_side["warning"]
    assert info_side["display_message"] == "Evidence-supported regime: SIDEWAYS. Production lock is not authorized because of recent 2025–26 edge compression."

    info_bull = get_scanner_health_regime_info("PULLBACK", current_macro_regime="BULL")
    assert info_bull["display_message"] == "Suppressed: PULLBACK evidence supports SIDEWAYS only."

    info_bear = get_scanner_health_regime_info("PULLBACK", current_macro_regime="BEAR")
    assert info_bear["display_message"] == "Suppressed: PULLBACK evidence supports SIDEWAYS only."


def test_accumulation_health_regime_mapping():
    info_bear = get_scanner_health_regime_info("ACCUMULATION", current_macro_regime="BEAR")
    assert info_bear["supported_regime"] == "BEAR"
    assert info_bear["is_production_active"] is False
    assert info_bear["production_active_now"] == "NO"
    assert info_bear["warning"] == "Warning: Q1 seasonal weakness"
    assert "Evidence-supported regime: BEAR" in info_bear["display_message"]
    assert "Q1 seasonal weakness noted" in info_bear["display_message"]
    assert "Production authorization remains subject to final certification" in info_bear["display_message"]

    info_bull = get_scanner_health_regime_info("ACCUMULATION", current_macro_regime="BULL")
    assert info_bull["display_message"] == "Suppressed: ACCUMULATION evidence supports BEAR only; current regime is BULL."

    info_side = get_scanner_health_regime_info("ACCUMULATION", current_macro_regime="SIDEWAYS")
    assert info_side["display_message"] == "Suppressed: ACCUMULATION evidence supports BEAR only; current regime is SIDEWAYS."


def test_eod_health_regime_mapping():
    for r in ["BULL", "SIDEWAYS", "BEAR"]:
        info = get_scanner_health_regime_info("EOD", current_macro_regime=r)
        assert info["supported_regime"] == "NONE"
        assert info["lifecycle_state"] == "UNDER_CERTIFICATION"
        assert info["production_authorization_state"] == "UNDERPOWERED / UNDER CERTIFICATION"
        assert info["display_message"] == "Suppressed: EOD remains underpowered/under certification; no live production alerts."
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"


def test_core_invariant_supported_not_equal_production_active():
    for sc in ["TECHNICAL", "PULLBACK", "ACCUMULATION", "EOD"]:
        info = get_scanner_health_regime_info(sc)
        assert info["supported_regime"] != info["current_production_active_regime"]
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"
        assert sc in UNDER_CERTIFICATION_SCANNERS
        assert sc not in CERTIFIED_PRODUCTION_SCANNERS


def test_fail_closed_empty_production_set():
    assert len(CERTIFIED_PRODUCTION_SCANNERS) == 0


def test_decommissioned_scanners_silence():
    for sc in DECOMMISSIONED_SCANNERS:
        info = get_scanner_health_regime_info(sc)
        assert info["lifecycle_state"] == "DECOMMISSIONED"
        assert info["display_message"] == "Decommissioned — permanently silenced."
        assert info["is_production_active"] is False
        assert can_scanner_emit_production_alert(sc) is False
        with pytest.raises(PermissionError):
            assert_production_alert_permitted(sc)


def test_persistence_gate_blocks_under_certification_and_decommissioned():
    # Database persistence gate must block all alerts
    for sc in ["TECHNICAL", "PULLBACK", "ACCUMULATION", "EOD"]:
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
        assert "UNDER_CERTIFICATION" in reason

    for sc in ["SHORT_COVERING", "5M_BREAKOUT", "REVERSAL", "TECHNICAL_INTRADAY"]:
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


def test_dispatch_cannot_bypass_governance():
    for sc in ["TECHNICAL", "PULLBACK", "ACCUMULATION", "EOD"]:
        assert can_scanner_emit_production_alert(sc) is False
        with pytest.raises(PermissionError):
            assert_production_alert_permitted(sc)
