import enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime

class FundamentalStatus(enum.Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_SINGLE_SOURCE = "VERIFIED_SINGLE_SOURCE"
    PARTIAL_RECOVERY = "PARTIAL_RECOVERY"
    DATA_RECOVERED = "DATA_RECOVERED"
    DATA_CONFLICT = "DATA_CONFLICT"
    INSUFFICIENT = "INSUFFICIENT"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    STATEMENT_MISMATCH = "STATEMENT_MISMATCH"
    STALE = "STALE"
    FUTURE = "FUTURE"
    INVALID = "INVALID"
    SOURCE_ERROR = "SOURCE_ERROR"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    CALCULATION_FAILURE = "CALCULATION_FAILURE"
    BLOCKED = "BLOCKED"
    NOT_ATTEMPTED_BUDGET_EXHAUSTED = "NOT_ATTEMPTED_BUDGET_EXHAUSTED"
    SOURCE_DATA_PRESENT_PARSE_OR_MAPPING_FAILURE = "SOURCE_DATA_PRESENT_PARSE_OR_MAPPING_FAILURE"
    SOURCE_DATA_PRESENT_PARSE_FAILURE = "SOURCE_DATA_PRESENT_PARSE_FAILURE"
    SOURCE_DATA_PRESENT_MAPPING_FAILURE = "SOURCE_DATA_PRESENT_MAPPING_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    NO_FILING_FOUND = "NO_FILING_FOUND"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_HISTORY = "STALE_HISTORY"
    INVALID_VALUE = "INVALID_VALUE"
    STRUCTURAL_INELIGIBLE = "STRUCTURAL_INELIGIBLE"
    PIT_CONFLICT = "PIT_CONFLICT"
    REVISION_PENDING = "REVISION_PENDING"

class StatementType(enum.Enum):
    INCOME_STATEMENT = "INCOME_STATEMENT"
    BALANCE_SHEET = "BALANCE_SHEET"
    CASH_FLOW = "CASH_FLOW"

class ConsolidationType(enum.Enum):
    CONSOLIDATED = "CONSOLIDATED"
    STANDALONE = "STANDALONE"

@dataclass
class FundamentalProvenance:
    symbol: str
    metric: str
    value: float
    source: str
    financial_period: str
    filing_date: Optional[str]
    retrieved_at: datetime
    statement_type: Optional[StatementType]
    consolidated_or_standalone: Optional[ConsolidationType]
    calculation_version: str
    validation_status: FundamentalStatus

@dataclass
class RawFinancialRecord:
    symbol: str
    source: str
    period_end_date: str
    period_type: str  # e.g., "ANNUAL", "QUARTERLY"
    consolidation: ConsolidationType
    revenue: Optional[float] = None
    net_profit: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    ebit: Optional[float] = None
    capital_employed: Optional[float] = None
    eps: Optional[float] = None
    availability_date: Optional[str] = None
    broadcast_timestamp: Optional[str] = None
    version: str = "v1"
    validation_status: str = "VALID"
    unit: Optional[str] = None
    currency: Optional[str] = None
    period_start_date: Optional[str] = None
    duration_days: Optional[int] = None
    fiscal_year: Optional[str] = None
    source_document_hash: Optional[str] = None
    filing_id: Optional[str] = None
    revision_indicator: Optional[str] = None
    raw_fact_details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def statement_basis(self) -> str:
        if hasattr(self.consolidation, "value"):
            return self.consolidation.value
        return str(self.consolidation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revenue": self.revenue,
            "net_profit": self.net_profit,
            "operating_cash_flow": self.operating_cash_flow,
            "total_debt": self.total_debt,
            "total_equity": self.total_equity,
            "ebit": self.ebit,
            "capital_employed": self.capital_employed,
            "eps": self.eps
        }

@dataclass
class ReconciledCanonicalMetrics:
    symbol: str
    roce_5y: Optional[float] = None
    sales_cagr_5y: Optional[float] = None
    pat_cagr_5y: Optional[float] = None
    cfo_pat_5y: Optional[float] = None
    debt_to_equity: Optional[float] = None
    q_eps_growth_yoy: Optional[float] = None
    q_sales_growth_yoy: Optional[float] = None
    eps_ttm: Optional[float] = None
    provenance_chain: List[FundamentalProvenance] = field(default_factory=list)
    overall_status: FundamentalStatus = FundamentalStatus.INSUFFICIENT

    @property
    def recovered_fields(self) -> Dict[str, Any]:
        """Returns dictionary of metrics that have a valid (non-None) value."""
        fields = (
            "roce_5y",
            "sales_cagr_5y",
            "pat_cagr_5y",
            "cfo_pat_5y",
            "debt_to_equity",
            "q_eps_growth_yoy",
            "q_sales_growth_yoy",
            "eps_ttm",
        )
        return {f: getattr(self, f) for f in fields if getattr(self, f) is not None}


class DataFailureClass(enum.Enum):
    API_VALUE_CONFIRMED = "API_VALUE_CONFIRMED"
    API_VALUE_CONFIRMED_BUT_INVALID_FOR_RULE = "API_VALUE_CONFIRMED_BUT_INVALID_FOR_RULE"
    API_HAS_DATA_PARSER_FAILURE = "API_HAS_DATA_PARSER_FAILURE"
    API_HAS_DATA_BUT_PERIOD_GAP = "API_HAS_DATA_BUT_PERIOD_GAP"
    API_HAS_DATA_BUT_SCOPE_MISMATCH = "API_HAS_DATA_BUT_SCOPE_MISMATCH"
    CALCULATION_FAILURE = "CALCULATION_FAILURE"
    API_DATA_GENUINELY_UNAVAILABLE = "API_DATA_GENUINELY_UNAVAILABLE"
    STRUCTURAL_HISTORY_DEFICIT = "STRUCTURAL_HISTORY_DEFICIT"
    CORPORATE_RESTRUCTURING_REMAPPING = "CORPORATE_RESTRUCTURING_REMAPPING"


class QuarantineAction(enum.Enum):
    IMMEDIATE_REPROCESS = "IMMEDIATE_REPROCESS"
    IMMEDIATE_REMAPPING = "IMMEDIATE_REMAPPING"
    QUARANTINE_COOLDOWN = "QUARANTINE_COOLDOWN"
    EVENT_DRIVEN_ANNUAL_WAIT = "EVENT_DRIVEN_ANNUAL_WAIT"
    STRATEGY_BLOCK_NO_REFETCH = "STRATEGY_BLOCK_NO_REFETCH"


@dataclass
class MetricResolutionResult:
    symbol: str
    metric: str
    value: Optional[float]
    source: str
    source_rank: int
    period_start: Optional[str]
    period_end: Optional[str]
    broadcast_timestamp: Optional[str]
    statement_scope: str
    raw_document_hash: str
    calculation_method: str
    validation_status: str
    failure_class: Optional[DataFailureClass] = None
    difference_pct: Optional[float] = None
    independent_reference_value: Optional[Any] = None


@dataclass
class DataQuarantineRecord:
    symbol: str
    metric: str
    failure_class: str
    action: str
    quarantined_at: str
    cooldown_until: str
    cooldown_days: int
    provenance_hash: str
    reason: str

