# =====================================================================================
# app/forensic_engine.py
# PHASE 2: FORENSIC RISK ENGINE & DYNAMIC GROWTH INVESTMENT MODE EVALUATOR
# =====================================================================================

import logging
from typing import Dict, Any, Optional, List, Tuple
import pandas as pd

from quality_trajectory import safe_float

logger = logging.getLogger(__name__)

class ForensicRiskTier:
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    REJECT = "REJECT"

class ForensicEngine:
    """
    Modular Forensic Risk Evaluator.
    Separates:
      1. Earnings Quality (3Y Cumulative CFO / PAT) as a hard gate (<0.6 -> REJECT).
      2. Capital Allocation & FCF Persistence as a graduated scoring signal.
      3. Business Investment Context (Growth Investment Score 0-100) evaluating Capex/Sales, Rev CAGR, ROCE.
    Purely evaluative: returns structured dict {score, tier, flags, details}; scanner policies enforce rejection.
    """

    @staticmethod
    def calc_growth_investment_score(fundamental_data: Dict[str, Any]) -> Tuple[int, bool, Dict[str, Any]]:
        """
        Computes 0-100 Weighted Growth Investment Score:
          - Capex / Sales Ratio (40% weight): 15% capex/sales -> 100 pts
          - Revenue CAGR 3Y (30% weight): 15% revenue CAGR -> 100 pts
          - ROCE (30% weight): 15% ROCE -> 100 pts
        Returns (growth_score, is_growth_mode, details_dict).
        """
        capex_sales = safe_float(fundamental_data.get("capex_sales_ratio"), default=None)
        if capex_sales is None and "capex" in fundamental_data and "sales" in fundamental_data:
            c = safe_float(fundamental_data["capex"])
            s = safe_float(fundamental_data["sales"])
            if c is not None and s and s > 0:
                capex_sales = c / s

        rev_cagr = safe_float(fundamental_data.get("revenue_cagr_3y"), default=None)
        roce = safe_float(fundamental_data.get("roce"), default=None)

        if capex_sales is None or rev_cagr is None or roce is None:
            return 0, False, {"status": "INSUFFICIENT_DATA", "reason": "Missing Capex/Sales, Rev CAGR, or ROCE"}

        # Sub-scores (normalized 0-100 scale anchored at 15% = 100 pts)
        capex_pts = min(100.0, max(0.0, (capex_sales / 0.15) * 100.0))
        cagr_pts = min(100.0, max(0.0, (rev_cagr / 0.15) * 100.0))
        roce_pts = min(100.0, max(0.0, (roce / 0.15) * 100.0))

        weighted_score = int(round(0.40 * capex_pts + 0.30 * cagr_pts + 0.30 * roce_pts))
        is_growth_mode = weighted_score >= 60

        details = {
            "capex_sales_pct": round(capex_sales * 100.0, 1),
            "revenue_cagr_pct": round(rev_cagr * 100.0, 1),
            "roce_pct": round(roce * 100.0, 1) if roce <= 1.0 else round(roce, 1),
            "growth_score": weighted_score,
            "growth_mode": is_growth_mode
        }

        return weighted_score, is_growth_mode, details

    @classmethod
    def evaluate_symbol(cls, fundamental_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates a symbol's fundamental data and returns a structured evaluation dict:
        {
            "forensic_score": int,
            "forensic_risk_tier": str,
            "growth_investment_mode": bool,
            "growth_investment_score": int,
            "forensic_details": dict
        }
        """
        if not fundamental_data:
            return {
                "forensic_score": 0,
                "forensic_risk_tier": ForensicRiskTier.UNKNOWN,
                "growth_investment_mode": False,
                "growth_investment_score": 0,
                "forensic_details": {
                    "status": "MISSING_DATA",
                    "reason": "No fundamental data dictionary provided"
                }
            }

        # 1. Primary Hard Gate Check (3Y Cumulative CFO / PAT < 0.6)
        cfo_pat_3y = safe_float(fundamental_data.get("cfo_pat_3y"), default=None)
        if cfo_pat_3y is None:
            # Fallback to single-year cfo_pat if 3y cumulative is not provided
            cfo_pat_3y = safe_float(fundamental_data.get("cfo_pat"), default=None)

        if cfo_pat_3y is not None and cfo_pat_3y < 0.6:
            return {
                "forensic_score": -30,
                "forensic_risk_tier": ForensicRiskTier.REJECT,
                "growth_investment_mode": False,
                "growth_investment_score": 0,
                "forensic_details": {
                    "cfo_pat_3y": round(cfo_pat_3y, 2),
                    "cfo_pat_status": "HARD_REJECT",
                    "reason": f"3Y Cumulative CFO/PAT ({cfo_pat_3y:.2f}) < 0.60 threshold",
                    "fcf_penalty": -30
                }
            }

        # 2. Compute Weighted Growth Investment Score
        growth_score, is_growth_mode, growth_details = cls.calc_growth_investment_score(fundamental_data)

        # 3. Evaluate FCF Persistence & Graduated Penalties
        fcf_history = fundamental_data.get("fcf_history", [])
        if not fcf_history and "fcf" in fundamental_data:
            fcf_val = safe_float(fundamental_data["fcf"])
            if fcf_val is not None:
                fcf_history = [fcf_val]

        neg_years = sum(1 for v in fcf_history if safe_float(v) is not None and safe_float(v) < 0)
        is_3_consecutive_neg = (len(fcf_history) >= 3 and all(safe_float(v) is not None and safe_float(v) < 0 for v in fcf_history[-3:]))

        penalty = 0
        status_msg = "PASS"

        if is_3_consecutive_neg:
            if cfo_pat_3y is not None and cfo_pat_3y < 0.6:
                return {
                    "forensic_score": -30,
                    "forensic_risk_tier": ForensicRiskTier.REJECT,
                    "growth_investment_mode": is_growth_mode,
                    "growth_investment_score": growth_score,
                    "forensic_details": {
                        "cfo_pat_3y": round(cfo_pat_3y, 2) if cfo_pat_3y is not None else "N/A",
                        "cfo_pat_status": "HARD_REJECT",
                        "reason": "3 consecutive years negative FCF + CFO/PAT < 0.6",
                        "fcf_penalty": -30
                    }
                }
            elif cfo_pat_3y is not None and cfo_pat_3y < 0.8:
                penalty = -5 if is_growth_mode else -20
                status_msg = "HEAVY_FCF_PENALTY"
            else:
                penalty = -3 if is_growth_mode else -10
                status_msg = "MODERATE_FCF_PENALTY"
        elif neg_years >= 2:
            penalty = 0 if is_growth_mode else -5
            status_msg = "MINOR_FCF_PENALTY"

        forensic_score = penalty

        # Determine Risk Tier
        if forensic_score == 0:
            tier = ForensicRiskTier.LOW
        elif forensic_score >= -5:
            tier = ForensicRiskTier.LOW
        elif forensic_score >= -15:
            tier = ForensicRiskTier.MEDIUM
        else:
            tier = ForensicRiskTier.HIGH

        forensic_details = {
            "cfo_pat_3y": round(cfo_pat_3y, 2) if cfo_pat_3y is not None else "N/A",
            "cfo_pat_status": status_msg,
            "fcf_penalty": penalty,
            "growth_mode": is_growth_mode,
            "growth_score": growth_score,
            "growth_details": growth_details,
            "negative_fcf_years": neg_years
        }

        return {
            "forensic_score": forensic_score,
            "forensic_risk_tier": tier,
            "growth_investment_mode": is_growth_mode,
            "growth_investment_score": growth_score,
            "forensic_details": forensic_details
        }
