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
    assert info_bull["selected_variant"] == "TECH-V01-BULL"
    assert info_bull["production_authorization_state"] == "LOCK REVIEW PENDING"
    assert info_bull["is_production_active"] is False
    assert info_bull["production_active_now"] == "NO"
    assert "Certified for production in BULL. Current regime=BULL." in info_bull["display_message"]
    assert "LOCK REVIEW PENDING" in info_bull["display_message"]
    assert info_bull["reason_code"] == "AUTH_PENDING_ADMIN_UNLOCK"

    info_side = get_scanner_health_regime_info("TECHNICAL", current_macro_regime="SIDEWAYS")
    assert info_side["display_message"] == "Suppressed: selected variant is certified only in BULL; current regime=SIDEWAYS."
    assert info_side["reason_code"] == "REGIME_MISMATCH_SIDEWAYS_VS_BULL"
    assert info_side["is_production_active"] is False

    info_bear = get_scanner_health_regime_info("TECHNICAL", current_macro_regime="BEAR")
    assert info_bear["display_message"] == "Suppressed: selected variant is certified only in BULL; current regime=BEAR."
    assert info_bear["reason_code"] == "REGIME_MISMATCH_BEAR_VS_BULL"
    assert info_bear["is_production_active"] is False


def test_pullback_health_regime_mapping():
    for r in ["BULL", "SIDEWAYS", "BEAR"]:
        info = get_scanner_health_regime_info("PULLBACK", current_macro_regime=r)
        assert info["supported_regime"] == "NONE"
        assert info["lifecycle_state"] == "DECOMMISSIONED"
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"
        assert info["production_authorization_state"] == "DISCARDED — NO SURVIVING VARIANTS"
        assert info["certification_status"] == "DISCARDED — ZERO QUALIFYING VARIANTS"
        assert info["reason_code"] == "DISCARDED_ZERO_QUALIFYING_VARIANTS"
        assert info["display_message"] == "No tested variant passed all certification gates; scanner remains out of production."


def test_accumulation_health_regime_mapping():
    for r in ["BULL", "SIDEWAYS", "BEAR"]:
        info = get_scanner_health_regime_info("ACCUMULATION", current_macro_regime=r)
        assert info["supported_regime"] == "NONE"
        assert info["lifecycle_state"] == "DECOMMISSIONED"
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"
        assert info["production_authorization_state"] == "DISCARDED — NO SURVIVING VARIANTS"
        assert info["certification_status"] == "DISCARDED — ZERO QUALIFYING VARIANTS"
        assert info["reason_code"] == "DISCARDED_ZERO_QUALIFYING_VARIANTS"
        assert info["display_message"] == "No tested variant passed all certification gates; scanner remains out of production."


def test_eod_health_regime_mapping():
    for r in ["BULL", "SIDEWAYS", "BEAR"]:
        info = get_scanner_health_regime_info("EOD", current_macro_regime=r)
        assert info["supported_regime"] == "NONE"
        assert info["lifecycle_state"] == "DECOMMISSIONED"
        assert info["production_authorization_state"] == "DISCARDED — NO SURVIVING VARIANTS"
        assert info["certification_status"] == "DISCARDED — ZERO QUALIFYING VARIANTS"
        assert info["reason_code"] == "DISCARDED_ZERO_QUALIFYING_VARIANTS"
        assert info["display_message"] == "No tested variant passed all certification gates; scanner remains out of production."
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"


def test_core_invariant_supported_not_equal_production_active():
    for sc in ["TECHNICAL", "PULLBACK", "ACCUMULATION", "EOD"]:
        info = get_scanner_health_regime_info(sc)
        assert info["supported_regime"] != info["current_production_active_regime"]
        assert info["is_production_active"] is False
        assert info["production_active_now"] == "NO"
        assert sc not in CERTIFIED_PRODUCTION_SCANNERS

    assert "TECHNICAL" in UNDER_CERTIFICATION_SCANNERS
    for sc in ["PULLBACK", "ACCUMULATION", "EOD"]:
        assert sc in DECOMMISSIONED_SCANNERS


def test_fail_closed_empty_production_set():
    assert len(CERTIFIED_PRODUCTION_SCANNERS) == 0


def test_decommissioned_scanners_silence():
    for sc in DECOMMISSIONED_SCANNERS:
        info = get_scanner_health_regime_info(sc)
        assert info["lifecycle_state"] == "DECOMMISSIONED"
        assert info["is_production_active"] is False
        assert can_scanner_emit_production_alert(sc) is False
        with pytest.raises(PermissionError):
            assert_production_alert_permitted(sc)


def test_persistence_gate_blocks_under_certification_and_decommissioned():
    # Database persistence gate must block TECHNICAL (under certification)
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

    # Discarded and decommissioned scanners must be blocked as DECOMMISSIONED
    for sc in ["PULLBACK", "ACCUMULATION", "EOD", "SHORT_COVERING", "5M_BREAKOUT", "REVERSAL", "TECHNICAL_INTRADAY"]:
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
