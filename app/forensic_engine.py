# =====================================================================================
# app/forensic_engine.py
# PHASE 2: SECTOR-AWARE FORENSIC RISK ENGINE & DYNAMIC GROWTH INVESTMENT MODE EVALUATOR
# =====================================================================================

import logging
import math
from typing import Dict, Any, Optional, List, Tuple

def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Defensively casts value to float, handling None, NaN, and invalid strings gracefully."""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default

logger = logging.getLogger(__name__)

class ForensicRiskTier:
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    REJECT = "REJECT"

class ForensicEngine:
    """
    Sector-Aware Modular Forensic Risk Evaluator.
    Separates:
      1. Sector Classification: Identifies Financials (Banks/NBFCs/Insurance/AMCs) vs Non-Financials (Industrials/Consumer/Tech).
      2. Earnings Quality: 3Y Cumulative CFO / PAT as a hard gate (<0.6 -> REJECT) for Non-Financials; SECTOR_EXEMPT for Financials.
      3. Financial Solvency & Asset Quality: Evaluates ROA (<0.0 -> REJECT) and Forensic Red Flags (>=2 -> REJECT) for Financials.
      4. Capital Allocation & FCF Persistence: Graduated scoring signal (SECTOR_EXEMPT for Financials).
      5. Business Investment Context: Sector-tailored Growth Investment Score (0-100).
    Purely evaluative: returns structured dict {score, tier, flags, details}; scanner policies enforce rejection.
    """

    @staticmethod
    def is_financial_institution(fundamental_data: Dict[str, Any]) -> bool:
        """Determines if the stock is a Financial Institution (Bank, NBFC, Insurance, AMC)."""
        if not fundamental_data:
            return False
        path = str(fundamental_data.get("Path") or fundamental_data.get("path") or "").strip().lower()
        if path == "financial":
            return True
        sector = str(fundamental_data.get("Sector") or fundamental_data.get("sector") or "").strip().lower()
        if sector in ["finance", "financial services", "financials", "banking", "banks", "insurance", "nbfc", "nbfc_hfc", "amc"]:
            return True
        cats = fundamental_data.get("categories") or fundamental_data.get("Category") or fundamental_data.get("category") or []
        if isinstance(cats, str):
            cats = [cats]
        if any("financial" in str(c).lower() or "bank" in str(c).lower() for c in cats):
            return True
        return False

    @classmethod
    def calc_growth_investment_score(cls, fundamental_data: Dict[str, Any], is_financial: bool = False) -> Tuple[int, bool, Dict[str, Any]]:
        """
        Computes 0-100 Weighted Growth Investment Score tailored by sector:
        - Non-Financials: Capex/Sales (40%), Revenue CAGR 3Y (30%), ROCE (30%). Preconditions: Rev CAGR >= 12%, ROCE >= 15%.
        - Financials: Revenue/NII CAGR 3Y (40%), ROE (40%), ROA (20%). Preconditions: Rev CAGR >= 12%, ROE >= 15%, ROA >= 1.0%.
        Returns (growth_score, is_growth_mode, details_dict).
        """
        rev_cagr = safe_float(fundamental_data.get("revenue_cagr_3y") or fundamental_data.get("yoy_rev"), default=None)
        if rev_cagr is not None and rev_cagr > 1.0:
            rev_cagr = rev_cagr / 100.0  # Normalize percentage to ratio (e.g. 15% -> 0.15)

        if is_financial:
            # Financial Sector Growth Mode
            roe = safe_float(fundamental_data.get("roe") or fundamental_data.get("ROE %"), default=None)
            roa = safe_float(fundamental_data.get("roa") or fundamental_data.get("ROA %"), default=None)

            if rev_cagr is None or roe is None:
                return 0, False, {"status": "INSUFFICIENT_DATA", "reason": "Missing Rev CAGR or ROE for Financial Growth evaluation"}

            rev_cagr_pct = rev_cagr * 100.0
            roe_pct_val = roe * 100.0 if roe <= 1.0 else roe
            roa_pct_val = (roa * 100.0 if roa <= 0.1 else roa) if roa is not None else 1.0

            cagr_pts = min(100.0, max(0.0, (rev_cagr / 0.15) * 100.0))
            roe_pts = min(100.0, max(0.0, (roe_pct_val / 15.0) * 100.0))
            roa_pts = min(100.0, max(0.0, (roa_pct_val / 1.5) * 100.0))

            weighted_score = int(round(0.40 * cagr_pts + 0.40 * roe_pts + 0.20 * roa_pts))
            preconditions_met = (rev_cagr_pct >= 12.0) and (roe_pct_val >= 15.0) and (roa_pct_val >= 1.0)
            is_growth_mode = (weighted_score >= 60) and preconditions_met

            details = {
                "sector_type": "FINANCIAL",
                "revenue_cagr_pct": round(rev_cagr_pct, 1),
                "roe_pct": round(roe_pct_val, 1),
                "roa_pct": round(roa_pct_val, 2),
                "growth_score": weighted_score,
                "preconditions_met": preconditions_met,
                "growth_mode": is_growth_mode
            }
            return weighted_score, is_growth_mode, details

        # Non-Financial Sector Growth Mode
        capex_sales = safe_float(fundamental_data.get("capex_sales_ratio"), default=None)
        if capex_sales is None and "capex" in fundamental_data and "sales" in fundamental_data:
            c = safe_float(fundamental_data["capex"])
            s = safe_float(fundamental_data["sales"])
            if c is not None and s and s > 0:
                capex_sales = c / s

        roce = safe_float(fundamental_data.get("roce") or fundamental_data.get("ROCE %"), default=None)

        if capex_sales is None or rev_cagr is None or roce is None:
            return 0, False, {"status": "INSUFFICIENT_DATA", "reason": "Missing Capex/Sales, Rev CAGR, or ROCE"}

        capex_pts = min(100.0, max(0.0, (capex_sales / 0.15) * 100.0))
        cagr_pts = min(100.0, max(0.0, (rev_cagr / 0.15) * 100.0))
        roce_pts = min(100.0, max(0.0, (roce / 0.15) * 100.0))

        weighted_score = int(round(0.40 * capex_pts + 0.30 * cagr_pts + 0.30 * roce_pts))

        rev_cagr_pct = rev_cagr * 100.0
        roce_pct_val = roce * 100.0 if roce <= 1.0 else roce
        preconditions_met = (rev_cagr_pct >= 12.0) and (roce_pct_val >= 15.0)

        is_growth_mode = (weighted_score >= 60) and preconditions_met

        details = {
            "sector_type": "NON_FINANCIAL",
            "capex_sales_pct": round(capex_sales * 100.0, 1),
            "revenue_cagr_pct": round(rev_cagr_pct, 1),
            "roce_pct": round(roce_pct_val, 1),
            "growth_score": weighted_score,
            "preconditions_met": preconditions_met,
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

        is_financial = cls.is_financial_institution(fundamental_data)

        # ── 1. Universal Forensic Red Flags Gate ──────────────────────────────
        forensic_flags = safe_float(fundamental_data.get("forensic_flags"), default=0)
        if forensic_flags is not None and forensic_flags >= 2:
            return {
                "forensic_score": -30,
                "forensic_risk_tier": ForensicRiskTier.REJECT,
                "growth_investment_mode": False,
                "growth_investment_score": 0,
                "forensic_details": {
                    "status": "HARD_REJECT",
                    "reason": f"Forensic red flags ({int(forensic_flags)} detected) — auditor/accounting irregularities",
                    "fcf_penalty": -30,
                    "is_financial": is_financial
                }
            }

        # ── 2. Sector-Aware Earnings Quality / Solvency Gate ──────────────────
        if is_financial:
            # Financial Sector (Banks, NBFCs, Insurance):
            # CFO/PAT is SECTOR_EXEMPT because deposit mobilization and loan disbursements
            # are classified as operating cash flows under standard accounting rules.
            # Instead, evaluate core financial health: Return on Assets (ROA).
            roa = safe_float(fundamental_data.get("roa") or fundamental_data.get("ROA %"), default=None)
            if roa is not None and roa < 0.0:
                return {
                    "forensic_score": -30,
                    "forensic_risk_tier": ForensicRiskTier.REJECT,
                    "growth_investment_mode": False,
                    "growth_investment_score": 0,
                    "forensic_details": {
                        "roa": round(roa, 2),
                        "cfo_pat_status": "SECTOR_EXEMPT",
                        "reason": f"Negative ROA ({roa:.2f}%) in Financial sector — severe asset quality erosion",
                        "fcf_penalty": -30,
                        "is_financial": True
                    }
                }
            cfo_pat_status_msg = "SECTOR_EXEMPT"
            cfo_pat_display = "N/A (Financial Sector)"
        else:
            # Non-Financial Sector:
            # 3Y Cumulative CFO / PAT < 0.60 is an earnings quality hard stop
            cfo_pat_3y = safe_float(fundamental_data.get("cfo_pat_3y"), default=None)
            if cfo_pat_3y is None:
                cfo_pat_3y = safe_float(fundamental_data.get("cfo_pat") or fundamental_data.get("cfo_pat_ratio"), default=None)

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
                        "fcf_penalty": -30,
                        "is_financial": False
                    }
                }
            cfo_pat_status_msg = "PASS" if cfo_pat_3y is not None else "INSUFFICIENT_DATA"
            cfo_pat_display = round(cfo_pat_3y, 2) if cfo_pat_3y is not None else "N/A"

        # ── 3. Compute Weighted Growth Investment Score ───────────────────────
        growth_score, is_growth_mode, growth_details = cls.calc_growth_investment_score(fundamental_data, is_financial=is_financial)

        # ── 4. Evaluate FCF Persistence & Graduated Penalties ─────────────────
        if is_financial:
            penalty = 0
            neg_years = 0
            status_msg = "SECTOR_EXEMPT"
        else:
            fcf_history = fundamental_data.get("fcf_history", [])
            if not fcf_history and "fcf" in fundamental_data:
                fcf_val = safe_float(fundamental_data["fcf"])
                if fcf_val is not None:
                    fcf_history = [fcf_val]

            neg_years = sum(1 for v in fcf_history if safe_float(v) is not None and safe_float(v) < 0)
            is_3_consecutive_neg = (len(fcf_history) >= 3 and all(safe_float(v) is not None and safe_float(v) < 0 for v in fcf_history[-3:]))

            penalty = 0
            status_msg = "PASS"

            cfo_pat_val = safe_float(fundamental_data.get("cfo_pat_3y") or fundamental_data.get("cfo_pat") or fundamental_data.get("cfo_pat_ratio"), default=None)

            if is_3_consecutive_neg:
                if cfo_pat_val is not None and cfo_pat_val < 0.6:
                    return {
                        "forensic_score": -30,
                        "forensic_risk_tier": ForensicRiskTier.REJECT,
                        "growth_investment_mode": is_growth_mode,
                        "growth_investment_score": growth_score,
                        "forensic_details": {
                            "cfo_pat_3y": round(cfo_pat_val, 2) if cfo_pat_val is not None else "N/A",
                            "cfo_pat_status": "HARD_REJECT",
                            "reason": "3 consecutive years negative FCF + CFO/PAT < 0.6",
                            "fcf_penalty": -30,
                            "is_financial": False
                        }
                    }
                elif cfo_pat_val is not None and cfo_pat_val < 0.8:
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
            "is_financial": is_financial,
            "cfo_pat_3y": cfo_pat_display,
            "cfo_pat_status": cfo_pat_status_msg,
            "fcf_status": status_msg,
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
