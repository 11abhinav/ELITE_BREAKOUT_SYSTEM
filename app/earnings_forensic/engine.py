"""
app/earnings_forensic/engine.py
Deterministic Forensic Engine — all calculations run before any AI call.

Rules:
  - Each check returns a ForensicFinding with explicit evidence_level
  - EVIDENCE_INSUFFICIENT is returned (not skipped) when data is missing
  - Financial sector stocks get sector-exempt treatment for OCF checks
  - Severity thresholds are configurable constants at the top of this file
  - Never manufacture an adjusted PAT — if evidence is insufficient, say so
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

from .schema import (
    CashConversionQuality,
    DataScope,
    EarningsGrade,
    EvidenceLevel,
    FindingClassification,
    FindingSeverity,
    ForensicFactSheet,
    ForensicFinding,
    ForensicReport,
    ProfitQuality,
    RevenueQuality,
    FORENSIC_ENGINE_VERSION,
)

logger = logging.getLogger(__name__)

# ─── Thresholds ────────────────────────────────────────────────────────────────
# Revenue growth: if revenue grows > this but OP grows less, flag divergence
REV_OP_DIVERGENCE_THRESHOLD = 10.0      # OP growth trails revenue growth by 10pp+
# Margin compression: if OPM drops by this many bps YoY, flag it
MARGIN_COMPRESSION_BPS_MEDIUM = 150     # ~1.5pp drop
MARGIN_COMPRESSION_BPS_HIGH   = 300     # ~3pp drop
# Profit quality: if net_margin rose materially despite OPM compression
NPM_OPM_DIVERGENCE_BPS = 200           # NPM up 200bps while OPM flat/down = suspicious

# Cash conversion thresholds
CFO_PAT_HEALTHY  = 0.80
CFO_PAT_ADEQUATE = 0.60
CFO_PAT_WEAK     = 0.40

# EPS vs revenue: revenue up but EPS down → dilution or one-off cost
EPS_REV_DIVERGENCE_THRESHOLD = 15.0    # revenue +15% but EPS flat/negative

# Multi-quarter trend: how many consecutive quarters needed to call a trend
TREND_MIN_QUARTERS = 3


def _safe_pct(val: Optional[float]) -> str:
    return f"{val:+.1f}%" if val is not None else "N/A"

def _safe_bps(val: Optional[float]) -> str:
    return f"{val:+.0f}bps" if val is not None else "N/A"

def _safe_cr(val: Optional[float]) -> str:
    return f"₹{val:,.1f} Cr" if val is not None else "N/A"


# ─── Individual checks ─────────────────────────────────────────────────────────

def check_revenue_growth_quality(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 1: Revenue growth vs operating profit growth divergence."""
    findings: List[ForensicFinding] = []
    g = fs.growth
    if g is None:
        return findings

    # --- Check 1a: Revenue YoY present? ----
    if g.revenue_yoy_pct is None:
        findings.append(ForensicFinding(
            code="REVENUE_YOY_UNAVAILABLE",
            title="Revenue YoY comparison unavailable",
            severity=FindingSeverity.INFO,
            evidence_level=EvidenceLevel.EVIDENCE_INSUFFICIENT,
            classification=FindingClassification.EVIDENCE_GAP,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation="Prior-year same-quarter data is missing. Revenue trend cannot be assessed.",
            source_refs=["PIT_QUARTERLY"],
            limitations=["Need Q-4 (same quarter prior year) in PIT series"],
        ))
        return findings

    rev_yoy = g.revenue_yoy_pct
    op_yoy  = g.op_profit_yoy_pct

    # --- Check 1b: Revenue grew but operating profit lagged ----
    if rev_yoy is not None and op_yoy is not None and rev_yoy > 5.0:
        divergence = rev_yoy - op_yoy
        if divergence >= REV_OP_DIVERGENCE_THRESHOLD:
            sev = FindingSeverity.HIGH if divergence >= 20 else FindingSeverity.MEDIUM
            findings.append(ForensicFinding(
                code="REVENUE_OP_DIVERGENCE",
                title="Revenue grew but operating profit lagged",
                severity=sev,
                evidence_level=EvidenceLevel.STRONG,
                classification=FindingClassification.CALCULATED_FACT,
                data_scope=DataScope.QUARTERLY_YOY,
                explanation=(
                    f"Revenue rose {_safe_pct(rev_yoy)} YoY but operating profit grew only "
                    f"{_safe_pct(op_yoy)} YoY — a {divergence:.1f}pp divergence. "
                    "This suggests costs rose faster than revenue. Investigate raw material, "
                    "employee costs, or fixed-cost absorption."
                ),
                source_refs=["PIT_QUARTERLY"],
                calculated_values={
                    "revenue_yoy_pct": rev_yoy,
                    "op_profit_yoy_pct": op_yoy,
                    "divergence_pp": round(divergence, 1),
                },
                limitations=["Exact cost category breakdown not available in PIT data"],
            ))

    # --- Check 1c: Revenue declined ----
    if rev_yoy is not None and rev_yoy < -5.0:
        findings.append(ForensicFinding(
            code="REVENUE_DECLINE_YOY",
            title="Revenue declined year-on-year",
            severity=FindingSeverity.HIGH,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=f"Revenue fell {_safe_pct(rev_yoy)} vs same quarter prior year.",
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"revenue_yoy_pct": rev_yoy},
        ))

    # --- Check 1d: Strong growth signal ----
    if rev_yoy is not None and op_yoy is not None and rev_yoy >= 15 and op_yoy >= rev_yoy:
        findings.append(ForensicFinding(
            code="REVENUE_GROWTH_SUPPORTED",
            title="Revenue growth supported by operating profit",
            severity=FindingSeverity.INFO,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"Revenue grew {_safe_pct(rev_yoy)} and operating profit grew {_safe_pct(op_yoy)} YoY. "
                "Operational leverage appears positive."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"revenue_yoy_pct": rev_yoy, "op_profit_yoy_pct": op_yoy},
        ))

    return findings


def check_margin_quality(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 2: Operating margin and net margin trends."""
    findings: List[ForensicFinding] = []
    g = fs.growth
    curr = fs.current
    if g is None or curr is None:
        return findings

    opm_delta = g.opm_yoy_delta_bps
    npm_delta  = g.npm_yoy_delta_bps

    if opm_delta is None:
        findings.append(ForensicFinding(
            code="MARGIN_COMPARISON_UNAVAILABLE",
            title="Margin YoY comparison unavailable",
            severity=FindingSeverity.INFO,
            evidence_level=EvidenceLevel.EVIDENCE_INSUFFICIENT,
            classification=FindingClassification.EVIDENCE_GAP,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation="Prior-year same-quarter margin data is missing.",
            source_refs=["PIT_QUARTERLY"],
        ))
        return findings

    # OPM compression
    if opm_delta <= -MARGIN_COMPRESSION_BPS_HIGH:
        findings.append(ForensicFinding(
            code="MARGIN_COMPRESSION_SEVERE_YOY",
            title="Severe operating margin compression YoY",
            severity=FindingSeverity.HIGH,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"Operating margin contracted {_safe_bps(opm_delta)} vs same quarter prior year. "
                f"Current OPM: {curr.operating_margin:.1f}%. "
                "Investigate input cost inflation, pricing power erosion, or fixed-cost deleverage."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"opm_yoy_delta_bps": opm_delta, "opm_current": curr.operating_margin},
        ))
    elif opm_delta <= -MARGIN_COMPRESSION_BPS_MEDIUM:
        findings.append(ForensicFinding(
            code="MARGIN_COMPRESSION_YOY",
            title="Operating margin compressed YoY",
            severity=FindingSeverity.MEDIUM,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"Operating margin compressed {_safe_bps(opm_delta)} vs same quarter prior year. "
                f"Current OPM: {curr.operating_margin:.1f}%."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"opm_yoy_delta_bps": opm_delta, "opm_current": curr.operating_margin},
        ))
    elif opm_delta >= MARGIN_COMPRESSION_BPS_MEDIUM:
        findings.append(ForensicFinding(
            code="MARGIN_EXPANSION_YOY",
            title="Operating margin expanded YoY",
            severity=FindingSeverity.INFO,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=f"Operating margin expanded {_safe_bps(opm_delta)} vs same quarter prior year.",
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"opm_yoy_delta_bps": opm_delta, "opm_current": curr.operating_margin},
        ))

    # NPM vs OPM divergence: net margin rose while OPM fell → non-operating income or tax benefit
    if (opm_delta is not None and npm_delta is not None
            and opm_delta < 0 and npm_delta > NPM_OPM_DIVERGENCE_BPS):
        findings.append(ForensicFinding(
            code="NPM_OPM_DIVERGENCE",
            title="Net margin rose while operating margin fell",
            severity=FindingSeverity.HIGH,
            evidence_level=EvidenceLevel.MODERATE,
            classification=FindingClassification.CALCULATED_PROXY,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"Operating margin compressed {_safe_bps(opm_delta)} but net margin expanded "
                f"{_safe_bps(npm_delta)} YoY. This divergence typically indicates non-operating income "
                "(investment gains, asset sales, or tax credits) contributing to PAT rather than "
                "core business improvement. Requires reconciliation with filing notes."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"opm_yoy_delta_bps": opm_delta, "npm_yoy_delta_bps": npm_delta},
            limitations=["Exceptional items / other income not available in PIT data — verify in filing"],
        ))

    return findings


def check_profit_quality(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 3: PAT vs operating profit relationship."""
    findings: List[ForensicFinding] = []
    g = fs.growth
    curr = fs.current
    if g is None or curr is None:
        return findings

    net_yoy = g.net_profit_yoy_pct
    op_yoy  = g.op_profit_yoy_pct

    # Revenue up, PAT down — earnings under pressure
    if g.revenue_yoy_pct is not None and net_yoy is not None:
        if g.revenue_yoy_pct > 5.0 and net_yoy < 0:
            findings.append(ForensicFinding(
                code="PAT_DECLINE_DESPITE_REVENUE_GROWTH",
                title="PAT declined despite revenue growth",
                severity=FindingSeverity.HIGH,
                evidence_level=EvidenceLevel.STRONG,
                classification=FindingClassification.CALCULATED_FACT,
                data_scope=DataScope.QUARTERLY_YOY,
                explanation=(
                    f"Revenue grew {_safe_pct(g.revenue_yoy_pct)} but PAT fell {_safe_pct(net_yoy)} YoY. "
                    "This may indicate margin compression, higher finance costs, exceptional losses, "
                    "or increased tax expense. Requires investigation of the P&L notes."
                ),
                source_refs=["PIT_QUARTERLY"],
                calculated_values={
                    "revenue_yoy_pct": g.revenue_yoy_pct,
                    "net_profit_yoy_pct": net_yoy,
                },
                limitations=["Exceptional items, tax split not available in PIT — check filing notes"],
            ))

    # OP up, PAT down much more → finance cost or tax surge
    if op_yoy is not None and net_yoy is not None:
        divergence = op_yoy - net_yoy
        if op_yoy > 0 and net_yoy < 0 and divergence > 15:
            findings.append(ForensicFinding(
                code="OPPROFIT_PAT_DIVERGENCE",
                title="Operating profit grew but PAT declined",
                severity=FindingSeverity.HIGH,
                evidence_level=EvidenceLevel.MODERATE,
                classification=FindingClassification.CALCULATED_PROXY,
                data_scope=DataScope.QUARTERLY_YOY,
                explanation=(
                    f"Operating profit grew {_safe_pct(op_yoy)} but PAT fell {_safe_pct(net_yoy)}. "
                    "The likely cause is increased finance costs, exceptional charges, deferred tax "
                    "movement, or prior-period adjustments below the EBIT line. Cannot confirm without "
                    "the full P&L breakdown."
                ),
                source_refs=["PIT_QUARTERLY"],
                calculated_values={"op_profit_yoy_pct": op_yoy, "net_profit_yoy_pct": net_yoy},
                limitations=["Full P&L breakdown below EBIT not available in PIT data"],
            ))

    return findings


def check_cash_conversion(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 4: CFO/PAT quality over time."""
    findings: List[ForensicFinding] = []
    cf = fs.cash_flow

    if cf is None or not cf.ocf_data_available:
        if not fs.is_financial_sector:
            findings.append(ForensicFinding(
                code="CFO_PAT_EVIDENCE_INSUFFICIENT",
                title="Cash flow data unavailable for quality check",
                severity=FindingSeverity.INFO,
                evidence_level=EvidenceLevel.EVIDENCE_INSUFFICIENT,
                classification=FindingClassification.EVIDENCE_GAP,
                data_scope=DataScope.ANNUAL_ONLY,
                explanation=(
                    "Operating cash flow data is not available in the quarterly PIT records. "
                    "Cash conversion quality cannot be assessed for this period. "
                    "Annual filing data may provide this metric."
                ),
                source_refs=["PIT_ANNUAL"],
                limitations=["Operating cash flow not populated in SCREENER quarterly records"],
            ))
        return findings

    ratio = cf.cfo_pat_current_year
    avg3  = cf.cfo_pat_3y_avg

    if ratio is not None:
        if ratio < CFO_PAT_WEAK:
            sev = FindingSeverity.HIGH
            label = "poor"
        elif ratio < CFO_PAT_ADEQUATE:
            sev = FindingSeverity.MEDIUM
            label = "weak"
        elif ratio < CFO_PAT_HEALTHY:
            sev = FindingSeverity.LOW
            label = "adequate"
        else:
            sev = FindingSeverity.INFO
            label = "healthy"

        avg3_str = f" (3Y avg: {avg3:.2f})" if avg3 else ""
        findings.append(ForensicFinding(
            code=f"CFO_PAT_{label.upper()}",
            title=f"Cash conversion is {label}",
            severity=sev,
            evidence_level=EvidenceLevel.STRONG if avg3 else EvidenceLevel.MODERATE,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.ANNUAL_ONLY,
            explanation=(
                f"CFO/PAT ratio is {ratio:.2f}{avg3_str}. "
                "A ratio below 0.60 means only a fraction of reported profit is converting into "
                "operating cash — a sustained concern for earnings quality."
            ),
            source_refs=["PIT_ANNUAL"],
            calculated_values={"cfo_pat_current": ratio, "cfo_pat_3y_avg": avg3},
            limitations=["Based on annual filing data only; quarterly OCF not available"],
        ))

        # Deterioration vs prior
        if avg3 and ratio < avg3 - 0.20:
            findings.append(ForensicFinding(
                code="CFO_PAT_DETERIORATING",
                title="Cash conversion deteriorating vs 3-year average",
                severity=FindingSeverity.MEDIUM,
                evidence_level=EvidenceLevel.STRONG,
                classification=FindingClassification.CALCULATED_FACT,
                data_scope=DataScope.MULTI_YEAR,
                explanation=(
                    f"Current CFO/PAT ({ratio:.2f}) is materially below the 3-year average ({avg3:.2f}). "
                    "This may indicate expanding working capital requirements, delayed collections, "
                    "or cash-flow timing differences."
                ),
                source_refs=["PIT_ANNUAL"],
                calculated_values={"cfo_pat_current": ratio, "cfo_pat_3y_avg": avg3, "decline": round(avg3 - ratio, 3)},
            ))

    return findings


def check_eps_quality(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 5: EPS vs revenue growth — detects dilution or cost surge."""
    findings: List[ForensicFinding] = []
    g = fs.growth
    if g is None:
        return findings

    if g.eps_yoy_pct is None:
        return findings

    rev_yoy = g.revenue_yoy_pct
    eps_yoy = g.eps_yoy_pct

    if (rev_yoy is not None and rev_yoy >= EPS_REV_DIVERGENCE_THRESHOLD
            and eps_yoy is not None and eps_yoy < 0):
        findings.append(ForensicFinding(
            code="EPS_DECLINE_VS_REVENUE_GROWTH",
            title="EPS declined despite revenue growth",
            severity=FindingSeverity.HIGH,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"Revenue grew {_safe_pct(rev_yoy)} but EPS fell {_safe_pct(eps_yoy)} YoY. "
                "Possible causes: share dilution, exceptional charges below EBIT, or higher finance costs."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"revenue_yoy_pct": rev_yoy, "eps_yoy_pct": eps_yoy},
        ))

    return findings


def check_margin_trend(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 6: Multi-quarter margin trend."""
    findings: List[ForensicFinding] = []
    q_series = fs.quarterly_series

    if len(q_series) < TREND_MIN_QUARTERS:
        return findings

    recent = q_series[:TREND_MIN_QUARTERS]
    opm_vals = [r.operating_margin for r in recent if r.operating_margin is not None]

    if len(opm_vals) >= TREND_MIN_QUARTERS:
        # Check monotonic decline
        if all(opm_vals[i] > opm_vals[i + 1] for i in range(len(opm_vals) - 1)):
            findings.append(ForensicFinding(
                code="OPM_CONSECUTIVE_DECLINE",
                title=f"Operating margin declined for {len(opm_vals)} consecutive quarters",
                severity=FindingSeverity.HIGH,
                evidence_level=EvidenceLevel.STRONG,
                classification=FindingClassification.CALCULATED_FACT,
                data_scope=DataScope.MULTI_YEAR,
                explanation=(
                    f"OPM has declined in each of the last {len(opm_vals)} quarters: "
                    f"{' → '.join(f'{v:.1f}%' for v in reversed(opm_vals))}."
                ),
                source_refs=["PIT_QUARTERLY"],
                calculated_values={"opm_trend": opm_vals},
            ))
        elif all(opm_vals[i] < opm_vals[i + 1] for i in range(len(opm_vals) - 1)):
            findings.append(ForensicFinding(
                code="OPM_CONSECUTIVE_EXPANSION",
                title=f"Operating margin expanded for {len(opm_vals)} consecutive quarters",
                severity=FindingSeverity.INFO,
                evidence_level=EvidenceLevel.STRONG,
                classification=FindingClassification.CALCULATED_FACT,
                data_scope=DataScope.MULTI_YEAR,
                explanation=(
                    f"OPM has expanded in each of the last {len(opm_vals)} quarters: "
                    f"{' → '.join(f'{v:.1f}%' for v in reversed(opm_vals))}."
                ),
                source_refs=["PIT_QUARTERLY"],
                calculated_values={"opm_trend": opm_vals},
            ))

    return findings


def check_financial_sector_quality(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 7: For financial sector stocks — ROA and ROCE trend."""
    findings: List[ForensicFinding] = []
    if not fs.is_financial_sector:
        return findings

    curr = fs.current
    if curr is None:
        return findings

    # ROA proxy: net_margin / 10 is a rough proxy for banks (asset-heavy)
    # Only flag if ROE is available and deteriorating
    if curr.roe is not None and curr.roe < 5.0:
        findings.append(ForensicFinding(
            code="FINANCIAL_LOW_ROE",
            title="ROE below minimum viable level for financial institution",
            severity=FindingSeverity.HIGH,
            evidence_level=EvidenceLevel.MODERATE,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.QUARTERLY_YOY,
            explanation=(
                f"ROE is {curr.roe:.1f}%, below the 5% floor typically required for financial "
                "institutions to cover cost of equity."
            ),
            source_refs=["PIT_QUARTERLY"],
            calculated_values={"roe": curr.roe},
        ))

    return findings


def check_leverage_trend(fs: ForensicFactSheet) -> List[ForensicFinding]:
    """Check 8: Debt trajectory from annual series."""
    findings: List[ForensicFinding] = []
    if fs.is_financial_sector:
        return findings  # D/E norms differ for financials

    ann = fs.annual_series
    if len(ann) < 2:
        return findings

    d0 = ann[0].total_debt
    d1 = ann[1].total_debt
    e0 = ann[0].total_equity

    if None in (d0, d1, e0) or e0 == 0:
        return findings

    de_ratio = d0 / e0
    debt_change_pct = (d0 - d1) / abs(d1) * 100 if d1 != 0 else None

    if de_ratio > 2.0:
        sev = FindingSeverity.HIGH if de_ratio > 3.0 else FindingSeverity.MEDIUM
        findings.append(ForensicFinding(
            code="HIGH_LEVERAGE",
            title="High debt-to-equity ratio",
            severity=sev,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.ANNUAL_ONLY,
            explanation=f"D/E ratio is {de_ratio:.1f}x based on latest annual filing.",
            source_refs=["PIT_ANNUAL"],
            calculated_values={"de_ratio": round(de_ratio, 2), "total_debt_cr": d0},
        ))

    if debt_change_pct is not None and debt_change_pct > 25:
        findings.append(ForensicFinding(
            code="DEBT_RAPID_INCREASE",
            title="Total debt increased rapidly YoY",
            severity=FindingSeverity.MEDIUM,
            evidence_level=EvidenceLevel.STRONG,
            classification=FindingClassification.CALCULATED_FACT,
            data_scope=DataScope.ANNUAL_ONLY,
            explanation=f"Total debt grew {debt_change_pct:.1f}% vs prior year.",
            source_refs=["PIT_ANNUAL"],
            calculated_values={"debt_yoy_pct": round(debt_change_pct, 1), "total_debt_cr": d0},
        ))

    return findings


# ─── Grade assignment ──────────────────────────────────────────────────────────

def _assign_revenue_quality(fs: ForensicFactSheet, findings: List[ForensicFinding]) -> RevenueQuality:
    g = fs.growth
    if g is None or g.revenue_yoy_pct is None:
        return RevenueQuality.UNVERIFIED
    if g.revenue_yoy_pct < -5:
        return RevenueQuality.NEGATIVE
    codes = {f.code for f in findings}
    if "REVENUE_OP_DIVERGENCE" in codes:
        return RevenueQuality.MIXED
    if "REVENUE_GROWTH_SUPPORTED" in codes:
        return RevenueQuality.SUPPORTED
    if g.revenue_yoy_pct > 5:
        return RevenueQuality.MIXED  # growth present but not independently verified
    return RevenueQuality.UNVERIFIED


def _assign_profit_quality(fs: ForensicFactSheet, findings: List[ForensicFinding]) -> ProfitQuality:
    codes = {f.code for f in findings}
    g = fs.growth
    if g is None:
        return ProfitQuality.UNVERIFIED
    if g.net_profit_yoy_pct is None:
        return ProfitQuality.UNVERIFIED
    if g.net_profit_yoy_pct < 0:
        return ProfitQuality.DECLINING
    if "NPM_OPM_DIVERGENCE" in codes or "OPPROFIT_PAT_DIVERGENCE" in codes:
        return ProfitQuality.NON_RECURRING
    if "PAT_DECLINE_DESPITE_REVENUE_GROWTH" in codes:
        return ProfitQuality.DECLINING
    if g.net_profit_yoy_pct > 0 and "MARGIN_COMPRESSION_YOY" not in codes:
        return ProfitQuality.OPERATIONAL
    return ProfitQuality.MIXED


def _assign_cash_quality(fs: ForensicFactSheet) -> CashConversionQuality:
    cf = fs.cash_flow
    if cf is None or not cf.ocf_data_available:
        return CashConversionQuality.INSUFFICIENT
    r = cf.cfo_pat_current_year
    if r is None:
        return CashConversionQuality.INSUFFICIENT
    if r >= CFO_PAT_HEALTHY:
        return CashConversionQuality.HEALTHY
    if r >= CFO_PAT_ADEQUATE:
        return CashConversionQuality.ADEQUATE
    if r >= CFO_PAT_WEAK:
        return CashConversionQuality.WEAK
    return CashConversionQuality.POOR


def _assign_grade(
    rev_q: RevenueQuality, prof_q: ProfitQuality, cash_q: CashConversionQuality,
    findings: List[ForensicFinding]
) -> EarningsGrade:
    """Simple rules-based grade assignment."""
    high_count = sum(1 for f in findings if f.severity == FindingSeverity.HIGH
                     and f.evidence_level not in (EvidenceLevel.EVIDENCE_INSUFFICIENT,))
    if rev_q == RevenueQuality.NEGATIVE or prof_q == ProfitQuality.DECLINING:
        return EarningsGrade.D if high_count >= 2 else EarningsGrade.C
    if rev_q == RevenueQuality.SUPPORTED and prof_q == ProfitQuality.OPERATIONAL:
        if cash_q in (CashConversionQuality.HEALTHY, CashConversionQuality.ADEQUATE):
            return EarningsGrade.A
        return EarningsGrade.B
    if rev_q == RevenueQuality.MIXED or prof_q in (ProfitQuality.MIXED, ProfitQuality.NON_RECURRING):
        return EarningsGrade.C
    if rev_q == RevenueQuality.UNVERIFIED:
        return EarningsGrade.U
    return EarningsGrade.B


def _evidence_coverage(fs: ForensicFactSheet, findings: List[ForensicFinding]) -> float:
    """Fraction of checks that had sufficient evidence."""
    total = len(findings)
    if total == 0:
        return 0.0
    sufficient = sum(1 for f in findings if f.evidence_level != EvidenceLevel.EVIDENCE_INSUFFICIENT)
    return round(sufficient / total * 100, 1)


def _build_headline(
    rev_q: RevenueQuality, prof_q: ProfitQuality, cash_q: CashConversionQuality, grade: EarningsGrade
) -> str:
    parts = []
    if rev_q == RevenueQuality.SUPPORTED:
        parts.append("Revenue growth supported")
    elif rev_q == RevenueQuality.MIXED:
        parts.append("Revenue grew, quality mixed")
    elif rev_q == RevenueQuality.NEGATIVE:
        parts.append("Revenue declined")
    else:
        parts.append("Revenue unverified")

    if prof_q == ProfitQuality.OPERATIONAL:
        parts.append("profit operationally driven")
    elif prof_q == ProfitQuality.NON_RECURRING:
        parts.append("profit boost appears non-recurring")
    elif prof_q == ProfitQuality.DECLINING:
        parts.append("profit under pressure")
    elif prof_q == ProfitQuality.MIXED:
        parts.append("profit quality mixed")

    if cash_q == CashConversionQuality.POOR or cash_q == CashConversionQuality.WEAK:
        parts.append("cash conversion weak")
    elif cash_q == CashConversionQuality.HEALTHY:
        parts.append("cash conversion healthy")

    return "; ".join(parts).capitalize() + "." if parts else f"Earnings grade {grade.value} — review required."


# ─── Main entry point ──────────────────────────────────────────────────────────

def run_all_checks(fs: ForensicFactSheet) -> ForensicReport:
    """
    Run all deterministic forensic checks and assemble a ForensicReport.
    AI analysis is NOT called here — this is purely deterministic.
    """
    report = ForensicReport(
        symbol        = fs.symbol,
        period_end    = fs.period_end,
        statement_type= fs.statement_type,
        reporting_basis= fs.basis,
        filing_id     = fs.filing_id,
        source_hash   = fs.source_hash,
        engine_version= FORENSIC_ENGINE_VERSION,
    )

    all_findings: List[ForensicFinding] = []

    all_findings += check_revenue_growth_quality(fs)
    all_findings += check_margin_quality(fs)
    all_findings += check_profit_quality(fs)
    all_findings += check_cash_conversion(fs)
    all_findings += check_eps_quality(fs)
    all_findings += check_margin_trend(fs)
    all_findings += check_leverage_trend(fs)
    if fs.is_financial_sector:
        all_findings += check_financial_sector_quality(fs)

    report.findings = all_findings

    # Assign quality dimensions
    report.revenue_quality   = _assign_revenue_quality(fs, all_findings)
    report.profit_quality    = _assign_profit_quality(fs, all_findings)
    report.cash_conversion   = _assign_cash_quality(fs)
    report.earnings_grade    = _assign_grade(
        report.revenue_quality, report.profit_quality, report.cash_conversion, all_findings
    )
    report.headline_verdict  = _build_headline(
        report.revenue_quality, report.profit_quality, report.cash_conversion, report.earnings_grade
    )

    # Populate key display metrics
    g = fs.growth
    if g:
        report.revenue_yoy_pct    = g.revenue_yoy_pct
        report.revenue_qoq_pct    = g.revenue_qoq_pct
        report.op_profit_yoy_pct  = g.op_profit_yoy_pct
        report.net_profit_yoy_pct = g.net_profit_yoy_pct
        report.opm_yoy_delta_bps  = g.opm_yoy_delta_bps
        report.ttm_revenue        = g.ttm_revenue
        report.ttm_net_profit     = g.ttm_net_profit

    if fs.current:
        report.opm_current = fs.current.operating_margin
        report.npm_current = fs.current.net_margin

    if fs.cash_flow:
        report.cfo_pat_ratio   = fs.cash_flow.cfo_pat_current_year
        report.cfo_pat_3y_avg  = fs.cash_flow.cfo_pat_3y_avg

    report.evidence_coverage_pct = _evidence_coverage(fs, all_findings)
    report.evidence_missing      = fs.evidence_missing_fields

    return report
