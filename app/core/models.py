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

class ClassificationTier(Enum):
    TIER_A = "Tier A: Emerging Compounder"
    TIER_B = "Tier B: Established Compounder"
    TIER_C = "Tier C: Turnaround"
    TIER_D = "Tier D: Deep Value"
    TIER_E = "Tier E: Watchlist"
    INVALIDATED = "Invalidated"

@dataclass
class MetricResult:
    """Represents a single evaluated metric with audit trail."""
    name: str
    value: Optional[float]
    confidence: float # 0.0 to 1.0 (e.g. 0.6 if using 3Y instead of 5Y history)
    coverage: float   # 0.0 to 1.0 (is data available)
    source: MetricSource
    freshness_days: int
    history_length_used: Optional[int] = None # e.g. 1, 3, 5 years
    explanation: str = "" # "ROIC > 15% (3Y avg) (+8 pts)"
    score_contribution: float = 0.0

@dataclass
class PillarResult:
    """Represents an aggregated pillar score."""
    name: str
    score: float
    confidence: float
    metrics: List[MetricResult] = field(default_factory=list)

@dataclass
class ImprovementResult:
    """Layer 4.5 Binary Improvement Detection"""
    revenue_acceleration: bool = False
    margin_expansion: bool = False
    roic_improving: bool = False
    debt_reducing: bool = False
    
    @property
    def has_improvement(self) -> bool:
        return any([
            self.revenue_acceleration, 
            self.margin_expansion, 
            self.roic_improving, 
            self.debt_reducing
        ])

@dataclass
class CoreScoreResult:
    """Layer 4: Six Pillar Static Score"""
    quality: PillarResult
    growth: PillarResult
    value: PillarResult
    risk: PillarResult
    capital_allocation: PillarResult
    momentum: PillarResult
    
    @property
    def overall_score(self) -> float:
        return (
            (self.quality.score * 0.30) +
            (self.growth.score * 0.20) +
            (self.value.score * 0.20) +
            (self.risk.score * 0.10) +
            (self.capital_allocation.score * 0.10) +
            (self.momentum.score * 0.10)
        )

@dataclass
class EmergingScoreResult:
    """Layer 5: Emerging Trajectory Score"""
    financial_improvement: PillarResult # 40%
    growth_improvement: PillarResult    # 40%
    market_recognition: PillarResult    # 20%
    
    @property
    def overall_score(self) -> float:
        return (
            (self.financial_improvement.score * 0.40) +
            (self.growth_improvement.score * 0.40) +
            (self.market_recognition.score * 0.20)
        )

@dataclass
class FinalScannerResult:
    """Final merged result from the scanner pipeline"""
    symbol: str
    static_score: CoreScoreResult
    improvement: ImprovementResult
    emerging_score: Optional[EmergingScoreResult]
    classification: ClassificationTier
    exit_state: ExitState
    confidence: float
    freshness: int # min days old
    
    # Technical entry info
    in_buy_zone: bool = False
    buy_zone_low: float = 0.0
    buy_zone_high: float = 0.0
    
    # Portfolio Optimizer info
    is_portfolio_candidate: bool = False
    
    # Full audit text
    audit_trail: str = ""
