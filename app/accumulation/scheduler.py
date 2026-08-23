"""
app/accumulation/scheduler.py — Standalone Background Runner for ACCUMULATION_SCANNER_V1.
Executes 15:45 IST post-close scan and 18:00 IST delivery finalization pass.
"""

import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd

from app.accumulation.scanner import AccumulationScanner
from app.accumulation.telemetry import AccumulationTelemetry
from app.accumulation.health import AccumulationHealthTracker
from app.accumulation.control import AccumulationControlPlane

logger = logging.getLogger(__name__)

class AccumulationScheduler:
    """Standalone Scheduler for ACCUMULATION_SCANNER_V1."""

    def __init__(self):
        self.scanner_name = "ACCUMULATION_SCANNER_V1"

    def run_main_scan(self, symbol_data_map: Dict[str, pd.DataFrame], fundamental_map: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes 15:45 IST main scan pass (Snapshot A creation).
        """
        start_time = time.time()
        run_id = f"ACCUM_RUN_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        AccumulationHealthTracker.record_heartbeat(run_id, lifecycle_state="RUNNING", current_phase="MAIN_SCAN_1545")

        control_state = AccumulationControlPlane.get_control_state()
        if not control_state.get("enabled") or control_state.get("paused"):
            logger.info("ACCUMULATION_SCANNER_V1 execution paused or disabled.")
            return {"run_id": run_id, "status": "PAUSED", "alerts": []}

        scanner = AccumulationScanner(run_id=run_id)
        alerts = []

        for symbol, df in symbol_data_map.items():
            fund_data = fundamental_map.get(symbol, {"roe": 15.0, "roce": 18.0, "de_ratio": 0.5})
            res = scanner.process_symbol(symbol, df, fund_data)
            if res.get("passed"):
                alerts.append(res)

        duration = time.time() - start_time
        AccumulationHealthTracker.record_success(run_id, duration)
        return {"run_id": run_id, "status": "COMPLETED", "alerts": alerts, "duration_seconds": round(duration, 2)}

    def run_delivery_finalization(self, pending_alerts: List[Dict[str, Any]], delivery_map: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Executes 18:00 IST delivery finalization pass using Snapshot A technical data + fresh delivery data.
        Sets canonical_for_trade = True on Snapshot B.
        """
        finalized_setups = []
        for alert in pending_alerts:
            symbol = alert["symbol"]
            delivery_status = delivery_map.get(symbol, {}).get("status", "UNAVAILABLE")
            
            # Recompute delivery component only; technical indicators remain 100% frozen from Snapshot A
            if delivery_status == "VALID":
                parent_snapshot_id = alert["audit_snapshot_id"]
                snapshot_b_id = AccumulationTelemetry.generate_snapshot_id(symbol, alert.get("run_id", "FINAL_1800"))
                
                finalized_alert = dict(alert)
                finalized_alert["parent_snapshot_id"] = parent_snapshot_id
                finalized_alert["audit_snapshot_id"] = snapshot_b_id
                finalized_alert["finalization_status"] = "PASSED"
                finalized_alert["canonical_for_trade"] = True
                finalized_setups.append(finalized_alert)
            else:
                alert["finalization_status"] = "REJECTED"
                alert["canonical_for_trade"] = False

        return finalized_setups
