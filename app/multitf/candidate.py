# =====================================================================================
# app/multitf/candidate.py
# MULTI_TF V2 — Candidate & Payload Building
#
# Responsibility: Translates internal evaluation results into flat dictionaries suitable
# for (a) the mtf_v2_watchlist database and (b) the common OpportunityManager.
# =====================================================================================

import logging
from datetime import datetime
from typing import Dict, Any, Optional

from multitf.data import MultitfDataBundle
from multitf.consolidation import ConsolidationResult
from multitf.pressure import PressureResult
from multitf.confluence import ConfluenceResult
from multitf.state import MtfSubstate, to_canonical

logger = logging.getLogger("multitf.candidate")


def build_watchlist_candidate(
    bundle: MultitfDataBundle,
    consolidation: ConsolidationResult,
    ctx_1h: Dict[str, Any],
    ctx_30m: Dict[str, Any],
    market_ctx: Dict[str, Any],
    ist_now: datetime
) -> Dict[str, Any]:
    """
    Builds the flat dictionary for inserting a NEW consolidation into mtf_v2_watchlist.
    """
    substate = MtfSubstate.WATCHING
    canonical_state = to_canonical(substate).value

    # Extract provenance
    prov_1h = bundle.prov_1h.to_dict() if bundle.prov_1h else {}
    prov_30m = bundle.prov_30m.to_dict() if bundle.prov_30m else {}
    prov_15m = bundle.prov_15m.to_dict() if bundle.prov_15m else {}
    prov_5m = bundle.prov_5m.to_dict() if bundle.prov_5m else {}

    return {
        "symbol": consolidation.symbol,
        "box_id": consolidation.box_id,
        "state": canonical_state,
        "mtf_substate": substate,
        
        "consolidation_start_ts": consolidation.start_ts,
        "consolidation_end_ts": consolidation.end_ts,
        "consolidation_bars": consolidation.bars_count,
        "consolidation_sessions": consolidation.sessions_count,
        
        "box_high": consolidation.box_high,
        "box_low": consolidation.box_low,
        "box_mid": consolidation.box_mid,
        "box_value_center": consolidation.box_value_center,
        "hard_high": consolidation.hard_high,
        "hard_low": consolidation.hard_low,
        "box_width_pct": consolidation.box_width_pct,
        "box_width_atr": consolidation.box_width_atr,
        "box_occupancy": consolidation.box_occupancy,
        
        "resistance_test_count": consolidation.resistance_test_count,
        "higher_low_score": consolidation.score_hl,
        "compression_score": consolidation.score_compression,
        "setup_score": consolidation.setup_score,
        "last_confirmed_pivot_level": consolidation.last_confirmed_pivot_level,
        "last_confirmed_pivot_ts": consolidation.last_confirmed_pivot_ts,
        
        "context_1h_score": ctx_1h.get("score", 0),
        "context_30m_score": ctx_30m.get("score", 0),
        "market_regime": market_ctx.get("regime", "UNKNOWN"),
        "relative_strength": 0.0, # Placeholder for future RS
        
        "data_source_1h": prov_1h.get("source", ""),
        "data_source_30m": prov_30m.get("source", ""),
        "data_source_15m": prov_15m.get("source", ""),
        "data_source_5m": prov_5m.get("source", ""),
        "candle_ts_1h": prov_1h.get("last_candle_ts"),
        "candle_ts_30m": prov_30m.get("last_candle_ts"),
        "candle_ts_15m": prov_15m.get("last_candle_ts"),
        "candle_ts_5m": prov_5m.get("last_candle_ts"),
        
        "created_at": ist_now,
        "updated_at": ist_now,
        "last_evaluated_at": ist_now
    }


def build_confirmed_payload(
    bundle: MultitfDataBundle,
    consolidation: ConsolidationResult,
    pressure: PressureResult,
    confluence: Optional[ConfluenceResult],
    sl_target: Dict[str, Any],
    ist_now: datetime,
    # [V3] Optional V3 fields
    alert_message: str = "",
    severity: str = "",
    breakout_strength=None
) -> Dict[str, Any]:
    """
    Builds the fully hydrated payload per section §37 of the architecture spec,
    ready for submission to OpportunityManager.
    [V3]: Now includes base_score, breakout_score, severity, rich alert message.
    """
    # Requires strictly validated data from the closed 5m bar that triggered confirmation
    c_bar = bundle.df_5m_closed.iloc[-1]

    # V3: conviction_score is now the base quality score (primary); keep legacy field populated
    conviction = consolidation.setup_score if consolidation.setup_score > 0 else (confluence.total_score if confluence else 0)
    components = confluence.to_dict()["components"] if confluence else {}

    payload = {
        "symbol": bundle.symbol,
        "scanner_name": "MULTI_TF",
        "scanner_version": "3.0",
        "signal_type": "BREAKOUT",
        "tf_primary": "15m",
        "tf_trigger": "5m",
        "timestamp": ist_now.isoformat(),

        # Core execution pricing
        "close_price": float(c_bar["Close"]),
        "trigger_price": float(c_bar["Close"]),
        "volume": int(c_bar["Volume"]),

        # SL/Target mapping
        "stop_loss": sl_target.get("stop_loss", 0.0),
        "target": sl_target.get("target_1", 0.0),
        "target_1": sl_target.get("target_1", 0.0),
        "target_2": sl_target.get("target_2", 0.0),
        "target_3": sl_target.get("target_3", 0.0),
        "rr_ratio": sl_target.get("rr_ratio", 0.0),
        "risk_pct": sl_target.get("risk_pct", 0.0),
        "sl_basis": sl_target.get("sl_basis", "UNKNOWN"),
        "target_basis": sl_target.get("target_basis", "UNKNOWN"),

        # [V3] Dual-Score Conviction
        "conviction_score": conviction,
        "base_score": consolidation.setup_score,
        "base_rating": consolidation.base_rating_label,
        "breakout_score": breakout_strength.breakout_score if breakout_strength else 0,
        "breakout_rating": breakout_strength.breakout_rating_label if breakout_strength else "",
        "severity": severity,
        "has_higher_lows": consolidation.has_higher_lows,
        "compression_ratio": consolidation.compression_ratio,
        "rvol_label": breakout_strength.rvol_label if breakout_strength else "",
        "velocity_label": breakout_strength.velocity_label if breakout_strength else "",
        "components": components,

        # Setup Geometry (for UI rendering and telemetry)
        "box_high": consolidation.box_high,
        "box_low": consolidation.box_low,
        "box_id": consolidation.box_id,
        "consolidation_bars": consolidation.bars_count,
        "distance_to_box_high": pressure.distance_to_box_high,
        "volume_ratio": pressure.volume_ratio,
        "range_ratio": pressure.range_ratio,

        # [V3] Rich alert message for Telegram/push
        "alert_message": alert_message,

        # Mandatory Provenance
        "provenance": {
            "15m": bundle.prov_15m.to_dict() if bundle.prov_15m else {},
            "5m": bundle.prov_5m.to_dict() if bundle.prov_5m else {},
            "1h": bundle.prov_1h.to_dict() if bundle.prov_1h else {},
            "30m": bundle.prov_30m.to_dict() if bundle.prov_30m else {}
        }
    }

    return payload


