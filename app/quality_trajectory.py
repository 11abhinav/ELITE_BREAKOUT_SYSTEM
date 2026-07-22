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

def _calc_trend_and_consistency(series: List[float]) -> Tuple[float, float]:
    """
    Computes (slope, variance) across a 4-quarter fundamental series.
    Returns (0.0, 999.0) if series is insufficient or invalid.
    """
    valid = [safe_float(v) for v in series if safe_float(v) is not None]
    if len(valid) < 2:
        return 0.0, 999.0
    x = np.arange(len(valid))
    y = np.array(valid)
    slope = float(np.polyfit(x, y, 1)[0]) if len(valid) >= 2 else 0.0
    variance = float(np.var(y)) if len(y) >= 2 else 0.0
    return slope, variance

def compute_trajectory_score(fundamental_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes a 0-20 pt Quality Trajectory Score and Grade based on 4-quarter fundamental trends.
    Measures multi-quarter slope, consistency, and graduated CFO/PAT quality.
    Returns UNKNOWN grade when fundamental data is missing/insufficient.
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

    # ── 1. ROCE Trend (0-4 pts) ────────────────────────────────────────────────────────
    roce_history = fundamental_data.get("roce_history", [])
    if not roce_history and "roce" in fundamental_data:
        roce_history = [fundamental_data["roce"]]
    roce_slope, roce_var = _calc_trend_and_consistency(roce_history)
    if roce_slope > 0.5 and roce_var < 50.0:
        score += 4
        details["roce"] = "Improving (High Consistency)"
    elif roce_slope > 0.0:
        score += 2
        details["roce"] = "Improving"
    elif roce_slope >= -0.2:
        score += 1
        details["roce"] = "Stable"
    else:
        details["roce"] = "Deteriorating"

    # ── 2. ROE Trend (0-4 pts) ─────────────────────────────────────────────────────────
    roe_history = fundamental_data.get("roe_history", [])
    if not roe_history and "roe" in fundamental_data:
        roe_history = [fundamental_data["roe"]]
    roe_slope, roe_var = _calc_trend_and_consistency(roe_history)
    if roe_slope > 0.5 and roe_var < 50.0:
        score += 4
        details["roe"] = "Improving (High Consistency)"
    elif roe_slope > 0.0:
        score += 2
        details["roe"] = "Improving"
    elif roe_slope >= -0.2:
        score += 1
        details["roe"] = "Stable"
    else:
        details["roe"] = "Deteriorating"

    # ── 3. OPM Trend (0-3 pts) ─────────────────────────────────────────────────────────
    opm_history = fundamental_data.get("opm_history", [])
    if not opm_history and "opm" in fundamental_data:
        opm_history = [fundamental_data["opm"]]
    opm_slope, _ = _calc_trend_and_consistency(opm_history)
    if opm_slope > 0.3:
        score += 3
        details["opm"] = "Expanding"
    elif opm_slope >= -0.1:
        score += 1
        details["opm"] = "Stable"
    else:
        details["opm"] = "Contracting"

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

