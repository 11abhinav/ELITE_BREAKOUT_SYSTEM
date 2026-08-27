"""
universe_quality_score.py
=========================
Phase 2A Daily Builder V2 — Universe Quality Scoring Engine.

Implements the 4-sub-score model from the approved specification:
  Sub-score 1: Business Quality   (0–30 pts)
  Sub-score 2: Growth Quality     (0–35 pts)
  Sub-score 3: Valuation Context  (0–20 pts)
  Sub-score 4: Governance         (0–15 pts)
  Total                           (0–100 pts, capped)

Five path-specific scorers are exposed (one per financial sub-path):
  score_nonfin   — non-financial stocks
  score_bank     — BANK sub-path (ROA replaces ROE weight; NIM/NPA/CAR/CASA gate)
  score_nbfc     — NBFC_HFC sub-path
  score_insurance — INSURANCE sub-path
  score_amc      — AMC sub-path (AUM-growth replaces capex/FCF)

All scorers return a ScoreBreakdown which carries both the integer sub-scores
AND a DataCoverage object (INV-2 compliance: data_confidence is NEVER silent).

[INV-3] FundamentalProfile schema is defined in universe_checklist.py.
        This module is responsible for the numeric scoring only.

[VERSION: DAILY_BUILDER_V2_SCORE_v1.0]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# GAP_TOLERANCE_BY_METRIC
# [INV-5] Provisional values — must be calibrated from 90-day replay.
# Do NOT change these values based on intuition or universe-count targets.
# ---------------------------------------------------------------------------
GAP_TOLERANCE_BY_METRIC: dict[str, float] = {
    # metric key        : max relative gap (fraction of threshold value)
    "roe":              0.15,   # ROE 13.5% vs 15% threshold = 10% gap → OK
    "roce":             0.15,
    "opm":              0.20,   # OPM is more volatile; wider tolerance
    "debt_equity":      0.10,   # D/E tight — 1.1 vs 1.0 = 10% gap → borderline
    "cfo_pat_ratio":    0.12,   # CFO/PAT 0.44 vs 0.50 = 12% → acceptable
    "fcf_margin":       0.25,   # FCF is variable; wider
    "yoy_sales":        0.20,
    "yoy_profit":       0.20,
    "roa":              0.15,   # Financial path: ROA
    "nim":              0.15,   # BANK: NIM
    "car":              0.10,   # BANK/NBFC: CAR (tighter)
    "gnpa":             0.10,   # BANK/NBFC: GNPA (tighter — credit risk)
}

# ELITE_UNIVERSE score threshold — PROVISIONAL (Q2).
# [INV-5] Must be set from replay data, not by intuition.
ELITE_SCORE_THRESHOLD: int = 45        # provisional
NEAR_QUALIFIED_SCORE_MIN: int = 35     # provisional lower bound
NEAR_QUALIFIED_SCORE_MAX: int = 44     # provisional upper bound (= ELITE - 1)

# SINGLE_GAP mode minimum score requirement
NEAR_QUALIFIED_SINGLE_GAP_MIN_SCORE: int = 38  # provisional


# ---------------------------------------------------------------------------
# DataCoverage
# [INV-2] Every admitted row MUST carry data_confidence alongside the score.
# ---------------------------------------------------------------------------
@dataclass
class DataCoverage:
    """
    Coverage fraction (0.0–1.0) per sub-score and overall confidence band.

    [INV-2] universe_quality_score and data_confidence must always travel
    together. A downstream scanner must never read the score in isolation.
    """
    business_quality_coverage: float = 0.0
    growth_quality_coverage:   float = 0.0
    valuation_coverage:        float = 0.0
    governance_coverage:       float = 0.0
    # Derived — set by _classify_data_confidence()
    overall_data_confidence:   str   = "LOW"

    def as_dict(self) -> dict:
        return {
            "business_quality_coverage": round(self.business_quality_coverage, 3),
            "growth_quality_coverage":   round(self.growth_quality_coverage, 3),
            "valuation_coverage":        round(self.valuation_coverage, 3),
            "governance_coverage":       round(self.governance_coverage, 3),
            "overall_data_confidence":   self.overall_data_confidence,
        }


def _classify_data_confidence(dc: DataCoverage) -> str:
    """
    Determines overall_data_confidence from per-sub-score coverage ratios.

    HIGH   → all 4 coverage ratios ≥ 0.75
    MEDIUM → all 4 ratios ≥ 0.50 OR 3 of 4 ≥ 0.75
    LOW    → any ratio < 0.50

    [INV-2] This is a first-class classification, not a hint.
    """
    ratios = [
        dc.business_quality_coverage,
        dc.growth_quality_coverage,
        dc.valuation_coverage,
        dc.governance_coverage,
    ]
    if all(r >= 0.75 for r in ratios):
        return "HIGH"
    if all(r >= 0.50 for r in ratios) or sum(1 for r in ratios if r >= 0.75) >= 3:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# ScoreBreakdown
# ---------------------------------------------------------------------------
@dataclass
class ScoreBreakdown:
    """
    Full 4-sub-score result returned by every score_* function.

    The `total` field is the capped (0–100) Universe Quality Score.
    `data_coverage` must always be consumed alongside `total` — see INV-2.
    """
    business_quality:  int = 0   # 0–30
    growth_quality:    int = 0   # 0–35
    valuation_context: int = 0   # 0–20
    governance:        int = 0   # 0–15
    total:             int = 0   # 0–100 (sum, capped)
    quality_tier:      str = ""  # "A+" | "A" | "B" | "C" | "" (only for ELITE)
    data_coverage:     DataCoverage = field(default_factory=DataCoverage)

    def as_dict(self) -> dict:
        return {
            "business_quality":  self.business_quality,
            "growth_quality":    self.growth_quality,
            "valuation_context": self.valuation_context,
            "governance":        self.governance,
            "universe_quality_score": self.total,
            "quality_tier":      self.quality_tier,
            **self.data_coverage.as_dict(),
        }


def _quality_tier(score: int) -> str:
    """Quality tier within ELITE universe. Empty string if not ELITE."""
    if score >= 75:
        return "A+"
    if score >= 60:
        return "A"
    if score >= 50:
        return "B"
    if score >= ELITE_SCORE_THRESHOLD:
        return "C"
    return ""


def _safe(metrics: dict, key: str, default=None):
    """
    Returns metrics[key] if present and not None/NaN, else default.
    Silently absorbs import dependency on pandas if unavailable.
    """
    val = metrics.get(key)
    if val is None:
        return default
    try:
        import math as _math
        if isinstance(val, float) and _math.isnan(val):
            return default
    except Exception:
        pass
    return val


def _pct(val) -> Optional[float]:
    """
    Normalise a percentage-or-ratio value to fractional (0.0–1.0) scale.
    TradingView delivers ROE=18.4 (percentage); internal fundamentals_cache
    may deliver 0.184 (ratio).  Values > 1.0 are treated as percentages
    and divided by 100.
    """
    if val is None:
        return None
    try:
        v = float(val)
        if abs(v) > 1.0:
            return v / 100.0
        return v
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Sub-score 1 helpers  (Non-Financial path)
# ---------------------------------------------------------------------------

def _bq_nonfin(m: dict) -> tuple[int, int, int]:
    """
    Returns (business_quality_score, max_possible, fields_populated).
    Max possible = 30.
    """
    score = 0
    populated = 0
    fields_total = 5  # ROE, ROCE, OPM, FCF Margin, D/E

    roe = _pct(_safe(m, "return_on_equity_fy") or _safe(m, "roe"))
    if roe is not None:
        populated += 1
        if roe >= 0.25:   score += 8
        elif roe >= 0.20: score += 6
        elif roe >= 0.15: score += 3

    roce = _pct(_safe(m, "return_on_invested_capital_fq") or _safe(m, "roce"))
    if roce is not None:
        populated += 1
        if roce >= 0.20:   score += 5
        elif roce >= 0.15: score += 3

    opm = _pct(_safe(m, "operating_margin_ttm") or _safe(m, "opm"))
    if opm is not None:
        populated += 1
        if opm >= 0.20:   score += 5
        elif opm >= 0.15: score += 3
        elif opm >= 0.10: score += 1

    fcf = _pct(_safe(m, "free_cash_flow_margin_ttm") or _safe(m, "fcf_margin"))
    if fcf is not None:
        populated += 1
        if fcf >= 0.15:   score += 7
        elif fcf >= 0.08: score += 5
        elif fcf > 0.00:  score += 2
        elif fcf < -0.05: score -= 3

    de = _safe(m, "debt_equity") or _safe(m, "debt_to_equity")
    if de is not None:
        try:
            de = float(de)
            populated += 1
            if de == 0.0:  score += 5
            elif de <= 0.5: score += 3
            elif de <= 1.0: score += 1
        except (TypeError, ValueError):
            pass

    coverage = populated / fields_total
    return max(0, score), 30, coverage


def _gq_nonfin(m: dict) -> tuple[int, int, float]:
    """Growth Quality score for non-financial path. Max = 35."""
    score = 0
    populated = 0
    fields_total = 8  # YoY rev, YoY profit, QoQ rev, QoQ profit, 5Y rev CAGR, 5Y EPS CAGR, margin bonus, anomaly-adjusted

    yoy_rev = _pct(_safe(m, "total_revenue_yoy_growth_ttm") or _safe(m, "yoy_revenue"))
    if yoy_rev is not None:
        populated += 1
        if yoy_rev >= 0.20:   score += 8
        elif yoy_rev >= 0.10: score += 4
        elif yoy_rev > 0.00:  score += 1

    yoy_profit = _pct(_safe(m, "earnings_per_share_diluted_yoy_growth_ttm") or _safe(m, "yoy_profit"))
    if yoy_profit is not None:
        populated += 1
        if yoy_profit >= 0.25:   score += 12
        elif yoy_profit >= 0.15: score += 7
        elif yoy_profit >= 0.05: score += 3

    qoq_rev = _pct(_safe(m, "total_revenue_qoq_growth_fq"))
    if qoq_rev is not None:
        populated += 1
        if qoq_rev >= 0.10:   score += 5
        elif qoq_rev >= 0.05: score += 2

    qoq_profit = _pct(_safe(m, "earnings_per_share_diluted_qoq_growth_fq"))
    if qoq_profit is not None:
        populated += 1
        if qoq_profit >= 0.10:   score += 5
        elif qoq_profit >= 0.05: score += 2

    cagr_rev5 = _pct(_safe(m, "total_revenue_5y_growth") or _safe(m, "revenue_cagr_5y"))
    if cagr_rev5 is not None:
        populated += 1
        if cagr_rev5 >= 0.12:
            score += 2.5

    cagr_eps5 = _pct(_safe(m, "earnings_per_share_basic_5y_growth") or _safe(m, "eps_cagr_5y"))
    if cagr_eps5 is not None:
        populated += 1
        if cagr_eps5 >= 0.15:
            score += 2.5

    # Margin-expansion bonus: profit growth ≥ revenue growth
    if yoy_profit is not None and yoy_rev is not None:
        populated += 1
        if yoy_profit >= yoy_rev and yoy_profit > 0:
            score += 1

    coverage = populated / fields_total
    return min(35, max(0, int(score))), 35, coverage


def _vc_common(m: dict) -> tuple[int, int, float]:
    """
    Valuation Context sub-score — same formula for all paths. Max = 20.

    [IMPORTANT] Valuation may influence ranking but must NOT eliminate
    an otherwise excellent company. A Valuation sub-score of 0 does not
    cause exclusion if Business Quality + Growth Quality + Governance
    produce a total ≥ ELITE_SCORE_THRESHOLD. (Enforced by the 4-sub-score
    architecture — no single sub-score can override the total.)
    """
    score = 0
    populated = 0
    fields_total = 3  # PEG, PE/FCF yield, long-term PEG

    pe = _safe(m, "price_earnings_ttm") or _safe(m, "pe_ratio")
    pb = _safe(m, "price_book_ratio")
    yoy_profit = _pct(_safe(m, "earnings_per_share_diluted_yoy_growth_ttm") or _safe(m, "yoy_profit"))

    # PEG ratio
    peg = _safe(m, "peg_ratio")
    if peg is None and pe is not None and yoy_profit is not None and yoy_profit > 0:
        try:
            peg = float(pe) / (float(yoy_profit) * 100.0)  # growth as %
        except (TypeError, ValueError, ZeroDivisionError):
            peg = None

    if peg is not None:
        try:
            peg = float(peg)
            populated += 1
            if 0.0 < peg <= 1.0:   score += 10
            elif peg <= 2.0:        score += 5
            # > 2.0 → 0 pts
        except (TypeError, ValueError):
            pass
    else:
        score += 5  # neutral when missing (per spec)

    # PE / FCF yield
    if pe is not None:
        try:
            pe_f = float(pe)
            populated += 1
            if pe_f < 20:    score += 7
            elif pe_f < 30:  score += 4
            elif pe_f < 40:  score += 2
            # ≥ 40 → 0 pts (not exclusion)
        except (TypeError, ValueError):
            pass
    else:
        score += 3  # neutral when missing

    # Long-term PEG: pe / (5Y EPS CAGR * 100)
    cagr_eps5 = _pct(_safe(m, "earnings_per_share_basic_5y_growth") or _safe(m, "eps_cagr_5y"))
    lt_peg = None
    if pe is not None and cagr_eps5 is not None and cagr_eps5 > 0:
        try:
            lt_peg = float(pe) / (cagr_eps5 * 100.0)
        except (TypeError, ValueError, ZeroDivisionError):
            lt_peg = None

    if lt_peg is not None:
        populated += 1
        if lt_peg <= 2.0:
            score += 3
    else:
        score += 1  # neutral when missing

    coverage = populated / fields_total
    return min(20, max(0, score)), 20, coverage


def _gov_common(m: dict) -> tuple[int, int, float]:
    """Governance sub-score — same for all paths. Max = 15."""
    score = 0
    populated = 0
    fields_total = 3  # forensic, insider hold, dividend

    # Forensic risk
    forensic = _safe(m, "forensic_risk") or _safe(m, "forensic_flags")
    if forensic is not None:
        populated += 1
        try:
            fv = int(forensic)
            if fv == 0:    score += 7  # CLEAN
            elif fv == 1:  score += 5  # LOW_RISK
            elif fv == 2:  score += 2  # MEDIUM_RISK
            # HIGH_RISK (fv >= 3) → 0 pts; junk gate will exclude if fv >= 2
        except (TypeError, ValueError):
            # String-form forensic_risk label
            fl = str(forensic).upper()
            if fl in ("CLEAN", "NONE"):      score += 7
            elif fl == "LOW_RISK":           score += 5
            elif fl == "MEDIUM_RISK":        score += 2
    else:
        score += 2  # neutral when missing (forensic is OPTIONAL)

    # Insider / promoter hold
    insider = _pct(_safe(m, "insider_hold") or _safe(m, "promoter_holding_pct"))
    if insider is not None:
        populated += 1
        if insider > 0.50:    score += 5
        elif insider > 0.40:  score += 3
        elif insider > 0.30:  score += 1
    else:
        score += 2  # neutral when missing

    # Dividend consistency
    div_yield = _safe(m, "dividend_yield_recent") or _safe(m, "dividend_yield")
    if div_yield is not None:
        try:
            dy = float(div_yield)
            if dy >= 0.03:  # ≥ 3% yield qualifies (3Y consistency presumed from fundamentals_cache)
                populated += 1
                score += 3
            else:
                populated += 1  # field present but yield too low
        except (TypeError, ValueError):
            pass
    # missing div yield → 0 pts (no neutral; spec does not award points for "unknown dividend")

    coverage = (populated / fields_total) if fields_total > 0 else 0.0
    return min(15, max(0, score)), 15, coverage


# ---------------------------------------------------------------------------
# get_institutional_context
# NOT included in score — separate field (see spec §3.9 and INV-2)
# ---------------------------------------------------------------------------

def get_institutional_context(metrics: dict) -> dict:
    """
    Returns institutional context fields that travel alongside the score
    but do NOT affect universe admission or quality score.

    Fields:
      institutional_interest: 'STRONG' | 'MODERATE' | 'WEAK' | 'UNKNOWN'
      delivery_pct_5d:        float | None
      has_institutional_buyers: bool
      block_deals_30d:        int
    """
    delivery = _safe(metrics, "delivery_pct_5d") or _safe(metrics, "delivery_volume_pct")
    inst_buy  = bool(_safe(metrics, "has_institutional_buyers", False))
    block_deals = int(_safe(metrics, "block_deals_30d", 0) or 0)

    interest = "UNKNOWN"
    if delivery is not None:
        try:
            dp = float(delivery)
            if dp >= 0.60 or inst_buy or block_deals >= 2:
                interest = "STRONG"
            elif dp >= 0.45 or inst_buy:
                interest = "MODERATE"
            else:
                interest = "WEAK"
        except (TypeError, ValueError):
            pass

    return {
        "institutional_interest":  interest,
        "delivery_pct_5d":         delivery,
        "has_institutional_buyers": inst_buy,
        "block_deals_30d":         block_deals,
    }


# ---------------------------------------------------------------------------
# NON-FINANCIAL scorer
# ---------------------------------------------------------------------------

def score_nonfin(metrics: dict) -> ScoreBreakdown:
    """
    Universe Quality Score for non-financial stocks.
    Returns a ScoreBreakdown with 4 sub-scores, total, and DataCoverage.
    """
    bq, _, bq_cov = _bq_nonfin(metrics)
    gq, _, gq_cov = _gq_nonfin(metrics)
    vc, _, vc_cov = _vc_common(metrics)
    gv, _, gv_cov = _gov_common(metrics)

    total = min(100, bq + gq + vc + gv)

    dc = DataCoverage(
        business_quality_coverage = bq_cov,
        growth_quality_coverage   = gq_cov,
        valuation_coverage        = vc_cov,
        governance_coverage       = gv_cov,
    )
    dc.overall_data_confidence = _classify_data_confidence(dc)

    return ScoreBreakdown(
        business_quality  = bq,
        growth_quality    = gq,
        valuation_context = vc,
        governance        = gv,
        total             = total,
        quality_tier      = _quality_tier(total),
        data_coverage     = dc,
    )


# ---------------------------------------------------------------------------
# BANK scorer
# Business Quality uses ROA (not ROE), NIM, GNPA, CAR, CASA instead of OPM/D/E/FCF
# ---------------------------------------------------------------------------

def _bq_bank(m: dict) -> tuple[int, int, float]:
    """Business Quality for BANK path. Max = 30."""
    score = 0
    populated = 0
    fields_total = 5  # ROA, NIM, GNPA, CAR, CASA (replaces ROE/ROCE/OPM/FCF/D/E)

    roa = _pct(_safe(m, "return_on_assets_fq") or _safe(m, "roa"))
    if roa is not None:
        populated += 1
        if roa >= 0.018:   score += 8   # ≥ 1.8% → 8 pts (strong bank)
        elif roa >= 0.012: score += 6   # ≥ 1.2%
        elif roa >= 0.008: score += 3   # ≥ 0.8% (minimum for pass — see junk gate)

    nim = _pct(_safe(m, "net_interest_margin") or _safe(m, "nim"))
    if nim is not None:
        populated += 1
        if nim >= 0.04:    score += 5
        elif nim >= 0.03:  score += 3

    gnpa = _pct(_safe(m, "gnpa") or _safe(m, "gross_npa_ratio"))
    if gnpa is not None:
        populated += 1
        if gnpa <= 0.02:   score += 8   # excellent
        elif gnpa <= 0.03: score += 6
        elif gnpa <= 0.05: score += 3
        # > 5% → 0 pts; junk gate excludes at > 7%

    car = _pct(_safe(m, "capital_adequacy_ratio") or _safe(m, "car"))
    if car is not None:
        populated += 1
        if car >= 0.16:    score += 5
        elif car >= 0.14:  score += 3
        elif car >= 0.12:  score += 1   # regulatory minimum

    casa = _pct(_safe(m, "casa_ratio") or _safe(m, "casa"))
    if casa is not None:
        populated += 1
        if casa >= 0.45:   score += 4
        elif casa >= 0.35: score += 2

    coverage = populated / fields_total
    return min(30, max(0, score)), 30, coverage


def score_bank(metrics: dict) -> ScoreBreakdown:
    """Universe Quality Score for BANK sub-path."""
    bq, _, bq_cov = _bq_bank(metrics)
    gq, _, gq_cov = _gq_nonfin(metrics)   # Growth sub-score is path-agnostic
    vc, _, vc_cov = _vc_common(metrics)
    gv, _, gv_cov = _gov_common(metrics)

    total = min(100, bq + gq + vc + gv)

    dc = DataCoverage(
        business_quality_coverage = bq_cov,
        growth_quality_coverage   = gq_cov,
        valuation_coverage        = vc_cov,
        governance_coverage       = gv_cov,
    )
    dc.overall_data_confidence = _classify_data_confidence(dc)

    return ScoreBreakdown(
        business_quality  = bq,
        growth_quality    = gq,
        valuation_context = vc,
        governance        = gv,
        total             = total,
        quality_tier      = _quality_tier(total),
        data_coverage     = dc,
    )


# ---------------------------------------------------------------------------
# NBFC_HFC scorer
# Similar to BANK but D/E is more relevant (NBFCs use leverage differently)
# ---------------------------------------------------------------------------

def _bq_nbfc(m: dict) -> tuple[int, int, float]:
    """Business Quality for NBFC_HFC path. Max = 30."""
    score = 0
    populated = 0
    fields_total = 5

    roa = _pct(_safe(m, "return_on_assets_fq") or _safe(m, "roa"))
    if roa is not None:
        populated += 1
        if roa >= 0.025:   score += 8
        elif roa >= 0.015: score += 6
        elif roa >= 0.008: score += 3

    roe = _pct(_safe(m, "return_on_equity_fy") or _safe(m, "roe"))
    if roe is not None:
        populated += 1
        if roe >= 0.18:   score += 6
        elif roe >= 0.12: score += 3

    gnpa = _pct(_safe(m, "gnpa") or _safe(m, "gross_npa_ratio"))
    if gnpa is not None:
        populated += 1
        if gnpa <= 0.02:   score += 7
        elif gnpa <= 0.04: score += 5
        elif gnpa <= 0.06: score += 2

    car = _pct(_safe(m, "capital_adequacy_ratio") or _safe(m, "car"))
    if car is not None:
        populated += 1
        if car >= 0.18:    score += 5
        elif car >= 0.15:  score += 3
        elif car >= 0.12:  score += 1

    de = _safe(m, "debt_equity") or _safe(m, "debt_to_equity")
    if de is not None:
        try:
            de_f = float(de)
            populated += 1
            # NBFCs operate with higher leverage — different scale
            if de_f <= 3.0:    score += 4
            elif de_f <= 5.0:  score += 2
        except (TypeError, ValueError):
            pass

    coverage = populated / fields_total
    return min(30, max(0, score)), 30, coverage


def score_nbfc(metrics: dict) -> ScoreBreakdown:
    """Universe Quality Score for NBFC_HFC sub-path."""
    bq, _, bq_cov = _bq_nbfc(metrics)
    gq, _, gq_cov = _gq_nonfin(metrics)
    vc, _, vc_cov = _vc_common(metrics)
    gv, _, gv_cov = _gov_common(metrics)

    total = min(100, bq + gq + vc + gv)

    dc = DataCoverage(
        business_quality_coverage = bq_cov,
        growth_quality_coverage   = gq_cov,
        valuation_coverage        = vc_cov,
        governance_coverage       = gv_cov,
    )
    dc.overall_data_confidence = _classify_data_confidence(dc)

    return ScoreBreakdown(
        business_quality  = bq,
        growth_quality    = gq,
        valuation_context = vc,
        governance        = gv,
        total             = total,
        quality_tier      = _quality_tier(total),
        data_coverage     = dc,
    )


# ---------------------------------------------------------------------------
# INSURANCE scorer
# ---------------------------------------------------------------------------

def _bq_insurance(m: dict) -> tuple[int, int, float]:
    """Business Quality for INSURANCE path. Max = 30."""
    score = 0
    populated = 0
    fields_total = 4  # Combined ratio, solvency margin, VNB margin, ROE

    combined_ratio = _pct(_safe(m, "combined_ratio"))
    if combined_ratio is not None:
        populated += 1
        # Combined ratio < 1.0 (100%) means underwriting profit
        if combined_ratio <= 0.90:   score += 8
        elif combined_ratio <= 0.95: score += 6
        elif combined_ratio <= 1.00: score += 3

    solvency = _pct(_safe(m, "solvency_margin") or _safe(m, "solvency_ratio"))
    if solvency is not None:
        populated += 1
        if solvency >= 2.0:    score += 8
        elif solvency >= 1.5:  score += 5
        elif solvency >= 1.3:  score += 2   # regulatory minimum ~1.5×

    vnb_margin = _pct(_safe(m, "vnb_margin") or _safe(m, "value_new_business_margin"))
    if vnb_margin is not None:
        populated += 1
        if vnb_margin >= 0.25:   score += 7
        elif vnb_margin >= 0.18: score += 4
        elif vnb_margin > 0:     score += 1

    roe = _pct(_safe(m, "return_on_equity_fy") or _safe(m, "roe"))
    if roe is not None:
        populated += 1
        if roe >= 0.20:   score += 7
        elif roe >= 0.15: score += 4
        elif roe >= 0.10: score += 1

    coverage = populated / fields_total
    return min(30, max(0, score)), 30, coverage


def score_insurance(metrics: dict) -> ScoreBreakdown:
    """Universe Quality Score for INSURANCE sub-path."""
    bq, _, bq_cov = _bq_insurance(metrics)
    gq, _, gq_cov = _gq_nonfin(metrics)
    vc, _, vc_cov = _vc_common(metrics)
    gv, _, gv_cov = _gov_common(metrics)

    total = min(100, bq + gq + vc + gv)

    dc = DataCoverage(
        business_quality_coverage = bq_cov,
        growth_quality_coverage   = gq_cov,
        valuation_coverage        = vc_cov,
        governance_coverage       = gv_cov,
    )
    dc.overall_data_confidence = _classify_data_confidence(dc)

    return ScoreBreakdown(
        business_quality  = bq,
        growth_quality    = gq,
        valuation_context = vc,
        governance        = gv,
        total             = total,
        quality_tier      = _quality_tier(total),
        data_coverage     = dc,
    )


# ---------------------------------------------------------------------------
# AMC scorer  (Investment Managers)
# AUM growth replaces capex / FCF; ROE / OPM remain relevant
# ---------------------------------------------------------------------------

def _bq_amc(m: dict) -> tuple[int, int, float]:
    """Business Quality for AMC sub-path. Max = 30."""
    score = 0
    populated = 0
    fields_total = 5  # ROE, OPM, AUM growth YoY, market share, D/E

    roe = _pct(_safe(m, "return_on_equity_fy") or _safe(m, "roe"))
    if roe is not None:
        populated += 1
        if roe >= 0.30:   score += 8
        elif roe >= 0.22: score += 6
        elif roe >= 0.15: score += 3

    opm = _pct(_safe(m, "operating_margin_ttm") or _safe(m, "opm"))
    if opm is not None:
        populated += 1
        if opm >= 0.35:   score += 7   # AMCs are asset-light; high OPM expected
        elif opm >= 0.25: score += 5
        elif opm >= 0.15: score += 2

    aum_growth = _pct(_safe(m, "aum_growth_yoy") or _safe(m, "aum_yoy"))
    if aum_growth is not None:
        populated += 1
        if aum_growth >= 0.20:   score += 7
        elif aum_growth >= 0.12: score += 4
        elif aum_growth >= 0.05: score += 2

    # Market share stability (optional; many AMCs won't have this)
    mkt_share = _safe(m, "market_share_aum")
    if mkt_share is not None:
        populated += 1
        try:
            ms = float(mkt_share)
            if ms >= 0.10:    score += 4   # top-3 AMC
            elif ms >= 0.05:  score += 2
        except (TypeError, ValueError):
            pass

    de = _safe(m, "debt_equity") or _safe(m, "debt_to_equity")
    if de is not None:
        try:
            de_f = float(de)
            populated += 1
            if de_f <= 0.1:   score += 4   # AMCs should be near-zero debt
            elif de_f <= 0.5: score += 2
        except (TypeError, ValueError):
            pass

    coverage = populated / fields_total
    return min(30, max(0, score)), 30, coverage


def score_amc(metrics: dict) -> ScoreBreakdown:
    """Universe Quality Score for AMC sub-path (Investment Managers)."""
    bq, _, bq_cov = _bq_amc(metrics)
    gq, _, gq_cov = _gq_nonfin(metrics)
    vc, _, vc_cov = _vc_common(metrics)
    gv, _, gv_cov = _gov_common(metrics)

    total = min(100, bq + gq + vc + gv)

    dc = DataCoverage(
        business_quality_coverage = bq_cov,
        growth_quality_coverage   = gq_cov,
        valuation_coverage        = vc_cov,
        governance_coverage       = gv_cov,
    )
    dc.overall_data_confidence = _classify_data_confidence(dc)

    return ScoreBreakdown(
        business_quality  = bq,
        growth_quality    = gq,
        valuation_context = vc,
        governance        = gv,
        total             = total,
        quality_tier      = _quality_tier(total),
        data_coverage     = dc,
    )
