# =====================================================================================
# app/pipeline_telemetry.py
# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Pipeline Stage Telemetry & Performance Budgets
# =====================================================================================

import time
import logging
from typing import Dict, Any, Optional
import config

logger = logging.getLogger(__name__)

class StageTimer:
    """Helper to time a specific code block/stage."""
    def __init__(self, telemetry, stage_name: str):
        self.telemetry = telemetry
        self.stage_name = stage_name
        self.start_t = 0.0

    def __enter__(self):
        self.start_t = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_t
        self.telemetry.record_stage(self.stage_name, duration)


class PipelineTelemetry:
    """
    Centralized dual-level telemetry engine tracking 8 pipeline stages:
    1. Download
    2. Fallback
    3. Validation
    4. Indicators
    5. Parquet Write
    6. Scanner
    7. Database
    8. Cleanup
    """
    def __init__(self, context_name: str = "Scan"):
        self.context_name = context_name
        self.stage_durations: Dict[str, float] = {}
        self.budgets = getattr(config, "STAGE_PERFORMANCE_BUDGETS", {})

    def record_stage(self, stage_name: str, duration_seconds: float):
        """Accumulates duration for a specific stage."""
        self.stage_durations[stage_name] = self.stage_durations.get(stage_name, 0.0) + duration_seconds

    def timer(self, stage_name: str) -> StageTimer:
        return StageTimer(self, stage_name)

    def evaluate_budgets(self) -> Dict[str, Any]:
        """
        Compares cumulative stage durations against STAGE_PERFORMANCE_BUDGETS.
        Returns a dict of stage status evaluations (PASS, WARNING, FAIL).
        """
        results = {}
        total_time = sum(self.stage_durations.values())
        total_budget = self.budgets.get("total_scan_seconds", 60.0)

        for stage, duration in self.stage_durations.items():
            budget_key = f"{stage}_seconds"
            budget = self.budgets.get(budget_key, 30.0)
            
            if duration > budget:
                status = "FAIL"
            elif duration > (budget * 0.8):
                status = "WARNING"
            else:
                status = "PASS"

            results[stage] = {
                "duration_s": round(duration, 3),
                "budget_s": budget,
                "status": status
            }

        total_status = "FAIL" if total_time > total_budget else ("WARNING" if total_time > (total_budget * 0.8) else "PASS")
        results["TOTAL"] = {
            "duration_s": round(total_time, 3),
            "budget_s": total_budget,
            "status": total_status
        }
        return results

    def log_summary(self, caller: str = None):
        """Prints dual-level stage-by-stage timing summary with budget status."""
        evals = self.evaluate_budgets()
        caller_str = f" [{caller}]" if caller else ""
        
        stages_str = " | ".join([
            f"{stg.capitalize()}: {data['duration_s']}s ({data['status']})"
            for stg, data in evals.items() if stg != "TOTAL"
        ])
        
        tot = evals.get("TOTAL", {})
        logger.info(
            f"⏱️ [PIPELINE_TELEMETRY]{caller_str} {self.context_name} Total: {tot.get('duration_s', 0.0)}s / Budget: {tot.get('budget_s', 60.0)}s ({tot.get('status', 'PASS')}) | {stages_str}"
        )

# Global telemetry instance for convenient pipeline stage tracking
telemetry = PipelineTelemetry()
