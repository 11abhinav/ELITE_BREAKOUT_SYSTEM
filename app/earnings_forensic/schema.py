"""
app/earnings_forensic/schema.py
Data model for the Earnings Forensic & Hidden Facts Scanner.

Design rules:
  - All monetary values in INR Crore (consistent with PIT raw filings)
  - Every finding carries: code, severity, evidence_level, data_scope, source_ref
  - EvidenceLevel distinguishes data quality from finding severity
  - EVIDENCE_INSUFFICIENT is always explicit — never hidden or zeroed
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ─── Engine version ──────────────────────────────────────────────────────────
FORENSIC_ENGINE_VERSION = "1.0.0"


# ─── Enums ───────────────────────────────────────────────────────────────────

class FindingSeverity(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"
    INFO   = "INFO"


class EvidenceLevel(str, Enum):
    """Quality of evidence supporting a finding."""
    STRONG               = "STRONG"       # Directly calculated from ≥2 periods of consistent data
    MODERATE             = "MODERATE"     # Single period or indirect proxy
    WEAK                 = "WEAK"         # Inferred; limited supporting data
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"  # Required data missing from PIT


class DataScope(str, Enum):
    """Time-window over which the check was computed."""
    QUARTERLY_YOY  = "QUARTERLY_YOY"    # Q vs same Q prior year
    QUARTERLY_QOQ  = "QUARTERLY_QOQ"    # Q vs immediately prior Q
    ANNUAL_ONLY    = "ANNUAL_ONLY"      # Full-year / annual filing data
    TTM            = "TTM"              # Trailing 12 months (last 4 quarters)
    MULTI_YEAR     = "MULTI_YEAR"       # 2+ year trend
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RevenueQuality(str, Enum):
    SUPPORTED   = "SUPPORTED"    # Revenue growth consistent with available evidence
    MIXED       = "MIXED"        # Revenue grows but other indicators deteriorate
    UNVERIFIED  = "UNVERIFIED"   # Insufficient data to classify
    NEGATIVE    = "NEGATIVE"     # Revenue declined


class ProfitQuality(str, Enum):
    OPERATIONAL   = "OPERATIONAL"   # Profit growth driven by core operations
    NON_RECURRING = "NON_RECURRING" # Significant non-recurring boost suspected
    MIXED         = "MIXED"
    DECLINING     = "DECLINING"
    UNVERIFIED    = "UNVERIFIED"


class CashConversionQuality(str, Enum):
    HEALTHY       = "HEALTHY"     # CFO/PAT ≥ 0.80 sustained
    ADEQUATE      = "ADEQUATE"    # CFO/PAT 0.60–0.80
    WEAK          = "WEAK"        # CFO/PAT 0.40–0.60
    POOR          = "POOR"        # CFO/PAT < 0.40
    INSUFFICIENT  = "INSUFFICIENT"  # OCF data not available


class FindingClassification(str, Enum):
    CALCULATED_FACT  = "CALCULATED_FACT"   # Directly computed from PIT data
    CALCULATED_PROXY = "CALCULATED_PROXY"  # Proxy metric (clearly marked as estimate)
    AI_INFERENCE     = "AI_INFERENCE"      # AI-generated hypothesis (not a proven fact)
    EVIDENCE_GAP     = "EVIDENCE_GAP"      # Data required for check is absent


class ReportStatus(str, Enum):
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE   = "COMPLETE"
    FAILED     = "FAILED"
    STALE      = "STALE"       # Underlying filing was revised; report superseded


class EarningsGrade(str, Enum):
    A = "A"  # Strong operational growth + good cash conversion
    B = "B"  # Positive growth, minor concerns
    C = "C"  # Mixed — revenue up but quality issues
    D = "D"  # Declining or materially non-recurring
    U = "U"  # Ungraded — insufficient evidence


# ─── Core dataclasses ─────────────────────────────────────────────────────────

@dataclass
class PeriodRecord:
    """A single period's normalized financial data from pit_raw_filings."""
    period_end_date: str                      # YYYY-MM-DD
    statement_type: str                       # QUARTERLY | ANNUAL
    basis: str                                # CONSOLIDATED | STANDALONE
    source_provider: str                      # SCREENER | NSE_XBRL | ...

    revenue: Optional[float]                  = None   # INR Cr
    operating_profit: Optional[float]         = None   # INR Cr (EBIT proxy)
    net_profit: Optional[float]               = None   # INR Cr (PAT)
    eps: Optional[float]                      = None
    shares_outstanding: Optional[float]       = None
    operating_cash_flow: Optional[float]      = None   # INR Cr (mostly annual only)
    free_cash_flow: Optional[float]           = None   # INR Cr
    total_debt: Optional[float]               = None   # INR Cr
    total_equity: Optional[float]             = None   # INR Cr
    cash_and_equivalents: Optional[float]     = None   # INR Cr
    depreciation_amortization: Optional[float] = None  # INR Cr
    roce: Optional[float]                     = None   # %
    roe: Optional[float]                      = None   # %
    operating_margin: Optional[float]         = None   # %
    net_margin: Optional[float]               = None   # %

    filing_id: Optional[str]                  = None
    filing_date: Optional[str]                = None


@dataclass
class GrowthMetrics:
    """Computed growth metrics between two periods."""
    revenue_yoy_pct: Optional[float]         = None
    revenue_qoq_pct: Optional[float]         = None
    op_profit_yoy_pct: Optional[float]       = None
    op_profit_qoq_pct: Optional[float]       = None
    net_profit_yoy_pct: Optional[float]      = None
    net_profit_qoq_pct: Optional[float]      = None
    eps_yoy_pct: Optional[float]             = None
    opm_yoy_delta_bps: Optional[float]       = None   # basis points
    npm_yoy_delta_bps: Optional[float]       = None   # basis points
    opm_qoq_delta_bps: Optional[float]       = None
    ttm_revenue: Optional[float]             = None   # INR Cr
    ttm_op_profit: Optional[float]           = None
    ttm_net_profit: Optional[float]          = None


@dataclass
class CashFlowMetrics:
    """Cash flow quality computed from available data."""
    cfo_pat_current_year: Optional[float]    = None   # OCF / PAT for latest annual
    cfo_pat_3y_avg: Optional[float]          = None   # 3-year average CFO/PAT
    cfo_pat_5y_avg: Optional[float]          = None   # 5-year average CFO/PAT (if available)
    fcf_current_year: Optional[float]        = None   # INR Cr
    ocf_data_available: bool                 = False
    data_scope: DataScope                    = DataScope.NOT_APPLICABLE


@dataclass
class ForensicFinding:
    """A single finding from the deterministic or AI analysis."""
    code: str                                # e.g. "MARGIN_COMPRESSION_YOY"
    title: str
    severity: FindingSeverity
    evidence_level: EvidenceLevel
    classification: FindingClassification
    data_scope: DataScope
    explanation: str                         # Investor-facing plain English
    source_refs: List[str]                   = field(default_factory=list)  # e.g. ["PIT_QUARTERLY", "NOTE_12"]
    calculated_values: Dict[str, Any]        = field(default_factory=dict)
    limitations: List[str]                   = field(default_factory=list)
    is_new_vs_prior_report: Optional[bool]   = None    # True=new, False=repeated, None=first report


@dataclass
class ForensicFactSheet:
    """Complete normalized fact sheet built from PIT series, passed to the engine and AI."""
    symbol: str
    period_end: str                          # YYYY-MM-DD (the period being analyzed)
    statement_type: str
    basis: str
    filing_id: Optional[str]
    source_hash: Optional[str]              # SHA256 of the raw exchange payload

    current: Optional[PeriodRecord]         = None
    prior_quarter: Optional[PeriodRecord]   = None    # Q-1
    same_quarter_py: Optional[PeriodRecord] = None    # Q-4 (same Q prior year)
    annual_series: List[PeriodRecord]       = field(default_factory=list)  # annual records, sorted desc
    quarterly_series: List[PeriodRecord]    = field(default_factory=list)  # all Qs, sorted desc

    growth: Optional[GrowthMetrics]         = None
    cash_flow: Optional[CashFlowMetrics]    = None

    is_financial_sector: bool               = False
    evidence_available_fields: List[str]    = field(default_factory=list)
    evidence_missing_fields: List[str]      = field(default_factory=list)


@dataclass
class ForensicReport:
    """The complete output for one symbol / period — stored as JSONB in DB."""
    id: Optional[int]                       = None
    symbol: str                             = ""
    period_end: str                         = ""
    statement_type: str                     = "QUARTERLY"
    reporting_basis: str                    = "CONSOLIDATED"
    filing_id: Optional[str]               = None
    report_version: int                     = 1
    source_hash: Optional[str]             = None
    engine_version: str                     = FORENSIC_ENGINE_VERSION

    # One-line verdict
    headline_verdict: str                   = ""
    earnings_grade: EarningsGrade          = EarningsGrade.U

    # Quality dimensions
    revenue_quality: RevenueQuality        = RevenueQuality.UNVERIFIED
    profit_quality: ProfitQuality          = ProfitQuality.UNVERIFIED
    cash_conversion: CashConversionQuality = CashConversionQuality.INSUFFICIENT

    # Key metrics for display
    revenue_yoy_pct: Optional[float]       = None
    revenue_qoq_pct: Optional[float]       = None
    op_profit_yoy_pct: Optional[float]     = None
    net_profit_yoy_pct: Optional[float]    = None
    opm_current: Optional[float]           = None    # %
    opm_yoy_delta_bps: Optional[float]     = None    # basis points
    npm_current: Optional[float]           = None
    cfo_pat_ratio: Optional[float]         = None
    cfo_pat_3y_avg: Optional[float]        = None
    ttm_revenue: Optional[float]           = None
    ttm_net_profit: Optional[float]        = None

    # Findings
    findings: List[ForensicFinding]        = field(default_factory=list)
    ai_findings: List[ForensicFinding]     = field(default_factory=list)  # AI-sourced only
    investor_questions: List[str]          = field(default_factory=list)

    # Evidence tracking
    evidence_coverage_pct: float           = 0.0    # fraction of checks with sufficient data
    evidence_missing: List[str]            = field(default_factory=list)

    # Change vs prior report
    prior_report_id: Optional[int]         = None
    new_findings_count: int                = 0
    repeated_findings_count: int           = 0
    resolved_findings_count: int           = 0

    # Meta
    ai_model_used: Optional[str]           = None
    status: ReportStatus                   = ReportStatus.PENDING
    error_message: Optional[str]           = None
    created_at: Optional[str]              = None
    updated_at: Optional[str]             = None

    def top_findings(self, n: int = 3) -> List[ForensicFinding]:
        """Returns top N findings by severity, then evidence level."""
        sev_order = {FindingSeverity.HIGH: 0, FindingSeverity.MEDIUM: 1,
                     FindingSeverity.LOW: 2, FindingSeverity.INFO: 3}
        ev_order = {EvidenceLevel.STRONG: 0, EvidenceLevel.MODERATE: 1,
                    EvidenceLevel.WEAK: 2, EvidenceLevel.EVIDENCE_INSUFFICIENT: 3}
        all_f = [f for f in self.findings if f.classification != FindingClassification.EVIDENCE_GAP]
        all_f += [f for f in self.ai_findings if f.severity in (FindingSeverity.HIGH, FindingSeverity.MEDIUM)]
        all_f.sort(key=lambda f: (sev_order.get(f.severity, 9), ev_order.get(f.evidence_level, 9)))
        return all_f[:n]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for JSONB storage."""
        def _serialize(obj):
            if isinstance(obj, Enum):
                return obj.value
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _serialize(v) for k, v in obj.__dict__.items()}
            if isinstance(obj, list):
                return [_serialize(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            return obj

        return _serialize(self.__dict__)
