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
