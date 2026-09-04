# =====================================================================================
# app/zero_alert_diagnostic.py
# INSTITUTIONAL ZERO-ALERT DIAGNOSTIC & CONSERVATION FUNNEL ENGINE
#
# Provides:
# 1. SingleTerminalTracker — Mutually-exclusive, single-disposition tracking guaranteeing
#    exact conservation of the scanner universe (Sum of Terminal Outcomes == Universe Size).
# 2. StageWaterfallTracker — Calculates population attrition across sequential stages and
#    identifies dominant bottleneck by relative failure rate (eliminated / entered).
# 3. classify_zero_alert_run — Categorizes zero-alert runs into LEGITIMATE_ZERO,
#    SUSPICIOUS_ZERO, CRITICAL_ZERO, or DATA_OR_ENGINE_FAILURE.
# =====================================================================================

import threading
import logging
from typing import Dict, List, Optional, Any, Set, Tuple

logger = logging.getLogger("zero_alert_diagnostic")


class SingleTerminalTracker:
    """
    Guarantees that every stock evaluated in a scanner universe receives EXACTLY ONE
    terminal disposition (first decisive failure wins). Enforces mathematical conservation:
    Universe Size == sum(terminal_dispositions) with Delta == 0.
    """
    def __init__(self, universe: Any):
        if isinstance(universe, (list, set, tuple)):
            self.universe_symbols: Set[str] = set(universe)
            self.total_universe: int = len(self.universe_symbols)
        elif isinstance(universe, int):
            self.universe_symbols = set()
            self.total_universe = universe
        else:
            self.universe_symbols = set()
            self.total_universe = 0

        self._lock = threading.Lock()
        self._dispositions: Dict[str, Tuple[str, str]] = {}  # symbol -> (gate, reason)
        self._counts: Dict[str, int] = {}                    # gate -> count

    def record_terminal(self, symbol: str, gate: str, reason: str = "") -> bool:
        """
        Records the terminal disposition for a symbol.
        Returns True if this was the first recording for this symbol.
        Returns False if the symbol already has an assigned terminal disposition (first-fail wins).
        """
        with self._lock:
            if symbol in self._dispositions:
                return False  # Already assigned terminal outcome, ignore downstream gates

            self._dispositions[symbol] = (gate, reason)
            self._counts[gate] = self._counts.get(gate, 0) + 1
            return True

    def record_untracked_remainder(self, default_gate: str = "UNTRACKED_DROP") -> int:
        """Assigns default_gate to any symbol in universe_symbols that did not receive a disposition."""
        untracked = 0
        with self._lock:
            for s in self.universe_symbols:
                if s not in self._dispositions:
                    self._dispositions[s] = (default_gate, "No explicit terminal assigned")
                    self._counts[default_gate] = self._counts.get(default_gate, 0) + 1
                    untracked += 1
        return untracked

    def get_summary(self) -> Dict[str, Any]:
        """Returns conservation accounting summary."""
        with self._lock:
            sum_terminal = sum(self._counts.values())
            delta = self.total_universe - sum_terminal
            return {
                "total_universe": self.total_universe,
                "terminal_counts": dict(sorted(self._counts.items(), key=lambda x: x[1], reverse=True)),
                "sum_terminal": sum_terminal,
                "conservation_delta": delta,
                "is_conserved": (delta == 0),
                "recorded_symbols_count": len(self._dispositions)
            }


class StageWaterfallTracker:
    """
    Tracks populations entering and surviving sequential pipeline stages:
    Stage 1 -> Stage 2 -> Stage 3 -> ... -> Alerts.
    Computes attrition rates and identifies the Dominant Bottleneck Gate.
    """
    def __init__(self, stage_order: Optional[List[str]] = None):
        self._stage_order: List[str] = stage_order or []
        self._stage_counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def set_stage_count(self, stage_name: str, count: int) -> None:
        with self._lock:
            if stage_name not in self._stage_order:
                self._stage_order.append(stage_name)
            self._stage_counts[stage_name] = count

    def compute_attrition(self) -> List[Dict[str, Any]]:
        """
        Computes stage-by-stage attrition:
        loss = entered - passed
        loss_pct = (loss / entered) * 100
        """
        with self._lock:
            results = []
            for i in range(len(self._stage_order) - 1):
                cur_stage = self._stage_order[i]
                next_stage = self._stage_order[i + 1]
                entered = self._stage_counts.get(cur_stage, 0)
                passed = self._stage_counts.get(next_stage, 0)
                eliminated = max(0, entered - passed)
                attrition_pct = (eliminated / entered * 100.0) if entered > 0 else 0.0
                results.append({
                    "stage": cur_stage,
                    "next_stage": next_stage,
                    "entered": entered,
                    "passed": passed,
                    "eliminated": eliminated,
                    "attrition_pct": round(attrition_pct, 1)
                })
            return results

    def get_dominant_bottleneck(self) -> Optional[Dict[str, Any]]:
        """
        Returns the gate with the highest relative loss rate (eliminated / entered)
        among stages where candidates entered (entered > 0 and eliminated > 0).
        """
        stages = self.compute_attrition()
        candidates = [s for s in stages if s["entered"] > 0 and s["eliminated"] > 0]
        if not candidates:
            return None
        # Sort by attrition_pct descending, then by eliminated count descending
        candidates.sort(key=lambda x: (x["attrition_pct"], x["eliminated"]), reverse=True)
        return candidates[0]


def classify_zero_alert_run(
    scanner_name: str,
    universe_size: int,
    valid_data_count: int,
    initial_setups_count: int,
    finalist_candidates_count: int,
    alerts_generated: int,
    near_miss_count: int = 0,
    regime: str = "NEUTRAL",
    execution_mode: str = "LIVE",
    stage_waterfall: Optional[List[Dict[str, Any]]] = None,
    persistence_failures_count: int = 0
) -> Dict[str, Any]:
    """
    Classifies a zero-alert run into an institutional anomaly category based on
    data health, persistence integrity, stage-by-stage candidate penetration, and execution mode:
    1. DATA_OR_ENGINE_FAILURE: Provider failure, fetch coverage < 75%, or 0 valid data.
    2. CRITICAL_ZERO: Finalists reached last risk/persistence gate, or state persistence failed (even in PREARM).
    3. SUSPICIOUS_ZERO: Candidates penetrated deep into intermediate/downstream stages (or near-misses existed), but 100% eliminated before alert emission.
    4. LEGITIMATE_ZERO: Clean legitimate zero (0 technical structures formed, or normal PREARM screening without failures).
    """
    if alerts_generated > 0:
        return {
            "classification": "NORMAL_ALERT_GENERATION",
            "severity": "INFO",
            "explanation": f"{scanner_name} emitted {alerts_generated} valid alerts.",
            "recommendation": "None",
            "last_stage_with_candidates": "ALERT_PERSISTENCE"
        }

    # 1. DATA OR ENGINE FAILURE: Critical data deficit or provider failure
    data_ratio = valid_data_count / max(universe_size, 1)
    if valid_data_count == 0 or data_ratio < 0.75:
        return {
            "classification": "DATA_OR_ENGINE_FAILURE",
            "severity": "CRITICAL",
            "explanation": f"Data coverage insufficient ({valid_data_count}/{universe_size} = {data_ratio*100:.1f}%). Provider down or blocked.",
            "recommendation": "Inspect data provider health, API tokens, and market session connectivity.",
            "last_stage_with_candidates": "DATA_ACQUISITION"
        }

    # 2. PERSISTENCE OR FINALIST FAILURE: Candidates reached final gate but failed to persist
    # Note: Even in PREARM/MONITOR mode, persistence failures are a critical defect, not a legitimate zero.
    if finalist_candidates_count > 0 or persistence_failures_count > 0:
        fail_count = finalist_candidates_count or persistence_failures_count
        return {
            "classification": "CRITICAL_ZERO",
            "severity": "CRITICAL",
            "explanation": f"{fail_count} candidates reached final risk/persistence gate or failed during state persistence, but 0 alerts/records were persisted.",
            "recommendation": "Verify SL/Target engine thresholds, live price recheck buy-zone shift, and database write connectivity.",
            "last_stage_with_candidates": "FINAL_RISK_AND_PERSISTENCE"
        }

    # Inspect stage waterfall progression to identify deepest stage reached
    deepest_stage = "UNIVERSE"
    downstream_candidates_reached = False

    if stage_waterfall and len(stage_waterfall) > 1:
        for idx, stg in enumerate(stage_waterfall):
            if stg.get("entered", 0) > 0:
                deepest_stage = stg.get("stage", "UNKNOWN")
                # If candidates penetrated past the first 2 stages into scoring/conviction/risk
                if idx >= 2:
                    downstream_candidates_reached = True
            if stg.get("passed", 0) > 0 and idx == len(stage_waterfall) - 1:
                deepest_stage = stg.get("next_stage", deepest_stage)

    # 3. DEEP FUNNEL COLLAPSE: Setups reached downstream gates or notable near-misses existed
    if downstream_candidates_reached or near_miss_count > 0:
        return {
            "classification": "SUSPICIOUS_ZERO",
            "severity": "WARNING",
            "explanation": f"Discovered {initial_setups_count} technical structures with candidates penetrating into stage '{deepest_stage}', but downstream filters eliminated 100% ({near_miss_count} near misses).",
            "recommendation": "Inspect dominant bottleneck attrition rate to assess if volume, conviction, or score gates are overly restrictive.",
            "last_stage_with_candidates": deepest_stage
        }

    # 4. NO VIABLE STRUCTURES: Clean structural legitimate zero under prevailing regime
    if initial_setups_count == 0 and near_miss_count == 0:
        return {
            "classification": "LEGITIMATE_ZERO",
            "severity": "INFO",
            "explanation": f"Market regime is {regime}. 0 structural setups formed from {universe_size} stocks. Clean legitimate zero under current market conditions.",
            "recommendation": "No action required. Market conditions do not exhibit required setup characteristics.",
            "last_stage_with_candidates": deepest_stage
        }

    # 5. PREARM / OUTSIDE WINDOW: Normal scheduled setup screening (with surviving armed pool)
    if execution_mode in ("PREARM", "NON_MARKET", "OUTSIDE_WINDOW"):
        return {
            "classification": "LEGITIMATE_ZERO",
            "severity": "INFO",
            "explanation": f"Execution mode is {execution_mode}. Successfully screened and preserved {initial_setups_count} armed candidate setups for next session open; new live trade entries intentionally suppressed.",
            "recommendation": "Monitor armed candidate pool for execution eligibility at 09:15 open.",
            "last_stage_with_candidates": deepest_stage
        }

    return {
        "classification": "LEGITIMATE_ZERO",
        "severity": "INFO",
        "explanation": f"Market regime is {regime}. Found {initial_setups_count} preliminary candidates, but all failed standard early technical filtering. No late-stage survivors.",
        "recommendation": "No action required. Preliminary candidates lacked necessary confirmation.",
        "last_stage_with_candidates": deepest_stage
    }


def format_zero_alert_diagnostic_block(
    scanner_name: str,
    execution_mode: str,
    regime: str,
    classification_result: Dict[str, Any],
    dominant_bottleneck: Optional[Dict[str, Any]],
    conservation_summary: Dict[str, Any],
    stage_waterfall: Optional[List[Dict[str, Any]]] = None,
    near_miss_count: int = 0,
    extra_specs: Optional[List[str]] = None
) -> List[str]:
    """Generates a standardized ASCII diagnostic block for scanner logs."""
    cls_name = classification_result.get("classification", "UNKNOWN")
    severity = classification_result.get("severity", "INFO")
    icon = "🚨" if severity == "CRITICAL" else ("⚠️" if severity == "WARNING" else "ℹ️")

    lines = [
        "",
        f"{icon} ZERO_ALERT_DIAGNOSTIC ({scanner_name}):",
        f"  • Classification            : {cls_name} [{severity}]",
        f"  • Execution Mode            : {execution_mode}",
        f"  • Market Regime             : {regime}",
        f"  • Finding                   : {classification_result.get('explanation', '')}",
    ]

    deepest = classification_result.get("last_stage_with_candidates")
    if deepest and deepest not in ("NONE", "UNIVERSE"):
        lines.append(f"  • Deepest Stage Reached     : {deepest}")

    if extra_specs:
        for spec in extra_specs:
            lines.append(f"  • {spec}")

    if dominant_bottleneck:
        b_stage = dominant_bottleneck.get("stage", "UNKNOWN")
        b_entered = dominant_bottleneck.get("entered", 0)
        b_failed = dominant_bottleneck.get("eliminated", 0)
        b_rate = dominant_bottleneck.get("attrition_pct", 0.0)
        lines.append(f"  • Dominant Bottleneck Gate  : {b_stage} (Attrition: {b_failed}/{b_entered} = {b_rate:.1f}%)")

    if stage_waterfall:
        lines.append("  • Stage Waterfall Funnel    :")
        for stg in stage_waterfall:
            lines.append(f"      ├── {stg['stage']:<24}: {stg['entered']:>4} entered → {stg['passed']:>4} passed (loss: {stg['eliminated']} [{stg['attrition_pct']:.1f}%])")

    lines.append(f"  • Near Miss Candidates      : {near_miss_count}")
    lines.append(f"  • Conservation Check        : {conservation_summary.get('sum_terminal', 0)}/{conservation_summary.get('total_universe', 0)} terminal outcomes (Delta: {conservation_summary.get('conservation_delta', 0)})")
    lines.append(f"  • Recommendation            : {classification_result.get('recommendation', 'None')}")

    return lines
