"""
app/accumulation/exit_evaluator.py — Post-Market Close Setup Exit Evaluator for ACCUMULATION_SCANNER_V1.
Executes the N+1 decision tree, same-bar STOP_FIRST precedence, milestone preservation,
and method-specific entry activation and gap classification rules.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class AccumulationExitEvaluator:
    """Post-Close Exit Evaluator & Milestone Engine for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def evaluate_bar(
        setup: Dict[str, Any],
        bar: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates a single completed daily OHLC bar against an active trade setup.
        Returns an updated dict with state modifications.
        """
        status = setup.get("status", "ACTIVE_SETUP")
        entry_type = setup.get("entry_type", "ZONE_MIDPOINT")
        entry_zone_low = float(setup["entry_zone_low"])
        entry_zone_high = float(setup["entry_zone_high"])
        entry_trigger_level = float(setup["entry_trigger_level"])
        preferred_entry = float(setup["preferred_entry"])
        stop_loss = float(setup["stop_loss"])
        target_1 = float(setup["target_1"])
        target_2 = float(setup["target_2"])
        target_3 = float(setup["target_3"])
        best_target_reached = setup.get("best_target_reached")

        bar_open = float(bar.get("Open") if "Open" in bar else bar.get("open", 0.0))
        bar_high = float(bar.get("High") if "High" in bar else bar.get("high", 0.0))
        bar_low = float(bar.get("Low") if "Low" in bar else bar.get("low", 0.0))
        bar_close = float(bar.get("Close") if "Close" in bar else bar.get("close", 0.0))
        bar_timestamp = bar.get("Timestamp") or bar.get("timestamp") or datetime.utcnow()

        result = dict(setup)

        # ── 1. ACTIVE_SETUP Evaluation (Activation & Gap Checks) ─────────────────
        if status == "ACTIVE_SETUP":
            # Check Activation Condition
            is_activated = False
            if entry_type == "ZONE_MIDPOINT":
                is_activated = (bar_low <= entry_zone_high and bar_high >= entry_zone_low)
            elif entry_type == "BREAKOUT_CONFIRMATION":
                is_activated = (bar_high >= entry_trigger_level)

            if not is_activated:
                result["action"] = "HOLD"
                return result

            # Activation met -> evaluate gap BEFORE transitioning to ENTRY_TRIGGERED
            excessive_gap = False
            gap_reason = None
            gap_pct = 0.0

            if entry_type == "ZONE_MIDPOINT":
                if bar_open > entry_zone_high * 1.02:
                    excessive_gap = True
                    gap_reason = "GAP_ABOVE_ZONE"
                    gap_pct = round(((bar_open - entry_zone_high) / entry_zone_high) * 100.0, 2)
                elif bar_open < entry_zone_low * 0.98:
                    excessive_gap = True
                    gap_reason = "GAP_BELOW_ZONE"
                    gap_pct = round(((entry_zone_low - bar_open) / entry_zone_low) * 100.0, 2)
            elif entry_type == "BREAKOUT_CONFIRMATION":
                if bar_open > entry_trigger_level * 1.02:
                    excessive_gap = True
                    gap_reason = "GAP_THROUGH"
                    gap_pct = round(((bar_open - entry_trigger_level) / entry_trigger_level) * 100.0, 2)

            if excessive_gap:
                result["status"] = "ENTRY_GAP_REJECTED"
                result["setup_outcome"] = "INVALIDATED"
                result["exit_reason"] = gap_reason
                result["entry_quality"] = "DEGRADED_GAP_RISK"
                result["entry_gap_pct"] = gap_pct
                result["entry_trigger_level_reached"] = None
                result["entry_triggered_at"] = None
                result["exit_timestamp"] = datetime.utcnow()
                result["exit_bar_timestamp"] = bar_timestamp
                result["action"] = "REJECTED_GAP"
                return result

            # Valid Activation -> Transition to ENTRY_TRIGGERED
            result["status"] = "ENTRY_TRIGGERED"
            result["setup_outcome"] = "PENDING"
            result["entry_triggered_at"] = datetime.utcnow()
            result["entry_triggered_price"] = preferred_entry if entry_type == "ZONE_MIDPOINT" else entry_trigger_level
            result["entry_quality"] = "STANDARD"

            # Determine trigger direction
            if entry_type == "ZONE_MIDPOINT":
                if entry_zone_low <= bar_open <= entry_zone_high:
                    result["trigger_direction"] = "OPEN_INSIDE"
                elif bar_open > entry_zone_high:
                    result["trigger_direction"] = "FROM_ABOVE"
                elif bar_open < entry_zone_low:
                    result["trigger_direction"] = "FROM_BELOW"
                else:
                    result["trigger_direction"] = "CROSSED_ZONE"
                
                # Check if preferred_entry was reached in bar range
                result["entry_trigger_level_reached"] = (bar_low <= preferred_entry <= bar_high)
                result["entry_trigger_type"] = "ZONE_TOUCH"
            else:
                if bar_open < entry_trigger_level * 0.98:
                    result["trigger_direction"] = "FROM_BELOW"
                else:
                    result["trigger_direction"] = "UPWARD_CROSS"
                
                result["entry_trigger_level_reached"] = (bar_high >= entry_trigger_level)
                result["entry_trigger_type"] = "BREAKOUT_BUFFER"

            # Provenance logging
            result["entry_trigger_bar_timestamp"] = bar_timestamp
            result["entry_trigger_bar_open"] = bar_open
            result["entry_trigger_bar_high"] = bar_high
            result["entry_trigger_bar_low"] = bar_low
            result["entry_trigger_bar_close"] = bar_close

            # Trigger bar N is EXCLUDED from exit evaluation!
            result["action"] = "TRIGGERED_EXCLUDED"
            return result

        # ── 2. Exit Evaluation from Bar N+1 Onward ─────────────────────────────
        # Check Stop Loss
        if bar_low <= stop_loss:
            if bar_high >= target_1:
                result["exit_status"] = "AMBIGUOUS"
                result["exit_assumption"] = "STOP_FIRST"
            else:
                result["exit_status"] = "OK"

            result["status"] = "STOP_TRIGGERED"
            result["setup_outcome"] = "FAILURE"
            result["exit_reason"] = "STOP_LOSS_HIT"
            result["exit_price"] = stop_loss
            result["exit_bar_timestamp"] = bar_timestamp
            result["exit_timestamp"] = datetime.utcnow()
            result["action"] = "STOP_TRIGGERED"
            # Note: best_target_reached (T1 or T2) is PRESERVED!
            return result

        # Check Target 3 (Terminal Success)
        if bar_high >= target_3:
            result["best_target_reached"] = "T3"
            result["status"] = "SETUP_COMPLETED"
            result["setup_outcome"] = "SUCCESS"
            result["exit_reason"] = "TARGET_3_REACHED"
            result["exit_price"] = target_3
            result["exit_bar_timestamp"] = bar_timestamp
            result["exit_timestamp"] = datetime.utcnow()
            result["action"] = "SETUP_COMPLETED"
            return result

        # Check Target 2 Milestone
        if bar_high >= target_2:
            if best_target_reached != "T2":
                result["best_target_reached"] = "T2"
                result["status"] = "TARGET_2_REACHED"
                result["setup_outcome"] = "PENDING"
                result["last_milestone_price"] = target_2
                result["last_milestone_bar_timestamp"] = bar_timestamp
                result["last_milestone_timestamp"] = datetime.utcnow()
                result["action"] = "MILESTONE_T2"
                return result

        # Check Target 1 Milestone
        if bar_high >= target_1:
            if best_target_reached is None:
                result["best_target_reached"] = "T1"
                result["status"] = "TARGET_1_REACHED"
                result["setup_outcome"] = "PENDING"
                result["last_milestone_price"] = target_1
                result["last_milestone_bar_timestamp"] = bar_timestamp
                result["last_milestone_timestamp"] = datetime.utcnow()
                result["action"] = "MILESTONE_T1"
                return result

        # Explicit HOLD Behavior
        result["action"] = "HOLD"
        return result
