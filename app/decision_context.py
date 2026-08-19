# =====================================================================================
# app/decision_context.py
# PER-STOCK DECISION CONTEXT & TERMINAL TELEMETRY ENGINE
# =====================================================================================
import json
import math
import time
import logging
from typing import Any, Dict, List, Optional, Union, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("DECISION_TELEMETRY")


def sanitize_value(val: Any) -> Tuple[Any, str, str]:
    """Sanitizes raw value and returns (sanitized_val, status, reason)."""
    if val is None:
        return None, "MISSING", "VALUE_IS_NONE"
    if isinstance(val, float):
        if math.isnan(val) or np.isnan(val):
            return "NaN", "INVALID", "VALUE_IS_NAN"
        if math.isinf(val) or np.isinf(val):
            return "INF", "INVALID", "VALUE_IS_INF"
    if isinstance(val, (int, float, np.number)):
        if val == 0 or val == 0.0:
            return val, "VALID", "ZERO_VALUE"
    return val, "VALID", "OK"


class ValueEntry:
    """Represents a single captured field in the decision context with provenance and status."""
    def __init__(self, key: str, value: Any, origin: str = "CALCULATED", group: str = "DERIVED", source_series: str = None):
        self.key = key
        self.raw_value = value
        self.origin = origin # EXTERNAL_API, CALCULATED, CONFIG, DERIVED
        self.group = group # RAW, INDICATOR, DERIVED, CONFIG, GATE, SCORE, SL_TARGET
        self.source_series = source_series
        self.timestamp = time.time()
        
        sanitized, status, reason = sanitize_value(value)
        self.value = sanitized
        self.status = status
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "origin": self.origin,
            "group": self.group,
            "source_series": self.source_series
        }


class DecisionContext:
    """
    Self-auditing container capturing EVERY raw value, indicator, config threshold,
    gate result, score component, SL/target, and alert payload for a single stock.
    """
    def __init__(self, symbol: str, scanner_name: str, exchange: str = "NSE", run_id: str = None):
        self.symbol = symbol
        self.scanner_name = scanner_name
        self.exchange = exchange
        self.run_id = run_id or f"run_{int(time.time())}"
        self.start_time = time.time()
        
        self.terminal_decision = "PENDING"
        self.primary_reason = ""
        self.alert_generated = False
        self.alert_id = None
        self.persisted = False
        
        # Captured Value Maps
        self.entries: Dict[str, ValueEntry] = {}
        self.gate_results: Dict[str, Dict[str, Any]] = {}
        self.score_breakdown: Dict[str, Dict[str, Any]] = {}
        self.configuration: Dict[str, Any] = {}
        self.sl_target: Dict[str, Any] = {}

    def capture(self, key: str, value: Any, origin: str = "CALCULATED", group: str = "DERIVED", source_series: str = None):
        """Captures a field into decision context, guaranteeing non-silent retention."""
        entry = ValueEntry(key=key, value=value, origin=origin, group=group, source_series=source_series)
        self.entries[key] = entry

    def capture_config(self, key: str, value: Any):
        """Captures a configuration threshold."""
        self.configuration[key] = value
        self.capture(key=f"config_{key}", value=value, origin="CONFIG", group="CONFIG")

    def capture_gate(self, gate_name: str, passed: bool, actual_val: Any = None, required_val: Any = None, reason: str = ""):
        """Captures a gate evaluation result."""
        self.gate_results[gate_name] = {
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "actual": actual_val,
            "required": required_val,
            "reason": reason
        }
        self.capture(key=f"gate_{gate_name}", value="PASS" if passed else "FAIL", origin="CALCULATED", group="GATE")

    def capture_score(self, component_name: str, score_points: float, max_points: float, reason: str = ""):
        """Captures a scoring breakdown component."""
        self.score_breakdown[component_name] = {
            "points": round(float(score_points), 2),
            "max_points": round(float(max_points), 2),
            "reason": reason
        }
        self.capture(key=f"score_{component_name}", value=score_points, origin="CALCULATED", group="SCORE")

    def capture_sl_target(self, entry_price: float, sl_price: float, target_price: float, rr_ratio: float = None):
        """Captures SL & Target geometry."""
        self.sl_target = {
            "entry_price": entry_price,
            "sl_price": sl_price,
            "target_price": target_price,
            "rr_ratio": rr_ratio
        }
        self.capture(key="entry_price", value=entry_price, origin="CALCULATED", group="SL_TARGET")
        self.capture(key="sl_price", value=sl_price, origin="CALCULATED", group="SL_TARGET")
        self.capture(key="target_price", value=target_price, origin="CALCULATED", group="SL_TARGET")
        if rr_ratio is not None:
            self.capture(key="rr_ratio", value=rr_ratio, origin="CALCULATED", group="SL_TARGET")

    def finalize(self, decision: str, primary_reason: str = "", alert_dict: dict = None):
        """Finalizes the context decision and alert state."""
        self.terminal_decision = decision # SELECTED / REJECTED / NOT_APPLICABLE
        self.primary_reason = primary_reason
        if alert_dict:
            self.alert_generated = True
            self.alert_id = alert_dict.get("alert_id") or alert_dict.get("id") or f"ALT_{self.symbol}_{int(time.time())}"
            self.persisted = alert_dict.get("persisted", True)

    @property
    def data_quality_summary(self) -> dict:
        """Computes data quality counts."""
        missing = sum(1 for e in self.entries.values() if e.status == "MISSING")
        invalid = sum(1 for e in self.entries.values() if e.status == "INVALID")
        valid = sum(1 for e in self.entries.values() if e.status == "VALID")
        return {
            "total_captured": len(self.entries),
            "valid_count": valid,
            "missing_count": missing,
            "invalid_count": invalid,
            "none_count": missing,
            "nan_count": invalid
        }

    def format_terminal_audit_box(self) -> str:
        """Generates a complete, human-readable ASCII terminal audit box (Section 8 & 9)."""
        lines = []
        lines.append("=" * 80)
        lines.append("SCANNER TERMINAL AUDIT")
        lines.append("=" * 80)
        lines.append(f"Scanner        : {self.scanner_name}")
        lines.append(f"Symbol         : {self.symbol}")
        lines.append(f"Exchange       : {self.exchange}")
        lines.append(f"Run ID         : {self.run_id}")
        lines.append(f"Decision       : {self.terminal_decision}")
        lines.append("=" * 80)

        # 1. RAW MARKET DATA
        raw_entries = [e for e in self.entries.values() if e.group == "RAW"]
        if raw_entries:
            lines.append("\n[RAW MARKET DATA]")
            for e in raw_entries:
                val_str = f"{e.value:,}" if isinstance(e.value, (int, float)) and e.value > 1000 else str(e.value)
                lines.append(f"{e.key:<22} = {val_str}")

        # 2. TECHNICAL INDICATORS
        ind_entries = [e for e in self.entries.values() if e.group == "INDICATOR"]
        if ind_entries:
            lines.append("\n[TECHNICAL INDICATORS]")
            for e in ind_entries:
                val_str = f"{e.value:.2f}" if isinstance(e.value, float) else str(e.value)
                lines.append(f"{e.key:<22} = {val_str}")

        # 3. DERIVED VALUES
        der_entries = [e for e in self.entries.values() if e.group == "DERIVED"]
        if der_entries:
            lines.append("\n[DERIVED VALUES]")
            for e in der_entries:
                val_str = f"{e.value:.2f}" if isinstance(e.value, float) else str(e.value)
                lines.append(f"{e.key:<22} = {val_str}")

        # 4. CONFIGURATION USED
        if self.configuration:
            lines.append("\n[CONFIGURATION USED]")
            for k, v in self.configuration.items():
                lines.append(f"{k:<22} = {v}")

        # 5. EVERY GATE
        if self.gate_results:
            lines.append("\n[EVERY GATE]")
            for g_name, g_info in self.gate_results.items():
                status_str = g_info["status"]
                lines.append(f"{g_name:<22} = {status_str}")
                if not g_info["passed"]:
                    if g_info.get("actual") is not None:
                        lines.append(f"  Actual               = {g_info['actual']}")
                    if g_info.get("required") is not None:
                        lines.append(f"  Required             = {g_info['required']}")

        # 6. SCORE BREAKDOWN
        if self.score_breakdown:
            lines.append("\n[SCORE]")
            total_pts = 0.0
            total_max = 0.0
            for sc_name, sc_info in self.score_breakdown.items():
                pts = sc_info["points"]
                max_p = sc_info["max_points"]
                total_pts += pts
                total_max += max_p
                lines.append(f"{sc_name:<22} = {pts:.1f} / {max_p:.1f}")
            lines.append(f"{'TOTAL SCORE':<22} = {total_pts:.1f} / {total_max:.1f}")

        # 7. SL / TARGET
        if self.sl_target:
            lines.append("\n[SL / TARGET]")
            lines.append(f"Entry Price            = ₹{self.sl_target.get('entry_price', 0.0):.2f}")
            lines.append(f"Stop Loss Price        = ₹{self.sl_target.get('sl_price', 0.0):.2f}")
            lines.append(f"Target Price           = ₹{self.sl_target.get('target_price', 0.0):.2f}")
            if self.sl_target.get("rr_ratio"):
                lines.append(f"Risk / Reward Ratio    = {self.sl_target['rr_ratio']:.2f}")

        # 8. DATA QUALITY
        dq = self.data_quality_summary
        lines.append("\n[DATA QUALITY]")
        lines.append(f"Missing Values         = {dq['missing_count']}")
        lines.append(f"None Values            = {dq['none_count']}")
        lines.append(f"NaN Values             = {dq['nan_count']}")
        lines.append(f"Invalid Values         = {dq['invalid_count']}")

        # 9. FINAL
        lines.append("\n[FINAL]")
        lines.append(f"Terminal Decision      = {self.terminal_decision}")
        if self.primary_reason:
            lines.append(f"Primary Reason         = {self.primary_reason}")
        lines.append(f"Alert Generated        = {'YES' if self.alert_generated else 'NO'}")
        if self.alert_id:
            lines.append(f"Alert ID               = {self.alert_id}")
        lines.append("=" * 80)
        
        return "\n".join(lines)

    def to_telemetry_json(self) -> dict:
        """Serializes complete decision context into JSON-compatible dictionary."""
        return {
            "run_id": self.run_id,
            "scanner": self.scanner_name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "terminal_decision": self.terminal_decision,
            "primary_reason": self.primary_reason,
            "alert_generated": self.alert_generated,
            "alert_id": self.alert_id,
            "persisted": self.persisted,
            "data_quality": self.data_quality_summary,
            "configuration": self.configuration,
            "gate_results": self.gate_results,
            "score_breakdown": self.score_breakdown,
            "sl_target": self.sl_target,
            "all_values": {k: e.to_dict() for k, e in self.entries.items()}
        }
