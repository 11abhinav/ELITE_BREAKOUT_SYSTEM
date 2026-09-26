#!/usr/bin/env python3
"""
AUTHORITATIVE PRODUCTION GOVERNANCE & REGIME ROUTING REGISTRY
============================================================
Enforces strict evidence-driven production scanner lifecycle and regime routing.

Governance Principles:
1. ONLY THREE SCANNER STATES PERMITTED:
   - UNDER_CERTIFICATION: Zero production alerts allowed.
   - CERTIFIED_FOR_PRODUCTION: May generate live production alerts ONLY in certified regimes.
   - DECOMMISSIONED: Permanently silenced; zero alerts, zero scheduling, zero production routing.
   NO SHADOW, NO PAPER, NO PROVISIONAL_PRODUCTION, NO OBSERVATION_ONLY.

2. CERTIFIED THREE-REGIME ROUTING MATRIX:
   Scanner      | BULL                    | SIDEWAYS                | BEAR
   -------------+-------------------------+-------------------------+-------------------------
   TECHNICAL    | CERTIFIED_FOR_PRODUCTION| NOT_CERTIFIED           | NOT_CERTIFIED
   PULLBACK     | NOT_CERTIFIED           | CERTIFIED_FOR_PRODUCTION| NOT_CERTIFIED
   ACCUMULATION | NOT_CERTIFIED           | NOT_CERTIFIED           | CERTIFIED_FOR_PRODUCTION
   EOD          | NOT_CERTIFIED           | NOT_CERTIFIED           | NOT_CERTIFIED (UNDER_CERT)

3. AUTOMATED PRODUCTION SAFETY ASSERTIONS:
   - No decommissioned scanner in active production scanners.
   - All active production scanners must be certified.
   - System fails closed if any scanner lacks a valid state.
"""

import os
import sys
import logging
from typing import Dict, List, Set, Tuple, Optional, Any

logger = logging.getLogger("GOVERNANCE_REGISTRY")

# 1. Authoritative Scanner States
VALID_SCANNER_STATES: Set[str] = {
    "UNDER_CERTIFICATION",
    "CERTIFIED_FOR_PRODUCTION",
    "DECOMMISSIONED"
}

# 2. Authoritative Decommissioned Scanners (Permanently Silenced)
DECOMMISSIONED_SCANNERS: Set[str] = {
    "SHORT_COVERING",
    "5M_BREAKOUT",
    "MOMENTUM_IGNITION",
    "MULTI_TF",
    "TECHNICAL_INTRADAY",
    "REVERSAL",
    # Variants and aliases
    "SHORT_COVERING_5M",
    "SHORT_COVERING_EOD",
    "SHORT_COVERING_IGNITION",
    "MOMENTUM_IGNITION_5M",
    "MOMENTUM_THRUST",
    "MOMENTUM_THRUST_H0",
    "BREAKOUT_5M",
    "SCAN_5M_BREAKOUT",
    "MULTITF",
    "MULTI_TF_5M",
    "MULTITF_5M",
    "MULTI_TF_LADDER",
    "MULTITF_V3",
    "REVERSAL_SCANNER",
    "REVERSAL_V2",
    "SCAN_SHORT_COVERING",
    "SCAN_REVERSAL_KEYLEVEL"
}

# 3. Scanners Under Certification (Zero Production Alerts Permitted)
# All candidate scanners remain strictly under certification until empirical temporal replication is authorized.
UNDER_CERTIFICATION_SCANNERS: Set[str] = {
    "TECHNICAL",
    "PULLBACK",
    "ACCUMULATION",
    "EOD",
    "SCAN_EOD",
    "EOD_SCANNER"
}

# 4. Certified Production Scanners (Must clear Regime AND Temporal Replication Gates)
# Currently empty: zero live alerts permitted until final governance lock.
CERTIFIED_PRODUCTION_SCANNERS: Set[str] = set()

# 5. Authoritative Three-Regime Certification Routing Matrix
# Gated: No scanner is active until full multi-cell temporal replication passes governance review.
REGIME_ROUTING_MATRIX: Dict[str, Dict[str, str]] = {
    "TECHNICAL": {
        "BULL": "UNDER_CERTIFICATION",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "NOT_CERTIFIED"
    },
    "PULLBACK": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "UNDER_CERTIFICATION",
        "BEAR": "NOT_CERTIFIED"
    },
    "ACCUMULATION": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "UNDER_CERTIFICATION"
    },
    "EOD": {
        "BULL": "NOT_CERTIFIED",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "NOT_CERTIFIED"
    }
}

# 6. Authoritative Scanner Health Regime & Temporal Robustness Mapping
# Invariant: "Supported regime" != "Currently production active".
# Fail-closed: All candidate breakout scanners remain strictly not active in production until explicit final lock.
SCANNER_REGIME_HEALTH_METADATA: Dict[str, Dict[str, Any]] = {
    "TECHNICAL": {
        "supported_regime": "BULL",
        "evidence_supported_regimes": ["BULL"],
        "current_production_active_regime": "None until final lock verification",
        "production_authorization_state": "LOCK REVIEW PENDING",
        "temporal_evidence_status": "Replication Passed / Ready for Lock Review",
        "temporal_evidence": "Replicated positively across all four multi-year cells (+0.220R, +0.233R, +0.221R, +0.146R) and all quarters (Q1-Q4).",
        "evidence_warnings": [],
        "warning": None,
        "suppression_reason": "Suppressed: TECHNICAL evidence supports BULL only; current regime is {current_regime}.",
        "pending_condition": "Not production-active pending final lock verification."
    },
    "PULLBACK": {
        "supported_regime": "SIDEWAYS",
        "evidence_supported_regimes": ["SIDEWAYS"],
        "current_production_active_regime": "None — recent decay prevents lock",
        "production_authorization_state": "DO NOT PERMANENTLY LOCK / CERTIFICATION PENDING",
        "temporal_evidence_status": "Temporal edge compression in 2025–26; Q1 weakness",
        "temporal_evidence": "Positive across multi-year cells (+0.208R, +0.138R, +0.107R), but substantial 2025–26 edge compression (+0.015R, CI crosses zero) and Q1 negative (-0.031R).",
        "evidence_warnings": ["recent 2025–26 edge compression", "Q1 weakness"],
        "warning": "Warning: 2025–26 temporal edge compression",
        "suppression_reason": "Suppressed: PULLBACK evidence supports SIDEWAYS only.",
        "pending_condition": "Recent decay (2025–26 Arm B CI crosses zero) prevents production lock."
    },
    "ACCUMULATION": {
        "supported_regime": "BEAR",
        "evidence_supported_regimes": ["BEAR"],
        "current_production_active_regime": "None until final lock verification",
        "production_authorization_state": "SUBJECT TO FINAL CERTIFICATION (NOT YET UNLOCKED)",
        "temporal_evidence_status": "Replication Passed, but Q1 seasonal weakness remains",
        "temporal_evidence": "Positive across all four multi-year cells (+0.040R, +0.136R, +0.206R, +0.098R); observed Q1 seasonal weakness (-0.060R).",
        "evidence_warnings": ["Q1 seasonal weakness (-0.060R)"],
        "warning": "Warning: Q1 seasonal weakness",
        "suppression_reason": "Suppressed: ACCUMULATION evidence supports BEAR only; current regime is {current_regime}.",
        "pending_condition": "Not production-active pending final lock verification."
    },
    "EOD": {
        "supported_regime": "NONE",
        "evidence_supported_regimes": [],
        "current_production_active_regime": "None",
        "production_authorization_state": "UNDERPOWERED / UNDER CERTIFICATION",
        "temporal_evidence_status": "Underpowered across temporal cells / CI crosses zero",
        "temporal_evidence": "Evidence remains underpowered / insufficient sample size across temporal cells.",
        "evidence_warnings": ["Severely underpowered sample size", "CI crosses zero in all regimes"],
        "warning": None,
        "suppression_reason": "Suppressed: EOD remains underpowered/under certification; no live production alerts.",
        "pending_condition": "Underpowered sample sizes across all cells. Remains under certification."
    }
}


def get_scanner_health_regime_info(scanner_name: Optional[str], current_macro_regime: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the authoritative regime health metadata for a given scanner.
    Enforces the core rule: Supported regime != Currently production active.
    """
    norm = normalize_scanner_name(scanner_name)
    current_regime = (current_macro_regime or get_current_macro_regime()).strip().upper()
    last_evidence_release = "TEMPORAL_REPLICATION_2026-09-26"
    last_study_date = "2026-09-26"
    version_identity = "v6.0-prod"

    if norm in SCANNER_REGIME_HEALTH_METADATA:
        info = dict(SCANNER_REGIME_HEALTH_METADATA[norm])
        supported = info["supported_regime"]
        warning = info.get("warning")
        warnings = list(info.get("evidence_warnings", []))
        
        # Build exact user-specified plain-language display messages
        if norm == "TECHNICAL":
            if current_regime == "BULL":
                display_msg = "Evidence-supported regime: BULL. Current production authorization: LOCK REVIEW PENDING. No live alert unless production governance is unlocked."
                suppression_reason = "No live alert unless production governance is unlocked."
            else:
                display_msg = f"Suppressed: TECHNICAL evidence supports BULL only; current regime is {current_regime}."
                suppression_reason = display_msg
        elif norm == "ACCUMULATION":
            if current_regime == "BEAR":
                display_msg = "Evidence-supported regime: BEAR. Q1 seasonal weakness noted. Production authorization remains subject to final certification."
                suppression_reason = "Production authorization remains subject to final certification."
            else:
                display_msg = f"Suppressed: ACCUMULATION evidence supports BEAR only; current regime is {current_regime}."
                suppression_reason = display_msg
        elif norm == "PULLBACK":
            if current_regime == "SIDEWAYS":
                display_msg = "Evidence-supported regime: SIDEWAYS. Production lock is not authorized because of recent 2025–26 edge compression."
                suppression_reason = display_msg
            else:
                display_msg = "Suppressed: PULLBACK evidence supports SIDEWAYS only."
                suppression_reason = display_msg
        elif norm == "EOD":
            display_msg = "Suppressed: EOD remains underpowered/under certification; no live production alerts."
            suppression_reason = display_msg
        else:
            display_msg = info.get("suppression_reason", "")
            suppression_reason = display_msg

        if norm == "TECHNICAL":
            reason_code = "AUTH_PENDING_ADMIN_UNLOCK" if current_regime == "BULL" else f"REGIME_MISMATCH_{current_regime}_VS_BULL"
        elif norm == "ACCUMULATION":
            reason_code = "LOCK_BLOCKED_Q1_SEASONAL_WEAKNESS" if current_regime == "BEAR" else f"REGIME_MISMATCH_{current_regime}_VS_BEAR"
        elif norm == "PULLBACK":
            reason_code = "LOCK_BLOCKED_TEMPORAL_DECAY" if current_regime == "SIDEWAYS" else f"REGIME_MISMATCH_{current_regime}_VS_SIDEWAYS"
        elif norm == "EOD":
            reason_code = "UNDERPOWERED_UNDER_CERTIFICATION"
        else:
            reason_code = "UNDER_CERTIFICATION"

        return {
            "scanner": norm,
            "scanner_name": norm,
            "lifecycle_state": "UNDER_CERTIFICATION",
            "current_macro_regime": current_regime,
            "evidence_supported_regime": supported,
            "supported_regime": supported,
            "evidence_supported_regimes": info.get("evidence_supported_regimes", [supported]),
            "production_active_now": "NO",
            "is_production_active": False,
            "production_authorization_state": info["production_authorization_state"],
            "current_production_active_regime": info["current_production_active_regime"],
            "temporal_evidence_status": info["temporal_evidence_status"],
            "temporal_evidence": info["temporal_evidence"],
            "last_evidence_release": last_evidence_release,
            "last_study_date": last_study_date,
            "certification_date": last_study_date,
            "evidence_warnings": warnings,
            "warning": warning,
            "version_identity": version_identity,
            "suppression_reason": suppression_reason,
            "reason_code": reason_code,
            "status_message": display_msg,
            "display_message": display_msg,
            "pending_condition": info["pending_condition"],
            "fail_closed_note": "Supported regime ≠ currently production active. Live alerts fail-closed (0 alerts)."
        }
    
    if norm in DECOMMISSIONED_SCANNERS:
        disp = "Decommissioned — permanently silenced."
        return {
            "scanner": norm,
            "scanner_name": norm,
            "lifecycle_state": "DECOMMISSIONED",
            "current_macro_regime": current_regime,
            "evidence_supported_regime": "DECOMMISSIONED",
            "supported_regime": "DECOMMISSIONED",
            "evidence_supported_regimes": [],
            "production_active_now": "NO",
            "is_production_active": False,
            "production_authorization_state": "PERMANENTLY SILENCED",
            "current_production_active_regime": "None — Decommissioned",
            "temporal_evidence_status": "Decommissioned by governance",
            "temporal_evidence": "Failed certification or decommissioned by governance.",
            "last_evidence_release": last_evidence_release,
            "last_study_date": last_study_date,
            "certification_date": last_study_date,
            "evidence_warnings": ["Decommissioned permanently"],
            "warning": "Decommissioned permanently",
            "version_identity": version_identity,
            "suppression_reason": disp,
            "reason_code": "PERMANENTLY_DECOMMISSIONED",
            "status_message": disp,
            "display_message": disp,
            "pending_condition": "Permanently decommissioned.",
            "fail_closed_note": "Decommissioned scanners produce zero alerts."
        }

        
    return {
        "scanner": norm,
        "scanner_name": norm,
        "lifecycle_state": "OPERATIONAL_WORKER",
        "current_macro_regime": current_regime,
        "evidence_supported_regime": "ALL_REGIMES",
        "supported_regime": "ALL_REGIMES",
        "evidence_supported_regimes": ["ALL_REGIMES"],
        "production_active_now": "YES",
        "is_production_active": True,
        "production_authorization_state": "ACTIVE",
        "current_production_active_regime": "ACTIVE",
        "temporal_evidence_status": "Operational system worker",
        "temporal_evidence": "N/A",
        "last_evidence_release": last_evidence_release,
        "last_study_date": last_study_date,
        "evidence_warnings": [],
        "warning": None,
        "version_identity": version_identity,
        "suppression_reason": None,
        "status_message": "Operational system worker / monitor.",
        "display_message": "Operational system worker / monitor.",
        "pending_condition": "N/A",
        "fail_closed_note": "System worker"
    }


def normalize_scanner_name(scanner_name: Optional[str]) -> str:
    """Normalizes any scanner identifier or breakout tag into its canonical family name."""
    if not scanner_name:
        return "UNKNOWN"
    s = scanner_name.strip().upper()
    if s.startswith("SCAN_"):
        s = s[5:]

    if any(k in s for k in ("SHORT_COVERING", "SHORT_COVER")):
        return "SHORT_COVERING"
    if any(k in s for k in ("MOMENTUM_IGNITION", "MOMENTUM_THRUST")):
        return "MOMENTUM_IGNITION"
    if any(k in s for k in ("5M_BREAKOUT", "BREAKOUT_5M")):
        return "5M_BREAKOUT"
    if any(k in s for k in ("MULTI_TF", "MULTITF")):
        return "MULTI_TF"
    if "TECHNICAL_INTRADAY" in s:
        return "TECHNICAL_INTRADAY"
    if any(k in s for k in ("REVERSAL", "KEYLEVEL")):
        return "REVERSAL"
    if "PULLBACK" in s:
        return "PULLBACK"
    if "ACCUMULATION" in s:
        return "ACCUMULATION"
    if "TECHNICAL" in s:
        return "TECHNICAL"
    if "EOD" in s:
        return "EOD"
    return s


def get_scanner_governance_state(scanner_name: str) -> str:
    """
    Returns the authoritative governance state:
    - CERTIFIED_FOR_PRODUCTION
    - UNDER_CERTIFICATION
    - DECOMMISSIONED
    Fails closed (raises ValueError) if uncertified/unregistered.
    """
    norm = normalize_scanner_name(scanner_name)
    raw = (scanner_name or "").strip().upper()

    if norm in DECOMMISSIONED_SCANNERS or raw in DECOMMISSIONED_SCANNERS:
        return "DECOMMISSIONED"
    if norm in UNDER_CERTIFICATION_SCANNERS or raw in UNDER_CERTIFICATION_SCANNERS:
        return "UNDER_CERTIFICATION"
    if norm in CERTIFIED_PRODUCTION_SCANNERS or raw in CERTIFIED_PRODUCTION_SCANNERS:
        return "CERTIFIED_FOR_PRODUCTION"

    # Fail closed on unknown scanner
    raise ValueError(
        f"CRITICAL GOVERNANCE FAILURE: Scanner '{scanner_name}' (normalized: '{norm}') "
        f"has no valid certification state in governance registry! System failing closed."
    )


def check_production_alert_permission(scanner_name: str, macro_regime: str) -> Tuple[bool, str]:
    """
    Determines if a scanner is permitted to generate live production alerts
    in the specified macro regime.
    
    Returns (is_permitted: bool, reason: str).
    """
    try:
        norm = normalize_scanner_name(scanner_name)
        state = get_scanner_governance_state(norm)
    except ValueError as e:
        logger.error(f"🛑 [GOVERNANCE GATE] Fail-Closed: {e}")
        return False, "UNKNOWN_SCANNER_FAIL_CLOSED"

    if state == "DECOMMISSIONED":
        return False, f"SCANNER_{norm}_IS_DECOMMISSIONED"

    if state == "UNDER_CERTIFICATION":
        return False, f"SCANNER_{norm}_IS_UNDER_CERTIFICATION_ZERO_ALERTS"

    if state != "CERTIFIED_FOR_PRODUCTION":
        return False, f"INVALID_STATE_{state}"

    # Evaluate against certified three-regime routing matrix
    regime = (macro_regime or "BULL").strip().upper()
    regime_map = REGIME_ROUTING_MATRIX.get(norm, {})
    regime_status = regime_map.get(regime, "NOT_CERTIFIED")

    if regime_status == "CERTIFIED_FOR_PRODUCTION":
        return True, f"CERTIFIED_FOR_PRODUCTION_{norm}_{regime}"
    else:
        return False, f"REGIME_NOT_CERTIFIED_{norm}_{regime}"


def can_scanner_emit_production_alert(scanner_name: str, macro_regime: Optional[str] = None) -> bool:
    """Convenience helper returning True only if scanner is fully certified and permitted in the given regime."""
    reg = macro_regime or get_current_macro_regime()
    is_perm, _ = check_production_alert_permission(scanner_name, reg)
    return is_perm


def assert_production_alert_permitted(scanner_name: str, macro_regime: Optional[str] = None) -> None:
    """Raises PermissionError if the scanner is not authorized to emit live production alerts."""
    reg = macro_regime or get_current_macro_regime()
    is_perm, reason = check_production_alert_permission(scanner_name, reg)
    if not is_perm:
        raise PermissionError(f"Production alert blocked by governance: {reason}")



def validate_production_safety_assertions(current_macro_regime: str) -> List[str]:
    """
    Executes mandatory automated production safety assertions:
    1. assert not any(scanner in DECOMMISSIONED_SCANNERS for scanner in ACTIVE_PRODUCTION_SCANNERS)
    2. assert all(scanner in CERTIFIED_PRODUCTION_SCANNERS for scanner in ACTIVE_PRODUCTION_SCANNERS)
    3. Logs active scanners, current regime, allowed scanners, blocked scanners, decommissioned scanners.
    
    Returns active_production_scanners for the current session.
    """
    regime = (current_macro_regime or "BULL").strip().upper()

    # Determine allowed scanners strictly from certified regime routing matrix
    allowed_scanners = [
        s for s in sorted(list(CERTIFIED_PRODUCTION_SCANNERS))
        if REGIME_ROUTING_MATRIX.get(s, {}).get(regime) == "CERTIFIED_FOR_PRODUCTION"
    ]

    blocked_scanners = [
        s for s in sorted(list(CERTIFIED_PRODUCTION_SCANNERS | UNDER_CERTIFICATION_SCANNERS))
        if s not in allowed_scanners
    ]

    active_production_scanners = list(allowed_scanners)

    # Mandatory Governance Assertions (§9)
    assert not any(
        s in DECOMMISSIONED_SCANNERS for s in active_production_scanners
    ), f"CRITICAL GOVERNANCE BREACH: Decommissioned scanner detected in active production: {active_production_scanners}"

    assert all(
        s in CERTIFIED_PRODUCTION_SCANNERS for s in active_production_scanners
    ), f"CRITICAL GOVERNANCE BREACH: Non-certified scanner detected in active production: {active_production_scanners}"

    # Mandatory Runtime Telemetry Logging (§9)
    logger.info("=" * 70)
    logger.info("🛡️ PRODUCTION GOVERNANCE SAFETY AUDIT & REGIME ROUTER")
    logger.info(f"  ACTIVE_PRODUCTION_SCANNERS:   {active_production_scanners}")
    logger.info(f"  CURRENT_MACRO_REGIME:          {regime}")
    logger.info(f"  ALLOWED_SCANNERS_FOR_REGIME:   {allowed_scanners}")
    logger.info(f"  BLOCKED_SCANNERS:              {blocked_scanners}")
    logger.info(f"  DECOMMISSIONED_SCANNERS:       {sorted(list(DECOMMISSIONED_SCANNERS))}")
    logger.info("=" * 70)

    return active_production_scanners


def get_current_macro_regime() -> str:
    """
    Returns the current macro regime (BULL, SIDEWAYS, BEAR) point-in-time.
    Defaults to the latest trading date's regime from regime_daycount_daily.csv.
    """
    regime_file = "reports/certification/FINAL_AUDIT_2026-09-26/regime_daycount_daily.csv"
    if os.path.exists(regime_file):
        try:
            import pandas as pd
            df = pd.read_csv(regime_file)
            if not df.empty and "macro_regime" in df.columns:
                return str(df["macro_regime"].iloc[-1]).strip().upper()
        except Exception as e:
            logger.warning(f"Could not load macro regime from file: {e}")
    return "BULL"
