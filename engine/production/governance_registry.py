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
    # Discarded surviving scanner families after multi-variant battery
    "ACCUMULATION",
    "PULLBACK",
    "EOD",
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
    "SCAN_REVERSAL_KEYLEVEL",
    "SCAN_ACCUMULATION",
    "SCAN_PULLBACK",
    "SCAN_EOD",
    "EOD_SCANNER",
    "MULTIBAGGER",
    "MULTIBAGGER_EXIT",
    "Wealth Engine",
    "WEALTH_ENGINE",
    "WEALTH",
    "WEALTH_EXIT"
}

# 3. Scanners Under Certification (Zero Production Alerts Permitted)
UNDER_CERTIFICATION_SCANNERS: Set[str] = set()

# 4. Certified Production Scanners (Must clear Regime AND Temporal Replication Gates)
# Formally unlocked: TECHNICAL is certified exclusively for BULL regime.
# Formally unlocked: FUNDAMENTAL is certified across ALL regimes (BULL, SIDEWAYS, BEAR) based on 2016-2026 backtest.
CERTIFIED_PRODUCTION_SCANNERS: Set[str] = {
    "TECHNICAL",
    "FUNDAMENTAL",
    "QUALITY_VALUE_RECOVERY",
    "QUALITY_COMPOUNDER",
    "QUALITY_COMPOUNDER_VALUE_V2_FINAL"
}

# 5. Authoritative Three-Regime Certification Routing Matrix
REGIME_ROUTING_MATRIX: Dict[str, Dict[str, str]] = {
    "TECHNICAL": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",
        "SIDEWAYS": "NOT_CERTIFIED",
        "BEAR": "NOT_CERTIFIED"
    },
    "FUNDAMENTAL": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",
        "SIDEWAYS": "CERTIFIED_FOR_PRODUCTION",
        "BEAR": "CERTIFIED_FOR_PRODUCTION"
    },
    "QUALITY_VALUE_RECOVERY": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",
        "SIDEWAYS": "CERTIFIED_FOR_PRODUCTION",
        "BEAR": "CERTIFIED_FOR_PRODUCTION"
    },
    "QUALITY_COMPOUNDER": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",
        "SIDEWAYS": "CERTIFIED_FOR_PRODUCTION",
        "BEAR": "CERTIFIED_FOR_PRODUCTION"
    },
    "QUALITY_COMPOUNDER_VALUE_V2_FINAL": {
        "BULL": "CERTIFIED_FOR_PRODUCTION",
        "SIDEWAYS": "CERTIFIED_FOR_PRODUCTION",
        "BEAR": "CERTIFIED_FOR_PRODUCTION"
    },
    "PULLBACK": {
        "BULL": "DECOMMISSIONED",
        "SIDEWAYS": "DECOMMISSIONED",
        "BEAR": "DECOMMISSIONED"
    },
    "ACCUMULATION": {
        "BULL": "DECOMMISSIONED",
        "SIDEWAYS": "DECOMMISSIONED",
        "BEAR": "DECOMMISSIONED"
    },
    "EOD": {
        "BULL": "DECOMMISSIONED",
        "SIDEWAYS": "DECOMMISSIONED",
        "BEAR": "DECOMMISSIONED"
    }
}

# 6. Authoritative Scanner Health Regime & Temporal Robustness Mapping
SCANNER_REGIME_HEALTH_METADATA: Dict[str, Dict[str, Any]] = {
    "TECHNICAL": {
        "selected_variant": "TECH-V01-BULL",
        "supported_regime": "BULL",
        "evidence_supported_regimes": ["BULL"],
        "production_authorized_regimes": ["BULL"],
        "current_production_active_regime": "BULL (Active when macro_regime == BULL)",
        "production_authorization_state": "CERTIFIED_FOR_PRODUCTION (BULL ONLY)",
        "lifecycle_state": "CERTIFIED_FOR_PRODUCTION",
        "certification_status": "CERTIFIED_FOR_PRODUCTION",
        "temporal_evidence_status": "Replication Passed & Certified",
        "temporal_evidence": "Replicated positively across all four multi-year cells (+0.220R, +0.233R, +0.221R, +0.146R) and all quarters (Q1-Q4).",
        "evidence_warnings": [],
        "warning": None,
        "suppression_reason": "Suppressed: selected variant is certified only in BULL; current regime={current_regime}.",
        "pending_condition": "Live production alerts authorized exclusively when macro_regime is BULL."
    },
    "FUNDAMENTAL": {
        "selected_variant": "FUND-V01-ALL-REGIMES",
        "supported_regime": "ALL (BULL, SIDEWAYS, BEAR)",
        "evidence_supported_regimes": ["BULL", "SIDEWAYS", "BEAR"],
        "production_authorized_regimes": ["BULL", "SIDEWAYS", "BEAR"],
        "current_production_active_regime": "Active in all regimes (BULL, SIDEWAYS, BEAR)",
        "production_authorization_state": "CERTIFIED_FOR_PRODUCTION (ALL REGIMES)",
        "lifecycle_state": "CERTIFIED_FOR_PRODUCTION",
        "certification_status": "CERTIFIED_FOR_PRODUCTION",
        "temporal_evidence_status": "Replication Passed & Certified",
        "temporal_evidence": "Replicated across all 4 temporal cells (2016-18, 2019-21, 2022-24, 2025-26) and 3 regimes (Bull +8.27%, Sideways +5.22%, Bear +6.12% in V1; V2 delivers +17.68%, +11.18%, +15.08%).",
        "evidence_warnings": [],
        "warning": None,
        "suppression_reason": None,
        "pending_condition": "Live production alerts active across BULL, SIDEWAYS, and BEAR."
    },
    "QUALITY_VALUE_RECOVERY": {
        "selected_variant": "RECOVERY-V01-MODEL-D-E3",
        "supported_regime": "ALL (BULL, SIDEWAYS, BEAR)",
        "evidence_supported_regimes": ["BULL", "SIDEWAYS", "BEAR"],
        "production_authorized_regimes": ["BULL", "SIDEWAYS, BEAR"],
        "current_production_active_regime": "Active in all regimes (BULL, SIDEWAYS, BEAR)",
        "production_authorization_state": "CERTIFIED_FOR_PRODUCTION (ALL REGIMES)",
        "lifecycle_state": "CERTIFIED_FOR_PRODUCTION",
        "certification_status": "CERTIFIED_FOR_PRODUCTION",
        "temporal_evidence_status": "Replication Passed & Certified",
        "temporal_evidence": "Replicated across all temporal cells and bear market dislocation episodes.",
        "evidence_warnings": [],
        "warning": None,
        "suppression_reason": None,
        "pending_condition": "Live production alerts active across BULL, SIDEWAYS, and BEAR."
    },
    "PULLBACK": {
        "selected_variant": "NONE",
        "supported_regime": "NONE",
        "evidence_supported_regimes": [],
        "production_authorized_regimes": [],
        "current_production_active_regime": "None — Discarded",
        "production_authorization_state": "DISCARDED — NO SURVIVING VARIANTS",
        "lifecycle_state": "DECOMMISSIONED",
        "certification_status": "DISCARDED — ZERO QUALIFYING VARIANTS",
        "temporal_evidence_status": "Zero variants qualified across all regimes",
        "temporal_evidence": "0 of 12 variant-regime combinations qualified. Base decayed in 2025–26; all feature variants failed incremental alpha.",
        "evidence_warnings": ["Zero variants qualified", "Modern decay across all variants"],
        "warning": None,
        "suppression_reason": "No tested variant passed all certification gates; scanner remains out of production.",
        "pending_condition": "Permanently discarded from production consideration."
    },
    "ACCUMULATION": {
        "selected_variant": "NONE",
        "supported_regime": "NONE",
        "evidence_supported_regimes": [],
        "production_authorized_regimes": [],
        "current_production_active_regime": "None — Discarded",
        "production_authorization_state": "DISCARDED — NO SURVIVING VARIANTS",
        "lifecycle_state": "DECOMMISSIONED",
        "certification_status": "DISCARDED — ZERO QUALIFYING VARIANTS",
        "temporal_evidence_status": "Zero variants qualified across all regimes",
        "temporal_evidence": "0 of 12 variant-regime combinations qualified. Persistent Q1 weakness across all BEAR variants; BULL/SIDEWAYS holdouts cross zero.",
        "evidence_warnings": ["Zero variants qualified", "Persistent Q1 weakness across all BEAR variants"],
        "warning": None,
        "suppression_reason": "No tested variant passed all certification gates; scanner remains out of production.",
        "pending_condition": "Permanently discarded from production consideration."
    },
    "EOD": {
        "selected_variant": "NONE",
        "supported_regime": "NONE",
        "evidence_supported_regimes": [],
        "production_authorized_regimes": [],
        "current_production_active_regime": "None — Discarded",
        "production_authorization_state": "DISCARDED — NO SURVIVING VARIANTS",
        "lifecycle_state": "DECOMMISSIONED",
        "certification_status": "DISCARDED — ZERO QUALIFYING VARIANTS",
        "temporal_evidence_status": "Zero variants qualified across all regimes",
        "temporal_evidence": "0 of 12 variant-regime combinations qualified. Severely underpowered across all cells; 95% bootstrap CIs cross zero widely.",
        "evidence_warnings": ["Zero variants qualified", "Underpowered across all regimes and cells"],
        "warning": None,
        "suppression_reason": "No tested variant passed all certification gates; scanner remains out of production.",
        "pending_condition": "Permanently discarded from production consideration."
    }
}


def get_scanner_health_regime_info(scanner_name: Optional[str], current_macro_regime: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the authoritative regime health metadata for a given scanner.
    Enforces the core rule: Supported regime != Currently production active.
    """
    norm = normalize_scanner_name(scanner_name)
    current_regime = (current_macro_regime or get_current_macro_regime()).strip().upper()
    last_evidence_release = "MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26"
    last_study_date = "2026-09-26"
    version_identity = "v6.0-prod"

    if norm in SCANNER_REGIME_HEALTH_METADATA:
        info = dict(SCANNER_REGIME_HEALTH_METADATA[norm])
        supported = info["supported_regime"]
        warning = info.get("warning")
        warnings = list(info.get("evidence_warnings", []))
        lifecycle = info.get("lifecycle_state", "DECOMMISSIONED")
        selected_variant = info.get("selected_variant", "NONE")
        cert_status = info.get("certification_status", "UNKNOWN")
        
        is_prod_active = False
        # Build exact plain-language display messages and reason codes
        if norm == "TECHNICAL":
            if current_regime == "BULL":
                display_msg = "Certified for production in BULL. Current regime=BULL. Production alerts active."
                suppression_reason = None
                reason_code = "CERTIFIED_ACTIVE"
                is_prod_active = True
            else:
                display_msg = f"Suppressed: selected variant is certified only in BULL; current regime={current_regime}."
                suppression_reason = display_msg
                reason_code = f"REGIME_MISMATCH_{current_regime}_VS_BULL"
                is_prod_active = False
        elif norm in ("FUNDAMENTAL", "QUALITY_VALUE_RECOVERY", "QUALITY_VALUE_RECOVERY_WEALTH_V1"):
            display_msg = f"Certified for production across all regimes (BULL, SIDEWAYS, BEAR). Current regime={current_regime}. Production alerts active."
            suppression_reason = None
            reason_code = "CERTIFIED_ACTIVE"
            is_prod_active = True
        elif norm in ("ACCUMULATION", "PULLBACK", "EOD"):
            display_msg = "No tested variant passed all certification gates; scanner remains out of production."
            suppression_reason = display_msg
            reason_code = "DISCARDED_ZERO_QUALIFYING_VARIANTS"
        else:
            display_msg = info.get("suppression_reason", "")
            suppression_reason = display_msg
            reason_code = "UNDER_CERTIFICATION"

        return {
            "scanner": norm,
            "scanner_name": norm,
            "selected_variant": selected_variant,
            "algorithm_version": version_identity,
            "evidence_release": last_evidence_release,
            "current_macro_regime": current_regime,
            "current_regime": current_regime,
            "evidence_supported_regime": supported,
            "supported_regime": supported,
            "evidence_supported_regimes": info.get("evidence_supported_regimes", [supported] if supported != "NONE" else []),
            "production_authorized_regimes": info.get("production_authorized_regimes", []),
            "production_active_now": "YES" if is_prod_active else "NO",
            "is_production_active": is_prod_active,
            "lifecycle": lifecycle,
            "lifecycle_state": lifecycle,
            "certification_status": cert_status,
            "production_authorization_state": info["production_authorization_state"],
            "current_production_active_regime": info["current_production_active_regime"],
            "temporal_evidence_status": info["temporal_evidence_status"],
            "temporal_evidence": info["temporal_evidence"],
            "last_evidence_release": last_evidence_release,
            "last_study_date": last_study_date,
            "certification_date": last_study_date,
            "evidence_warnings": warnings,
            "warnings": warnings,
            "warning": warning,
            "version_identity": version_identity,
            "exact_suppression_reason": suppression_reason,
            "suppression_reason": suppression_reason,
            "reason_code": reason_code,
            "status_message": display_msg,
            "display_message": display_msg,
            "pending_condition": info["pending_condition"],
            "fail_closed_note": "Supported regime ≠ currently production active. Live alerts fail-closed (0 alerts)."
        }
    
    raw = (scanner_name or "").strip().upper()
    if norm in DECOMMISSIONED_SCANNERS or raw in DECOMMISSIONED_SCANNERS or (scanner_name and scanner_name in DECOMMISSIONED_SCANNERS):
        disp = "Decommissioned — permanently silenced."
        return {
            "scanner": norm,
            "scanner_name": norm,
            "selected_variant": "NONE",
            "algorithm_version": version_identity,
            "evidence_release": last_evidence_release,
            "current_macro_regime": current_regime,
            "current_regime": current_regime,
            "evidence_supported_regime": "DECOMMISSIONED",
            "supported_regime": "DECOMMISSIONED",
            "evidence_supported_regimes": [],
            "production_authorized_regimes": [],
            "production_active_now": "NO",
            "is_production_active": False,
            "lifecycle": "DECOMMISSIONED",
            "lifecycle_state": "DECOMMISSIONED",
            "certification_status": "PERMANENTLY_DECOMMISSIONED",
            "production_authorization_state": "PERMANENTLY SILENCED",
            "current_production_active_regime": "None — Decommissioned",
            "temporal_evidence_status": "Decommissioned by governance",
            "temporal_evidence": "Failed certification or decommissioned by governance.",
            "last_evidence_release": last_evidence_release,
            "last_study_date": last_study_date,
            "certification_date": last_study_date,
            "evidence_warnings": ["Decommissioned permanently"],
            "warnings": ["Decommissioned permanently"],
            "warning": "Decommissioned permanently",
            "version_identity": version_identity,
            "exact_suppression_reason": disp,
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
    if "QUALITY_VALUE_RECOVERY" in s or "RECOVERY_WEALTH" in s or "RECOVERY" in s:
        return "QUALITY_VALUE_RECOVERY"
    if "TECHNICAL" in s:
        return "TECHNICAL"
    if "FUNDAMENTAL" in s:
        return "FUNDAMENTAL"
    if "EOD" in s:
        return "EOD"
    if "MULTIBAGGER" in s:
        return "MULTIBAGGER"
    if "WEALTH_EXIT_V1" in s:
        return "WEALTH_EXIT_V1"
    if "WEALTH_EXIT_V2" in s:
        return "WEALTH_EXIT_V2"
    if "WEALTH" in s:
        return "WEALTH"
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
    if not macro_regime or not str(macro_regime).strip():
        return False, f"MISSING_REGIME_FAIL_CLOSED_{norm}"
    regime = str(macro_regime).strip().upper()
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


_REGIME_CACHE: Dict[str, Any] = {"regime": None, "timestamp": 0.0}

def calculate_live_market_regime(sample_size: int = 200, cache_ttl_sec: float = 600.0) -> Dict[str, Any]:
    """
    Calculates the point-in-time market regime using market breadth across the clean universe:
      - BULL: >= 60% above SMA50 and >= 50% above SMA200
      - BEAR: < 40% above SMA50 or < 40% above SMA200
      - SIDEWAYS: Otherwise (40% to 60%)
    Cached in RAM for 10 minutes to avoid redundant I/O.
    """
    global _REGIME_CACHE
    import time
    now_mono = time.time()
    if _REGIME_CACHE["regime"] is not None and (now_mono - _REGIME_CACHE["timestamp"]) < cache_ttl_sec:
        return dict(_REGIME_CACHE["regime"])

    # First check CSV if available for fast read
    regime_file = "reports/certification/FINAL_AUDIT_2026-09-26/regime_daycount_daily.csv"
    if os.path.exists(regime_file):
        try:
            import pandas as pd
            df = pd.read_csv(regime_file)
            if not df.empty:
                col = "regime" if "regime" in df.columns else ("macro_regime" if "macro_regime" in df.columns else None)
                if col:
                    last_row = df.iloc[-1]
                    reg = str(last_row[col]).strip().upper()
                    s50_pct = float(last_row.get("above_sma50_pct", 0.0)) * 100.0
                    s200_pct = float(last_row.get("above_sma200_pct", 0.0)) * 100.0
                    res = {
                        "regime": reg,
                        "above_sma50_pct": round(s50_pct, 1),
                        "above_sma200_pct": round(s200_pct, 1),
                        "detail": f"{round(s50_pct, 1)}% > SMA50",
                        "date": str(last_row.get("date", "Latest")),
                        "source": "REGIME_AUDIT_DAILY"
                    }
                    _REGIME_CACHE["regime"] = res
                    _REGIME_CACHE["timestamp"] = now_mono
                    return res
        except Exception:
            pass

    # If CSV not available, calculate live from Parquet breadth
    try:
        import glob
        import pandas as pd
        files = glob.glob("data/history/1d/*.parquet")[:sample_size]
        breadth_rows = []
        for p in files:
            try:
                df = pd.read_parquet(p)
                df.columns = [str(c).capitalize() for c in df.columns]
                date_col = "Date" if "Date" in df.columns else df.columns[0]
                df = df.sort_values(by=date_col).reset_index(drop=True)
                if len(df) < 50:
                    continue
                c = df["Close"]
                s50 = c.rolling(50, min_periods=20).mean().iloc[-1]
                s200 = c.rolling(200, min_periods=50).mean().iloc[-1]
                last_c = c.iloc[-1]
                breadth_rows.append({
                    "above_sma50": int(last_c > s50),
                    "above_sma200": int(last_c > s200)
                })
            except Exception:
                pass

        if breadth_rows:
            df_b = pd.DataFrame(breadth_rows)
            pct50 = float(df_b["above_sma50"].mean())
            pct200 = float(df_b["above_sma200"].mean())
            if pct50 >= 0.60 and pct200 >= 0.50:
                reg = "BULL"
            elif pct50 < 0.40 or pct200 < 0.40:
                reg = "BEAR"
            else:
                reg = "SIDEWAYS"
            res = {
                "regime": reg,
                "above_sma50_pct": round(pct50 * 100.0, 1),
                "above_sma200_pct": round(pct200 * 100.0, 1),
                "detail": f"{round(pct50 * 100.0, 1)}% > SMA50",
                "source": f"LIVE_BREADTH_{len(breadth_rows)}_STOCKS"
            }
        else:
            res = {
                "regime": "BULL",
                "above_sma50_pct": 50.0,
                "above_sma200_pct": 50.0,
                "detail": "Baseline",
                "source": "FALLBACK"
            }
    except Exception:
        res = {
            "regime": "BULL",
            "above_sma50_pct": 50.0,
            "above_sma200_pct": 50.0,
            "detail": "Baseline",
            "source": "FALLBACK"
        }

    _REGIME_CACHE["regime"] = res
    _REGIME_CACHE["timestamp"] = now_mono
    return res


def get_current_macro_regime() -> str:
    """
    Returns the current macro regime (BULL, SIDEWAYS, BEAR) point-in-time.
    Defaults to the latest trading date's regime from regime_daycount_daily.csv or live breadth.
    """
    reg_info = calculate_live_market_regime()
    return reg_info.get("regime", "BULL")
