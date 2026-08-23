"""
app/accumulation/telemetry.py — Forensic Decision Audit Snapshots & Provenance Fingerprints for ACCUMULATION_SCANNER_V1.
Generates unique audit_snapshot_ids and records complete gate-by-gate decision metadata.
"""

import hashlib
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class AccumulationTelemetry:
    """Forensic Telemetry Engine for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def generate_snapshot_id(symbol: str, run_id: str, timestamp: Optional[datetime] = None) -> str:
        if timestamp is None:
            timestamp = datetime.utcnow()
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        raw_key = f"{symbol}_{run_id}_{ts_str}"
        short_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:8]
        return f"ACCUM_SNAP_{symbol}_{ts_str}_{short_hash}"

    @staticmethod
    def generate_provenance_fingerprint(data: Dict[str, Any]) -> str:
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

    @staticmethod
    def record_alert(alert_data: Dict[str, Any], conn=None) -> Optional[int]:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.error(f"Could not connect DB for record_alert: {e}")
                return None

        try:
            cur = conn.cursor()
            query = """
                INSERT INTO accumulation_alerts (
                    run_id, audit_snapshot_id, parent_snapshot_id, finalization_snapshot_id, finalization_status,
                    symbol, signal_state, tradable, score, close, entry_zone_low, entry_zone_high, breakout_level,
                    preferred_entry, entry_method, entry_trigger_rule, stop_loss, target_1, target_2, target_3,
                    risk_pct, rr_1, rr_2, rr_3, suggested_capital, suggested_position_size, position_sizing_basis, effective_as_of
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING id;
            """
            params = (
                alert_data["run_id"], alert_data["audit_snapshot_id"], alert_data.get("parent_snapshot_id"),
                alert_data.get("finalization_snapshot_id"), alert_data.get("finalization_status", "PASSED"),
                alert_data["symbol"], alert_data["signal_state"], alert_data.get("tradable", True),
                alert_data["score"], alert_data["close"], alert_data["entry_zone_low"], alert_data["entry_zone_high"],
                alert_data["breakout_level"], alert_data["preferred_entry"], alert_data.get("entry_method", "ZONE_MIDPOINT"),
                alert_data.get("entry_trigger_rule", "RANGE_TOUCH"), alert_data["stop_loss"], alert_data["target_1"],
                alert_data["target_2"], alert_data["target_3"], alert_data["risk_pct"], alert_data["rr_1"],
                alert_data["rr_2"], alert_data["rr_3"], alert_data.get("suggested_capital"),
                alert_data.get("suggested_position_size"), alert_data.get("position_sizing_basis", "ACCOUNT_RISK_1PCT"),
                alert_data.get("effective_as_of", datetime.utcnow())
            )
            cur.execute(query, params)
            alert_id = cur.fetchone()[0]
            conn.commit()
            return alert_id
        except Exception as e:
            logger.error(f"Error recording accumulation alert: {e}")
            if hasattr(conn, "rollback"): conn.rollback()
            return None
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass
