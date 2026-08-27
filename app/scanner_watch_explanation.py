# =====================================================================================
# app/scanner_watch_explanation.py
# V2 SCANNER WATCH EXPLANATION BUILDERS
# =====================================================================================
#
# Each function is a translation layer that converts a normalized evaluation payload
# (produced by the existing scanner) into a standardised WatchExplanation.
#
# PHASE 1 CONTRACT — CRITICAL:
#   These builders describe EXISTING scanner conditions as they are computed today.
#   They do NOT introduce new thresholds or new scanner rules.
#   Full individual scanner redesigns are Phase 2.
#   The builders simply classify current scanner outputs into the V2 checklist schema.
#
# Each builder:
#   1. Reads fields from a normalized `data: dict` provided by the scanner.
#   2. Calls evaluate_checklist() from signal_contract for each criterion.
#      — Never builds cleared/pending/failed lists manually.
#   3. Calls build_primary_blocker() for the ranked blocker.
#   4. Returns a WatchExplanation (scanner-agnostic output used by dashboard & tracker).
#
# Health delta computation requires an optional `prev_data: dict` (previous snapshot).
# If prev_data is None, health_status defaults to STABLE.
#
# Expected `data` dict keys are documented per builder below.
# All keys are optional; missing keys are handled gracefully as NOT_APPLICABLE
# or PENDING where semantically correct.
# =====================================================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from signal_contract import (
    BlockerCandidate,
    ChecklistStatus,
    WatchExplanation,
    build_primary_blocker,
    evaluate_checklist,
    partition_checklists,
)

logger = logging.getLogger("scanner_watch_explanation")

# -------------------------------------------------------------------------------------
# INTERNAL HELPERS
# -------------------------------------------------------------------------------------

def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _compute_health(
    current: Dict[str, Any],
    prev: Optional[Dict[str, Any]],
    quality_key: str = "quality_score",
    distance_key: str = "distance_to_trigger_pct",
    volume_key: str = "volume_ratio",
    rs_key: str = "rs_rating",
) -> Tuple[str, str, str]:
    """
    Returns (health_status, health_reason, last_change_summary).

    Rules:
      IMPROVING    — quality up AND/OR distance narrowing AND/OR volume up
      DETERIORATING— quality down OR distance widening OR RS declining
      STABLE       — no meaningful change
      INVALIDATED  — structure failure detected (structural_failure=True in current)

    If prev is None, returns STABLE with no reason (first observation).
    """
    if current.get("structural_failure"):
        return "INVALIDATED", "Structural failure detected.", ""

    if prev is None:
        return "STABLE", "Initial Watch observation.", ""

    changes: List[str] = []
    positive = 0
    negative = 0

    q_curr = _safe_float(current.get(quality_key))
    q_prev = _safe_float(prev.get(quality_key))
    if q_curr is not None and q_prev is not None:
        delta = q_curr - q_prev
        if abs(delta) >= 1.0:
            direction = "↑" if delta > 0 else "↓"
            changes.append(f"Quality {q_prev:.0f} → {q_curr:.0f} {direction}")
            positive += 1 if delta > 0 else 0
            negative += 1 if delta < 0 else 0

    d_curr = _safe_float(current.get(distance_key))
    d_prev = _safe_float(prev.get(distance_key))
    if d_curr is not None and d_prev is not None:
        delta = d_prev - d_curr  # positive = narrowing = good
        if abs(delta) >= 0.1:
            if delta > 0:
                changes.append(f"distance narrowed {d_prev:.1f}% → {d_curr:.1f}% ↑")
                positive += 1
            else:
                changes.append(f"distance widened {d_prev:.1f}% → {d_curr:.1f}% ↓")
                negative += 1

    v_curr = _safe_float(current.get(volume_key))
    v_prev = _safe_float(prev.get(volume_key))
    if v_curr is not None and v_prev is not None:
        delta = v_curr - v_prev
        if abs(delta) >= 0.05:
            direction = "↑" if delta > 0 else "↓"
            changes.append(f"volume {v_prev:.2f}x → {v_curr:.2f}x {direction}")
            positive += 1 if delta > 0 else 0
            negative += 1 if delta < 0 else 0

    rs_curr = _safe_float(current.get(rs_key))
    rs_prev = _safe_float(prev.get(rs_key))
    if rs_curr is not None and rs_prev is not None:
        delta = rs_curr - rs_prev
        if abs(delta) >= 2:
            direction = "↑" if delta > 0 else "↓"
            changes.append(f"RS {rs_prev:.0f} → {rs_curr:.0f} {direction}")
            positive += 1 if delta > 0 else 0
            negative += 1 if delta < 0 else 0

    if positive > negative and positive >= 1:
        health = "IMPROVING"
    elif negative > positive and negative >= 1:
        health = "DETERIORATING"
    else:
        health = "STABLE"

    reason = "; ".join(changes) + "." if changes else "No significant change since last scan."
    summary = reason  # last_change_summary = same single-sentence delta for Phase 1
    return health, reason, summary


def _result_dict(results):
    """Convenience: dict of criterion → (status, blocker_candidate)."""
    return results


# -------------------------------------------------------------------------------------
# EOD SCANNER
# -------------------------------------------------------------------------------------

def build_eod_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for an EOD scanner Watch candidate.

    Expected data keys:
        trend_valid (bool)          — price above SMA50, SMA50 > SMA200
        structure_valid (bool)      — close > resistance, tests >= 1
        resistance_level (float)    — key resistance level (immutable trigger anchor)
        cmp (float)                 — current market price
        atr (float)                 — ATR of the stock
        rs_valid (bool)             — RS rating passes threshold
        rs_rating (float)           — RS percentile
        sector_valid (bool)         — sector rank passes
        sector_rank (int)
        base_valid (bool)           — base compression check
        distance_to_trigger_pct (float)
        distance_to_trigger_atr (float)  — (resistance - cmp) / atr. Must be >= 0.
        extension_from_base_atr (float)
        data_valid (bool)           — result of validate_signal_data()
        risk_valid (bool)           — R:R >= threshold, gap <= threshold
        reward_risk_ratio (float)
        volume_ratio (float)        — current volume ratio (may be intraday / partial)
        quality_score (float)
        stop_loss (float)
        target_1 (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    # TREND
    results["TREND"] = evaluate_checklist("TREND", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("trend_valid")
        else (ChecklistStatus.FAILED, None, None, "Trend not valid (SMA50/SMA200 alignment)")
    ))

    # STRUCTURE
    results["STRUCTURE"] = evaluate_checklist("STRUCTURE", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("structure_valid")
        else (ChecklistStatus.FAILED, None, None, "Structure not valid (close vs resistance)")
    ))

    # RS
    results["RS"] = evaluate_checklist("RS", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("rs_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("rs_rating")),
              data.get("rs_threshold"),
              "RS below threshold")
    ))

    # SECTOR
    results["SECTOR"] = evaluate_checklist("SECTOR", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_valid")
        else (ChecklistStatus.FAILED, None, None, "Sector rank weak")
    ))

    # BASE
    results["BASE"] = evaluate_checklist("BASE", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("base_valid")
        else (ChecklistStatus.WARNING if data.get("base_near_valid") else ChecklistStatus.FAILED,
              _safe_float(data.get("extension_from_base_atr")),
              1.5,
              "Base extension elevated" if data.get("base_near_valid") else "Base not compressed")
    ))

    # DATA
    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    # RISK
    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.5,
              "R:R below threshold or gap too wide")
    ))

    # BREAKOUT — always PENDING in Watch state (trigger not yet occurred)
    cmp = _safe_float(data.get("cmp"))
    resistance = _safe_float(data.get("resistance_level"))
    gap_pct = _safe_float(data.get("distance_to_trigger_pct"))
    results["BREAKOUT"] = evaluate_checklist("BREAKOUT", lambda: (
        ChecklistStatus.PENDING,
        cmp,
        resistance,
        "Trigger Blocker",
    ))

    # VOLUME — pending pre-breakout (confirmation volume happens at breakout bar)
    vol_ratio = _safe_float(data.get("volume_ratio"))
    results["VOLUME"] = evaluate_checklist("VOLUME", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if vol_ratio and vol_ratio >= (data.get("volume_threshold") or 1.3)
        else (
            ChecklistStatus.PENDING,
            vol_ratio,
            data.get("volume_threshold") or 1.3,
            "Confirmation volume pending at breakout",
        )
    ))

    partitioned = partition_checklists(results)

    # Blockers
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    # next_required_event
    tgt = f"₹{resistance:.2f}" if resistance else "{trigger}"
    vol_thr = data.get("volume_threshold") or 1.3
    next_event = f"Daily close > {tgt} with volume >= {vol_thr:.1f}x"

    health, health_reason, last_change = _compute_health(data, prev_data)

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# PULLBACK SCANNER
# -------------------------------------------------------------------------------------

def build_pullback_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for a Pullback scanner Watch candidate.

    Expected data keys:
        impulse_valid (bool)         — strong prior impulse
        support_holding (bool)       — price at/above AVWAP or EMA20 support
        support_level (float)        — support price level
        cmp (float)
        rs_valid (bool)              — 6M RS >= threshold
        rs_rating (float)
        sector_valid (bool)
        data_valid (bool)
        risk_valid (bool)
        volume_ratio (float)         — resumption volume (pending)
        volume_threshold (float)     — default 1.3
        quality_score (float)
        distance_to_trigger_pct (float)
        reward_risk_ratio (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    results["IMPULSE"] = evaluate_checklist("IMPULSE", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("impulse_valid")
        else (ChecklistStatus.FAILED, None, None, "Prior impulse not strong enough")
    ))

    results["SUPPORT"] = evaluate_checklist("SUPPORT", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("support_holding")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("cmp")),
              _safe_float(data.get("support_level")),
              "Support not holding")
    ))

    results["RS"] = evaluate_checklist("RS", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("rs_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("rs_rating")),
              data.get("rs_threshold"),
              "RS below 6M threshold")
    ))

    results["SECTOR"] = evaluate_checklist("SECTOR", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_valid")
        else (ChecklistStatus.FAILED, None, None, "Sector rank weak")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Resumption volume — pending
    vol_ratio = _safe_float(data.get("volume_ratio"))
    vol_thr = data.get("volume_threshold") or 1.3
    results["VOLUME"] = evaluate_checklist("VOLUME", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if vol_ratio and vol_ratio >= vol_thr
        else (ChecklistStatus.PENDING, vol_ratio, vol_thr, "Resumption volume pending")
    ))

    # Candle confirmation — pending
    results["CANDLE"] = evaluate_checklist("CANDLE", lambda: (
        (ChecklistStatus.PENDING, None, None, "Confirmation candle pending")
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    support = _safe_float(data.get("support_level"))
    support_str = f"₹{support:.2f}" if support else "{support}"
    next_event = f"Support holds at {support_str} + resumption volume >= {vol_thr:.1f}x"

    health, health_reason, last_change = _compute_health(data, prev_data)

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# REVERSAL SCANNER
# -------------------------------------------------------------------------------------

def build_reversal_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for a Reversal scanner Watch candidate.

    Expected data keys:
        oversold_valid (bool)        — RSI/price vs lows indicates oversold
        support_holding (bool)       — key support holding
        no_lower_low (bool)          — no lower low formed (structural integrity)
        structural_failure (bool)    — hard reject: lower low formed
        cmp (float)
        trigger_level (float)        — higher-high confirmation level
        rs_valid (bool)
        sector_valid (bool)
        data_valid (bool)
        risk_valid (bool)
        volume_ratio (float)
        quality_score (float)
        reward_risk_ratio (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    # Hard structural gate — lower low = immediate FAILED
    no_lower_low = data.get("no_lower_low", True)
    results["NO_LOWER_LOW"] = evaluate_checklist("NO_LOWER_LOW", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if no_lower_low
        else (ChecklistStatus.FAILED, None, None, "Lower low formed — hard reject")
    ))

    results["OVERSOLD"] = evaluate_checklist("OVERSOLD", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("oversold_valid")
        else (ChecklistStatus.FAILED, None, None, "Not in oversold zone")
    ))

    results["SUPPORT"] = evaluate_checklist("SUPPORT", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("support_holding")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("cmp")),
              _safe_float(data.get("support_level")),
              "Support not holding")
    ))

    results["RS"] = evaluate_checklist("RS", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("rs_valid", True)   # RS may be lenient for Reversal
        else (ChecklistStatus.WARNING,
              _safe_float(data.get("rs_rating")),
              data.get("rs_threshold"),
              "RS weak but not disqualifying for reversal")
    ))

    results["SECTOR"] = evaluate_checklist("SECTOR", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_valid", True)
        else (ChecklistStatus.WARNING, None, None, "Sector rank weak")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Higher-high confirmation — always PENDING in Watch state
    trigger = _safe_float(data.get("trigger_level"))
    results["HIGHER_HIGH"] = evaluate_checklist("HIGHER_HIGH", lambda: (
        ChecklistStatus.PENDING,
        _safe_float(data.get("cmp")),
        trigger,
        "Trigger Blocker",
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    tgt = f"₹{trigger:.2f}" if trigger else "{trigger}"
    next_event = f"Higher-high confirmation above {tgt}"

    health, health_reason, last_change = _compute_health(data, prev_data)

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# MULTI-TF SCANNER
# -------------------------------------------------------------------------------------

def build_multi_tf_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for a Multi-Timeframe scanner Watch candidate.

    Expected data keys:
        weekly_trend_valid (bool)    — weekly trend bullish
        daily_trend_valid (bool)     — daily trend bullish
        rs_valid (bool)
        sector_valid (bool)
        data_valid (bool)
        risk_valid (bool)
        extension_valid (bool)       — within EMA20 + 2*ATR (not extended)
        hourly_trigger_level (float)
        cmp (float)
        quality_score (float)
        volume_ratio (float)
        reward_risk_ratio (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    results["WEEKLY_TREND"] = evaluate_checklist("WEEKLY_TREND", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("weekly_trend_valid")
        else (ChecklistStatus.FAILED, None, None, "Weekly trend not bullish")
    ))

    results["DAILY_TREND"] = evaluate_checklist("DAILY_TREND", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("daily_trend_valid")
        else (ChecklistStatus.FAILED, None, None, "Daily trend not bullish")
    ))

    results["RS"] = evaluate_checklist("RS", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("rs_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("rs_rating")),
              data.get("rs_threshold"),
              "RS below threshold")
    ))

    results["SECTOR"] = evaluate_checklist("SECTOR", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_valid")
        else (ChecklistStatus.FAILED, None, None, "Sector rank weak")
    ))

    # Extension guard — CMP too far above EMA20
    results["EXTENSION"] = evaluate_checklist("EXTENSION", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("extension_valid", True)
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("extension_from_base_atr")),
              2.0,
              "Price extended too far above EMA20 — risk blocker")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Hourly trigger — always PENDING in Watch state
    hourly_trig = _safe_float(data.get("hourly_trigger_level"))
    results["HOURLY_TRIGGER"] = evaluate_checklist("HOURLY_TRIGGER", lambda: (
        ChecklistStatus.PENDING,
        _safe_float(data.get("cmp")),
        hourly_trig,
        "Trigger Blocker",
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    tgt = f"₹{hourly_trig:.2f}" if hourly_trig else "{trigger}"
    next_event = f"Hourly breakout above {tgt}"

    health, health_reason, last_change = _compute_health(data, prev_data)

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# ACCUMULATION SCANNER
# -------------------------------------------------------------------------------------

def build_accumulation_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for an Accumulation scanner Watch candidate.

    Expected data keys:
        delivery_5d_valid (bool)     — 5D delivery trend strong
        delivery_20d_valid (bool)    — 20D delivery trend strong
        fii_dii_valid (bool)         — FII/DII institutional flow present
        absorption_valid (bool)      — absorption pattern (vs distribution)
        data_valid (bool)
        risk_valid (bool)
        sector_valid (bool)
        trigger_level (float)        — price expansion trigger
        cmp (float)
        quality_score (float)
        volume_ratio (float)
        reward_risk_ratio (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    results["DELIVERY_5D"] = evaluate_checklist("DELIVERY_5D", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("delivery_5d_valid")
        else (ChecklistStatus.FAILED, None, None, "5D delivery trend weak")
    ))

    results["DELIVERY_20D"] = evaluate_checklist("DELIVERY_20D", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("delivery_20d_valid")
        else (ChecklistStatus.FAILED, None, None, "20D delivery trend weak")
    ))

    results["FII_DII_FLOW"] = evaluate_checklist("FII_DII_FLOW", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("fii_dii_valid")
        else (
            ChecklistStatus.WARNING if data.get("fii_dii_neutral") else ChecklistStatus.FAILED,
            None, None,
            "Institutional flow absent or insufficient",
        )
    ))

    results["ABSORPTION"] = evaluate_checklist("ABSORPTION", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("absorption_valid")
        else (ChecklistStatus.FAILED, None, None, "Distribution pattern detected")
    ))

    results["SECTOR"] = evaluate_checklist("SECTOR", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_valid", True)
        else (ChecklistStatus.WARNING, None, None, "Sector rank weak")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Price expansion — PENDING
    trig = _safe_float(data.get("trigger_level"))
    results["PRICE_EXPANSION"] = evaluate_checklist("PRICE_EXPANSION", lambda: (
        ChecklistStatus.PENDING,
        _safe_float(data.get("cmp")),
        trig,
        "Trigger Blocker",
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    tgt = f"₹{trig:.2f}" if trig else "{trigger}"
    next_event = f"Price expansion above {tgt} with institutional flow"

    health, health_reason, last_change = _compute_health(
        data, prev_data,
        volume_key="volume_ratio",
    )

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# MULTIBAGGER SCANNER
# -------------------------------------------------------------------------------------

def build_multibagger_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for a Multibagger scanner Watch candidate.

    Expected data keys:
        cfo_pat_valid (bool)         — CFO/PAT >= 0.8 (cash-flow quality gate)
        cfo_pat_ratio (float)
        fcf_valid (bool)             — 3Y FCF > 0
        piotroski_valid (bool)       — Piotroski score >= 6
        piotroski_score (float)
        pledge_valid (bool)          — Promoter pledge <= 15%
        pledge_pct (float)
        data_valid (bool)
        risk_valid (bool)
        trigger_level (float)        — valuation entry zone price
        cmp (float)
        quality_score (float)
        reward_risk_ratio (float)
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    results["CFO_PAT"] = evaluate_checklist("CFO_PAT", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("cfo_pat_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("cfo_pat_ratio")),
              0.8,
              "Cash-flow quality gate: CFO/PAT below 0.8")
    ))

    results["FCF"] = evaluate_checklist("FCF", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("fcf_valid")
        else (ChecklistStatus.FAILED, None, None, "3Y Free Cash Flow not positive")
    ))

    results["PIOTROSKI"] = evaluate_checklist("PIOTROSKI", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("piotroski_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("piotroski_score")),
              6.0,
              "Piotroski score below 6")
    ))

    results["PLEDGE"] = evaluate_checklist("PLEDGE", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("pledge_valid")
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("pledge_pct")),
              15.0,
              "Promoter pledge exceeds 15%")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid", True)
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Valuation zone entry — PENDING
    trig = _safe_float(data.get("trigger_level"))
    cmp = _safe_float(data.get("cmp"))
    results["VALUATION_ZONE"] = evaluate_checklist("VALUATION_ZONE", lambda: (
        ChecklistStatus.PENDING,
        cmp,
        trig,
        "Trigger Blocker",
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    tgt = f"₹{trig:.2f}" if trig else "{trigger}"
    next_event = f"Valuation entry zone <= {tgt}"

    health, health_reason, last_change = _compute_health(
        data, prev_data,
        distance_key="distance_to_trigger_pct",
        volume_key="volume_ratio",
    )

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# WEALTH ENGINE SCANNER
# -------------------------------------------------------------------------------------

def build_wealth_watch_explanation(
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Builds WatchExplanation for a Wealth Engine scanner Watch candidate.

    Expected data keys:
        business_quality_valid (bool)  — business quality score passes
        sector_cap_valid (bool)        — sector allocation < 25%
        sector_allocation_pct (float)  — current sector allocation
        portfolio_fit_valid (bool)     — portfolio-level fit passes
        data_valid (bool)
        risk_valid (bool)
        trigger_level (float)          — price trigger for portfolio allocation
        cmp (float)
        quality_score (float)
        reward_risk_ratio (float)
        action_type (str)              — BUY / ADD_ON / PYRAMID / HOLD / WAIT / SELL_REVIEW
    """
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]] = {}

    results["BUSINESS_QUALITY"] = evaluate_checklist("BUSINESS_QUALITY", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("business_quality_valid")
        else (ChecklistStatus.FAILED, None, None, "Business quality score insufficient")
    ))

    sector_alloc = _safe_float(data.get("sector_allocation_pct"))
    results["SECTOR_CAP"] = evaluate_checklist("SECTOR_CAP", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("sector_cap_valid", True)
        else (ChecklistStatus.FAILED,
              sector_alloc,
              25.0,
              "Sector allocation exceeds 25% cap")
    ))

    results["PORTFOLIO_FIT"] = evaluate_checklist("PORTFOLIO_FIT", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("portfolio_fit_valid", True)
        else (ChecklistStatus.FAILED, None, None, "Portfolio-level fit not satisfied")
    ))

    results["DATA"] = evaluate_checklist("DATA", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("data_valid")
        else (ChecklistStatus.FAILED, None, None, "Data validation failed")
    ))

    results["RISK"] = evaluate_checklist("RISK", lambda: (
        (ChecklistStatus.CLEARED, None, None, None)
        if data.get("risk_valid", True)
        else (ChecklistStatus.FAILED,
              _safe_float(data.get("reward_risk_ratio")),
              2.0,
              "R:R insufficient")
    ))

    # Portfolio allocation price trigger — PENDING
    trig = _safe_float(data.get("trigger_level"))
    results["PORTFOLIO_TRIGGER"] = evaluate_checklist("PORTFOLIO_TRIGGER", lambda: (
        ChecklistStatus.PENDING,
        _safe_float(data.get("cmp")),
        trig,
        "Trigger Blocker",
    ))

    partitioned = partition_checklists(results)
    blockers = [bc for _, bc in results.values() if bc is not None]
    primary = build_primary_blocker(blockers)
    primary_type = primary.get("type") if primary else None

    tgt = f"₹{trig:.2f}" if trig else "{trigger}"
    action = data.get("action_type", "BUY")
    next_event = f"Portfolio allocation trigger ({action}) at {tgt}"

    health, health_reason, last_change = _compute_health(
        data, prev_data,
        distance_key="distance_to_trigger_pct",
    )

    return WatchExplanation(
        cleared=partitioned["cleared"],
        pending=partitioned["pending"],
        failed=partitioned["failed"],
        warning=partitioned["warning"],
        not_applicable=partitioned["not_applicable"],
        primary_blocker=primary,
        primary_blocker_type=primary_type,
        next_required_event=next_event,
        health_status=health,
        health_reason=health_reason,
        last_change_summary=last_change,
    )


# -------------------------------------------------------------------------------------
# ROUTER
# -------------------------------------------------------------------------------------

_BUILDER_MAP = {
    "EOD":          build_eod_watch_explanation,
    "PULLBACK":     build_pullback_watch_explanation,
    "REVERSAL":     build_reversal_watch_explanation,
    "MULTI_TF":     build_multi_tf_watch_explanation,
    "ACCUMULATION": build_accumulation_watch_explanation,
    "MULTIBAGGER":  build_multibagger_watch_explanation,
    "WEALTH":       build_wealth_watch_explanation,
}


def build_watch_explanation(
    scanner_name: str,
    data: Dict[str, Any],
    prev_data: Optional[Dict[str, Any]] = None,
) -> WatchExplanation:
    """
    Routes to the correct scanner-specific builder by scanner_name.

    scanner_name must match one of: EOD, PULLBACK, REVERSAL, MULTI_TF,
                                    ACCUMULATION, MULTIBAGGER, WEALTH

    Returns a WatchExplanation on success.
    Raises ValueError for unknown scanner names.
    """
    builder = _BUILDER_MAP.get(scanner_name.upper())
    if builder is None:
        raise ValueError(
            f"[scanner_watch_explanation] Unknown scanner: {scanner_name!r}. "
            f"Valid scanners: {list(_BUILDER_MAP.keys())}"
        )
    return builder(data, prev_data)
