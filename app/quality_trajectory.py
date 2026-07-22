import logging
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Defensively casts value to float, handling None, NaN, and invalid strings gracefully."""
    if val is None or pd.isna(val):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

# [VERSION: PHASE2_TRAJECTORY_RECALIB_v1.0]
# 1. Level-and-Trend Trajectory Scoring: pillar_score = max(level_score, trend_score)
#    Prevents penalizing already-elite stable companies (e.g., ROCE 35% flat).
# 2. Seasonality Smoothing: Uses TTM / 4-quarter rolling averages for slope calculations.

def _calc_trend_and_consistency(series: List[float]) -> Tuple[float, float]:
    """
    Computes (slope, variance) across a fundamental series with TTM rolling smoothing if len >= 4.
    Returns (0.0, 999.0) if series is insufficient or invalid.
    """
    valid = [safe_float(v) for v in series if safe_float(v) is not None]
    if len(valid) < 2:
        return 0.0, 999.0
        
    # Apply TTM / 4-quarter rolling average smoothing for seasonal metrics
    if len(valid) >= 4:
        smoothed = [float(np.mean(valid[i:i+4])) for i in range(len(valid) - 3)]
        if len(smoothed) >= 2:
            valid = smoothed

    x = np.arange(len(valid))
    y = np.array(valid)
    slope = float(np.polyfit(x, y, 1)[0]) if len(valid) >= 2 else 0.0
    variance = float(np.var(y)) if len(y) >= 2 else 0.0
    return slope, variance

def compute_trajectory_score(fundamental_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes a 0-20 pt Quality Trajectory Score and Grade based on 4-quarter fundamental trends & levels.
    Measures multi-quarter slope, level excellence, consistency, and graduated CFO/PAT quality.
    Total 20 pts: ROCE (0-4), ROE (0-4), OPM (0-3), Debt (0-3), Interest (0-3), CFO/PAT (0-3).
    Grade A: >= 18, Grade B: >= 15, Grade C: >= 10, Grade D: < 10.
    """
    if not fundamental_data:
        return {
            "trajectory_score": 0,
            "trajectory_grade": "UNKNOWN",
            "trajectory_details": {
                "status": "MISSING_DATA",
                "reason": "No fundamental metrics provided"
            }
        }

    score = 0
    details = {}

    # ── 1. ROCE Trend & Level (0-4 pts) ────────────────────────────────────────────────
    roce_history = fundamental_data.get("roce_history", [])
    if not roce_history and "roce" in fundamental_data:
        roce_history = [fundamental_data["roce"]]
    roce_slope, roce_var = _calc_trend_and_consistency(roce_history)
    
    trend_roce_pts = 0
    if roce_slope > 0.5 and roce_var < 50.0:
        trend_roce_pts = 4
    elif roce_slope > 0.0:
        trend_roce_pts = 2
    elif roce_slope >= -0.2:
        trend_roce_pts = 1

    last_roce = safe_float(roce_history[-1]) if roce_history else safe_float(fundamental_data.get("roce"))
    current_roce_val = (last_roce * 100.0 if last_roce <= 1.0 else last_roce) if last_roce is not None else 0.0
    
    level_roce_pts = 0
    if current_roce_val >= 25.0:
        level_roce_pts = 4
    elif current_roce_val >= 18.0:
        level_roce_pts = 3
    elif current_roce_val >= 12.0:
        level_roce_pts = 2
    elif current_roce_val >= 8.0:
        level_roce_pts = 1

    roce_pts = max(trend_roce_pts, level_roce_pts)
    score += roce_pts
    details["roce"] = f"{roce_pts}/4 pts (Level: {level_roce_pts}, Trend: {trend_roce_pts})"

    # ── 2. ROE Trend & Level (0-4 pts) ─────────────────────────────────────────────────
    roe_history = fundamental_data.get("roe_history", [])
    if not roe_history and "roe" in fundamental_data:
        roe_history = [fundamental_data["roe"]]
    roe_slope, roe_var = _calc_trend_and_consistency(roe_history)
    
    trend_roe_pts = 0
    if roe_slope > 0.5 and roe_var < 50.0:
        trend_roe_pts = 4
    elif roe_slope > 0.0:
        trend_roe_pts = 2
    elif roe_slope >= -0.2:
        trend_roe_pts = 1

    last_roe = safe_float(roe_history[-1]) if roe_history else safe_float(fundamental_data.get("roe"))
    current_roe_val = (last_roe * 100.0 if last_roe <= 1.0 else last_roe) if last_roe is not None else 0.0

    level_roe_pts = 0
    if current_roe_val >= 22.0:
        level_roe_pts = 4
    elif current_roe_val >= 15.0:
        level_roe_pts = 3
    elif current_roe_val >= 10.0:
        level_roe_pts = 2
    elif current_roe_val >= 6.0:
        level_roe_pts = 1

    roe_pts = max(trend_roe_pts, level_roe_pts)
    score += roe_pts
    details["roe"] = f"{roe_pts}/4 pts (Level: {level_roe_pts}, Trend: {trend_roe_pts})"

    # ── 3. OPM Trend & Level (0-3 pts) ─────────────────────────────────────────────────
    opm_history = fundamental_data.get("opm_history", [])
    if not opm_history and "opm" in fundamental_data:
        opm_history = [fundamental_data["opm"]]
    opm_slope, _ = _calc_trend_and_consistency(opm_history)
    
    trend_opm_pts = 0
    if opm_slope > 0.3:
        trend_opm_pts = 3
    elif opm_slope >= -0.1:
        trend_opm_pts = 1

    last_opm = safe_float(opm_history[-1]) if opm_history else safe_float(fundamental_data.get("opm"))
    current_opm_val = (last_opm * 100.0 if last_opm <= 1.0 else last_opm) if last_opm is not None else 0.0

    level_opm_pts = 0
    if current_opm_val >= 20.0:
        level_opm_pts = 3
    elif current_opm_val >= 14.0:
        level_opm_pts = 2
    elif current_opm_val >= 8.0:
        level_opm_pts = 1

    opm_pts = max(trend_opm_pts, level_opm_pts)
    score += opm_pts
    details["opm"] = f"{opm_pts}/3 pts (Level: {level_opm_pts}, Trend: {trend_opm_pts})"

    # ── 4. Debt-to-Equity Reduction (0-3 pts) ──────────────────────────────────────────
    de_history = fundamental_data.get("de_history", [])
    if not de_history and "debt_to_equity" in fundamental_data:
        de_history = [fundamental_data["debt_to_equity"]]
    de_slope, _ = _calc_trend_and_consistency(de_history)
    last_de = de_history[-1] if de_history else None
    current_de = safe_float(last_de, default=0.0)
    if current_de < 0.2:
        score += 3
        details["debt"] = "Debt-Free / Very Low"
    elif de_slope < -0.05:
        score += 3
        details["debt"] = "Reducing"
    elif de_slope <= 0.05:
        score += 1
        details["debt"] = "Stable"
    else:
        details["debt"] = "Increasing"

    # ── 5. Interest Coverage Ratio Improvement (0-3 pts) ──────────────────────────────
    icr_history = fundamental_data.get("icr_history", [])
    if not icr_history and "icr" in fundamental_data:
        icr_history = [fundamental_data["icr"]]
    icr_slope, _ = _calc_trend_and_consistency(icr_history)
    last_icr = icr_history[-1] if icr_history else None
    current_icr = safe_float(last_icr, default=0.0)
    if current_icr > 10.0 or icr_slope > 0.5:
        score += 3
        details["interest"] = "Strong / Expanding"
    elif current_icr > 3.0 or icr_slope >= 0.0:
        score += 1
        details["interest"] = "Adequate"
    else:
        details["interest"] = "Strained"

    # ── 6. Graduated CFO / PAT Quality (0-3 pts) ──────────────────────────────────────
    cfo_pat = safe_float(fundamental_data.get("cfo_pat"), default=0.0)
    details["cfo_pat"] = f"{cfo_pat:.2f}"
    if cfo_pat >= 1.0:
        score += 3
    elif cfo_pat >= 0.8:
        score += 2
    elif cfo_pat >= 0.6:
        score += 1

    # ── Trajectory Grade Mapping ──────────────────────────────────────────────────────
    if score >= 18:
        grade = "A"
    elif score >= 15:
        grade = "B"
    elif score >= 10:
        grade = "C"
    else:
        grade = "D"

    return {
        "trajectory_score": score,
        "trajectory_grade": grade,
        "trajectory_details": details
    }

