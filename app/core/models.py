from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum

class MetricSource(Enum):
    YAHOO_FINANCE = "yahoo"
    FUNDAMENTAL_CACHE = "fundamental_cache"
    TECHNICAL = "technical"
    DERIVED = "derived"

class ExitState(Enum):
    BUY = "BUY"
    ADD = "ADD"
    HOLD = "HOLD"
    TRIM = "TRIM"
    SELL = "SELL"

@dataclass
class MetricResult:
    """Represents a single evaluated metric."""
    name: str
    value: Optional[float]
    confidence: float # 0.0 to 100.0
    source: MetricSource
    explanation: str = ""
    score_contribution: float = 0.0

# [VERSION: CORE_MODELS_DATACLASS_FIX_v1.0] Dataclass additions for core engines
@dataclass
class PillarResult:
    """Represents a single evaluated pillar of metrics."""
    name: str
    score: float
    confidence: float
    weight: float = 1.0
    metrics: List[MetricResult] = field(default_factory=list)

@dataclass
class CoreScoreResult:
    """Core score output containing all evaluated pillars."""
    quality: PillarResult = field(default_factory=lambda: PillarResult("Quality", 0.0, 0.0))
    growth: PillarResult = field(default_factory=lambda: PillarResult("Growth", 0.0, 0.0))
    value: PillarResult = field(default_factory=lambda: PillarResult("Value", 0.0, 0.0))
    risk: PillarResult = field(default_factory=lambda: PillarResult("Risk", 0.0, 0.0))
    capital_allocation: PillarResult = field(default_factory=lambda: PillarResult("Capital Allocation", 0.0, 0.0))
    momentum: PillarResult = field(default_factory=lambda: PillarResult("Momentum", 0.0, 0.0))

@dataclass
class EmergingScoreResult:
    """Layer 4.6 output specific to emerging trajectory."""
    financial_improvement: PillarResult = field(default_factory=lambda: PillarResult("Financial Improvement", 0.0, 0.0))
    growth_improvement: PillarResult = field(default_factory=lambda: PillarResult("Growth Improvement", 0.0, 0.0))
    market_recognition: PillarResult = field(default_factory=lambda: PillarResult("Market Recognition", 0.0, 0.0))

@dataclass
class ImprovementResult:
    """Layer 4.5 output for trajectory improvement detection."""
    revenue_acceleration: bool = False
    margin_expansion: bool = False
    roic_improving: bool = False
    debt_reducing: bool = False

    @property
    def has_improvement(self) -> bool:
        return any([self.revenue_acceleration, self.margin_expansion, self.roic_improving, self.debt_reducing])

@dataclass
class FinalScannerResult:
    """Final scanner output candidate structure."""
    symbol: str
    classification: Any = "Watchlist"
    composite_score: float = 0.0
    action: str = "HOLD"
    reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0

@dataclass
class EngineResult:
    """Standardized output for all V5 engines."""
    score: float 
    confidence: float 
    warnings: List[str] = field(default_factory=list)
    missing_metrics: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

@dataclass
class ValuationResult(EngineResult):
    """Layer 5 output specific to valuation."""
    fair_value: float = 0.0
    bear_value: float = 0.0
    bull_value: float = 0.0
    margin_of_safety: float = 0.0
    
    # Individual Model Outputs
    dcf_value: Optional[float] = None
    peer_relative_value: Optional[float] = None
    graham_value: Optional[float] = None
    epv_value: Optional[float] = None
    asset_value: Optional[float] = None

@dataclass
class BuyZoneResult:
    """Layer 7 output."""
    in_buy_zone: bool = False
    buy_zone_low: float = 0.0
    buy_zone_high: float = 0.0
    reason: str = ""

@dataclass
class InvestmentDecision:
    """Final output object containing unified V5 results."""
    symbol: str
    
    # Layer Outputs
    quality: EngineResult
    growth: EngineResult
    financial_strength: EngineResult
    valuation: ValuationResult
    market_structure: EngineResult
    buy_zone: BuyZoneResult
    
    # Final Computations
    composite_score: float = 0.0
    raw_composite_score: float = 0.0
    confidence: float = 0.0
    classification: str = "Watchlist"
    
    # Price
    current_price: float = 0.0
    
    # Rejection & Audit
    is_invalidated: bool = False
    invalidation_reason: str = ""
    audit_trail: list = field(default_factory=list)
    
    # Versioning
    engine_version: str = "V5.0.0"
    weights_profile: str = "default"
    weights_version: str = "1.0"
    valuation_version: str = "1.0"
    timestamp: str = ""

@dataclass
class AuditTrailEntry:
    """Structured log entry for all decisions (Pass/Warning/Fail)."""
    symbol: str
    layer: str
    status: str # "Passed", "Warning", "Failed"
    reason: str
    metric: str
    value: Any

@dataclass
class AlertOutcome:
    """Represents a tracked trade alert outcome and feature snapshot."""
    alert_id: int
    leg: int
    symbol: str
    scanner: str
    regime: str
    regime_score: float
    base_score: int
    rs_bonus: int
    sector_bonus: int
    rs_percentile: float
    sector_name: str
    rr_at_alert: float
    atr_pct_at_alert: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    alert_timestamp: Optional[str] = None
    exit_timestamp: Optional[str] = None
    exit_reason: Optional[str] = None  # 'T1_HIT', 'T2_HIT', 'SL_HIT', 'AMBIGUOUS_SL_HIT', 'EXPIRED_POS', 'EXPIRED_NEG'
    realized_rr: Optional[float] = None
    unrealized_rr_at_expiry: Optional[float] = None
    holding_period_bars: Optional[int] = None
    max_favorable_excursion_r: float = 0.0
    max_adverse_excursion_r: float = 0.0

@dataclass
class SectorRank:
    """Represents daily sector ranking with 3-session hysteresis."""
    sector_symbol: str
    sector_name: str
    ranking_date: str
    blended_score: float
    raw_rank: int
    consecutive_top3_days: int = 0
    consecutive_bottom3_days: int = 0
    effective_status: str = "NEUTRAL"  # "TAILWIND", "HEADWIND", "NEUTRAL"

@dataclass
class ConfluenceMatch:
    """Represents a cross-scanner confluence match."""
    symbol: str
    match_date: str
    fm_score: float
    rs_percentile: float
    matched_scanners: List[str] = field(default_factory=list)
    confluence_score: float = 95.0
    is_elite: bool = True

