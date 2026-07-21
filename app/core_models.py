from dataclasses import dataclass, field

@dataclass
class ScanFailure:
    symbol: str
    provider: str
    reason: str
    exception_type: str
    scanner: str
    stage: str

@dataclass
class ProviderStats:
    provider_name: str
    requests: int = 0
    retries: int = 0
    fallbacks: int = 0
    failures: int = 0

    def record_request(self):
        self.requests += 1

    def record_retry(self):
        self.retries += 1
        
    def record_fallback(self):
        self.fallbacks += 1
        
    def record_failure(self):
        self.failures += 1

@dataclass
class ScannerMetrics:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    provider_stats: dict[str, ProviderStats] = field(default_factory=dict)

    def get_provider(self, name: str) -> ProviderStats:
        if name not in self.provider_stats:
            self.provider_stats[name] = ProviderStats(provider_name=name)
        return self.provider_stats[name]

from datetime import date
from decimal import Decimal
from typing import Optional, List
from core_enums import PivotKind, CandidateState, RejectionReason

class DataQualityError(Exception):
    def __init__(self, reason: RejectionReason, message: str = None):
        self.reason = reason
        self.message = message or reason.value
        super().__init__(self.message)

@dataclass(frozen=True)
class SwingPoint:
    index: int
    date: date
    price: float
    kind: PivotKind
    is_plateau: bool

@dataclass(frozen=True)
class ImpulseLeg:
    start: SwingPoint
    end: SwingPoint
    gain_pct: float
    atr_multiple: float
    median_volume: float

@dataclass(frozen=True)
class StageResult:
    stage: str
    gate: str
    passed: bool
    observed_value: Optional[float]
    threshold: Optional[float]
    comparator: str
    message: Optional[str] = None

@dataclass
class PullbackStructure:
    symbol: str
    as_of_date: date
    impulse: Optional[ImpulseLeg]
    pullback_low: Optional[SwingPoint]
    depth_pct: Optional[float]
    duration_bars: Optional[int]
    volume_ratio: Optional[float]
    internal_swing_count: int
    closed_below_sma50: bool
    min_rsi_during_pullback: Optional[float]
    pullback_count_in_trend: int
    valid: bool
    rejection_reason: Optional[RejectionReason]
    stage_results: List[StageResult] = field(default_factory=list)
    debug: Optional[dict] = None

@dataclass(frozen=True)
class TriggerSignal:
    date: date
    entry_price: Decimal
    trigger_low: Decimal
    body_atr_ratio: float
    upper_wick_ratio: float
    gap_pct: float
    volume_mult: float
    valid: bool
    rejection_reason: Optional[RejectionReason]

@dataclass
class PullbackCandidate:
    symbol: str
    as_of_date: date
    structure: PullbackStructure
    trigger: TriggerSignal
    entry_price: Decimal
    base_score: float = 0.0
    final_score: float = 0.0
    warnings: List[str] = field(default_factory=list)
    status: CandidateState = CandidateState.NEW
    suppressed_by: Optional[str] = None
    config_version: str = ""

