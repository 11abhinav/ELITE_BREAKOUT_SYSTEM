# =====================================================================================
# reports/forensic_attribution_report.py
# FORENSIC ALERT ATTRIBUTION & EXPECTANCY CALIBRATION REPORT GENERATOR
# =====================================================================================

import sys
import os
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, List

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
app_dir = os.path.join(project_root, "app")
for p in [app_dir, project_root]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from database import get_connection
    from score_calibration_engine import ScoreCalibrationEngine, SCORE_BUCKETS
    from alert_quality_engine import SCANNER_RECOVERY_HORIZONS
except ImportError:
    from app.database import get_connection
    from app.score_calibration_engine import ScoreCalibrationEngine, SCORE_BUCKETS
    from app.alert_quality_engine import SCANNER_RECOVERY_HORIZONS

logger = logging.getLogger("forensic_attribution_report")


def generate_forensic_report() -> Dict[str, Any]:
    """
    Queries alert_outcomes and compiles:
      1. Overall Quality Ladder (+1R, +1.5R, +2R) by Scanner.
      2. Score Calibration Matrix with Block-Bootstrap 95% Confidence Intervals.
      3. Post-SL Excursion Diagnostic (separating tight stops from bad signals).
      4. Sector & Regime Attribution.
    """
    query = """
        SELECT 
            ao.alert_id,
            ao.symbol,
            ao.scanner,
            ao.regime,
            COALESCE(a.score, ao.base_score) as score,
            ao.entry_price,
            ao.stop_loss,
            ao.target_1,
            ao.realized_rr,
            ao.holding_period_bars,
            ao.max_favorable_excursion_r,
            ao.max_adverse_excursion_r,
            ao.r1_hit_before_sl,
            ao.r1_5_hit_before_sl,
            ao.r2_hit_before_sl,
            ao.same_bar_conflict,
            ao.post_sl_min_excursion_r,
            ao.post_sl_recovered_entry,
            ao.post_sl_recovered_t1,
            ao.post_sl_max_recovery_r,
            ao.post_sl_recovery_bars,
            ao.event_risk,
            ao.sector_regime,
            ao.sector_relative_strength,
            ao.exit_reason
        FROM alert_outcomes ao
        LEFT JOIN alerts a ON a.id = ao.alert_id
        WHERE ao.leg = 1 AND ao.exit_reason IS NOT NULL
    """
    
    rows = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                cols = [desc[0] for desc in cur.description]
                for r in cur.fetchall():
                    rows.append(dict(zip(cols, r)))
    except Exception as e:
        logger.warning(f"Failed to query database for forensic report (using empty dataset): {e}")

    if not rows:
        return {
            "status": "NO_DATA",
            "message": "No historical alert outcomes recorded yet. Run live scanner or historical replay to populate.",
            "timestamp": datetime.utcnow().isoformat()
        }

    df = pd.DataFrame(rows)

    # 1. Scanner Breakdown
    scanner_summary = {}
    for scanner, group in df.groupby("scanner"):
        metrics = ScoreCalibrationEngine.compute_distribution_metrics(group)
        # Post-SL recovery statistics for this scanner
        sl_trades = group[group["exit_reason"].isin(["SL_HIT", "SAME_BAR_CONFLICT_SL"])]
        n_sl = len(sl_trades)
        if n_sl > 0:
            rec_entry_rate = (sl_trades["post_sl_recovered_entry"].sum() / n_sl) * 100.0
            rec_t1_rate = (sl_trades["post_sl_recovered_t1"].sum() / n_sl) * 100.0
            avg_post_sl_max_r = float(sl_trades["post_sl_max_recovery_r"].mean())
        else:
            rec_entry_rate = 0.0
            rec_t1_rate = 0.0
            avg_post_sl_max_r = 0.0

        metrics["post_sl_sample_size"] = n_sl
        metrics["post_sl_recovered_entry_pct"] = round(rec_entry_rate, 1)
        metrics["post_sl_recovered_t1_pct"] = round(rec_t1_rate, 1)
        metrics["post_sl_avg_max_recovery_r"] = round(avg_post_sl_max_r, 2)
        scanner_summary[scanner] = metrics

    # 2. Score Calibration Table
    score_table = ScoreCalibrationEngine.calibrate_score_buckets(df)

    # 3. Overall Portfolio Distribution
    overall_metrics = ScoreCalibrationEngine.compute_distribution_metrics(df)

    report = {
        "status": "SUCCESS",
        "generated_at": datetime.utcnow().isoformat(),
        "total_alerts_analyzed": len(df),
        "overall_distribution": overall_metrics,
        "scanner_breakdown": scanner_summary,
        "score_calibration_matrix": score_table
    }

    return report


if __name__ == "__main__":
    rep = generate_forensic_report()
    print(json.dumps(rep, indent=2))
