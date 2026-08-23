"""
app/accumulation_telemetry.py

Forensic Telemetry Engine for ACCUMULATION_SCANNER_V1.
Generates SCANNER TERMINAL DECISION AUDIT records, input manifests, and DB snapshot logs.
"""

import json
import math
import hashlib
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from database import get_connection

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _sanitize_nans(obj: Any) -> Any:
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    elif isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_nans(item) for item in obj]
    return obj


class AccumulationTelemetryContext:
    def __init__(self, run_id: str, symbol: str, scanner: str = "ACCUMULATION"):
        self.run_id = run_id
        self.symbol = symbol
        self.scanner = scanner
        self.effective_as_of = datetime.now(IST).isoformat()
        
        self.raw_market: Dict[str, Any] = {}
        self.indicators: Dict[str, Any] = {}
        self.fundamentals: Dict[str, Any] = {}
        self.relative_strength: Dict[str, Any] = {}
        self.resistance: Dict[str, Any] = {}
        self.scores: Dict[str, Any] = {}
        self.sl_target: Dict[str, Any] = {}

        self.gates: List[Dict[str, Any]] = []
        self.consumed_inputs: List[Dict[str, Any]] = []
        self.decision = "PENDING"
        self.primary_reason = ""
        
        # Unique audit snapshot ID
        h_str = f"{self.scanner}:{self.symbol}:{self.run_id}:{self.effective_as_of}"
        self.audit_snapshot_id = f"snap_acc_{hashlib.sha256(h_str.encode('utf-8')).hexdigest()[:16]}"

    def capture_raw_market(self, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timestamp: str):
        self.raw_market = {
            "Open": open_p, "High": high_p, "Low": low_p, "Close": close_p, "Volume": volume, "Timestamp": timestamp
        }

    def capture_indicators(self, indicators_dict: Dict[str, Any], provenance: str = "TA_LIB_PARQUET"):
        for k, v in indicators_dict.items():
            self.indicators[k] = {
                "value": _sanitize_nans(v),
                "provenance": provenance
            }

    def capture_fundamentals(self, fund_dict: Dict[str, Any]):
        for k, v in fund_dict.items():
            self.fundamentals[k] = _sanitize_nans(v)

    def capture_relative_strength(self, rs_dict: Dict[str, Any]):
        self.relative_strength = _sanitize_nans(rs_dict)

    def capture_resistance(self, resistance_dict: Dict[str, Any]):
        self.resistance = _sanitize_nans(resistance_dict)

    def capture_scores(self, score_breakdown: Dict[str, Any]):
        self.scores = _sanitize_nans(score_breakdown)

    def capture_sl_target(self, sl_target_dict: Dict[str, Any]):
        self.sl_target = _sanitize_nans(sl_target_dict)

    def capture_gate(self, gate_name: str, passed: bool, actual_val: Any, operator_str: str, threshold_val: Any, reason: str):
        self.gates.append({
            "gate_name": gate_name,
            "passed": passed,
            "actual": _sanitize_nans(actual_val),
            "operator": operator_str,
            "threshold": _sanitize_nans(threshold_val),
            "reason": reason
        })

    def add_decision_input(self, name: str, value: Any, source: str, valid: bool = True):
        self.consumed_inputs.append({
            "name": name,
            "value": _sanitize_nans(value),
            "source": source,
            "valid": valid
        })

    def finalize(self, decision: str, primary_reason: str):
        self.decision = decision
        self.primary_reason = primary_reason

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "audit_snapshot_id": self.audit_snapshot_id,
            "run_id": self.run_id,
            "scanner": self.scanner,
            "symbol": self.symbol,
            "effective_as_of": self.effective_as_of,
            "decision": self.decision,
            "primary_reason": self.primary_reason,
            "raw_market": self.raw_market,
            "indicators": self.indicators,
            "fundamentals": self.fundamentals,
            "relative_strength": self.relative_strength,
            "resistance": self.resistance,
            "scores": self.scores,
            "sl_target": self.sl_target,
            "gates": self.gates,
            "consumed_inputs": self.consumed_inputs
        }
        return _sanitize_nans(payload)

    def persist(self):
        payload = self.to_dict()
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO accumulation_telemetry (run_id, symbol, audit_snapshot_id, payload, created_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        """,
                        (self.run_id, self.symbol, self.audit_snapshot_id, json.dumps(payload))
                    )
                    conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist accumulation_telemetry for {self.symbol}: {e}")
