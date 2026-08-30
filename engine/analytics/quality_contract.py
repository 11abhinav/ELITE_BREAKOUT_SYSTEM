"""
Quality Contract Specification
Final Common Quality Contract for Elite Breakout Scanner Ecosystem.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

class ScannerType(str, Enum):
    EOD = "EOD"
    MULTIBAGGER = "MULTIBAGGER"
    PULLBACK = "PULLBACK"
    MULTI_TF = "MULTI_TF"
    DAILY_BUILDER = "DAILY_BUILDER"
    REVERSAL = "REVERSAL"
    WEALTH_ENGINE = "WEALTH_ENGINE"

class QualityAction(str, Enum):
    PASS_THROUGH = "PASS_THROUGH"
    RANK_BOOST = "RANK_BOOST"
    RANK_DOWNGRADE = "RANK_DOWNGRADE"
    FILTER_DISCARD = "FILTER_DISCARD"
    SIZE_SCALE_UP = "SIZE_SCALE_UP"
    SIZE_SCALE_DOWN = "SIZE_SCALE_DOWN"

class IntegrityStatus(str, Enum):
    VALID = "VALID"
    INVALID_SCALE = "INVALID_SCALE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    INVALID_PIT = "INVALID_PIT"
    INVALID_TIMEFRAME = "INVALID_TIMEFRAME"
    DUPLICATE_SETUP = "DUPLICATE_SETUP"
    PENDING = "PENDING"

@dataclass
class QualityAlertContract:
    # 1. Alert Identification
    scanner: ScannerType
    alert_id: str
    setup_id: str
    decision_timestamp: datetime
    symbol: str
    
    # 2. Execution Geometry
    entry_price: float
    stop_price: float
    target_price: float
    risk_distance: float
    side: str = "LONG"
    timeframe: str = "1D"
    
    # 3. Macro / Sector Context
    market_regime: str = "NEUTRAL"
    sector_regime: str = "NEUTRAL"
    
    # 4. Quality Scoring & Action
    quality_score: float = 50.0
    quality_tier: str = "STANDARD"
    quality_action: QualityAction = QualityAction.PASS_THROUGH
    
    # 5. Realized Outcome Metrics
    gross_realized_R: Optional[float] = None
    net_realized_R: Optional[float] = None
    MFE_R: Optional[float] = None
    MAE_R: Optional[float] = None
    exit_reason: Optional[str] = None
    time_to_exit_bars: Optional[int] = None
    
    # 6. Data Integrity & Replay Status
    PIT_status: IntegrityStatus = IntegrityStatus.VALID
    geometry_status: IntegrityStatus = IntegrityStatus.VALID
    scale_status: IntegrityStatus = IntegrityStatus.VALID
    replay_status: IntegrityStatus = IntegrityStatus.VALID
    event_identity_status: IntegrityStatus = IntegrityStatus.VALID
    outcome_status: IntegrityStatus = IntegrityStatus.VALID
    
    # Feature snapshots available at decision
    features_at_decision: Dict[str, Any] = field(default_factory=dict)
    
    def validate_geometry(self) -> bool:
        if self.entry_price <= 0 or self.stop_price <= 0 or self.target_price <= 0:
            self.geometry_status = IntegrityStatus.INVALID_GEOMETRY
            return False
        if self.target_price == self.entry_price:
            self.geometry_status = IntegrityStatus.INVALID_GEOMETRY
            return False
        if self.side.upper() == "LONG":
            if not (self.stop_price < self.entry_price < self.target_price):
                self.geometry_status = IntegrityStatus.INVALID_GEOMETRY
                return False
        elif self.side.upper() == "SHORT":
            if not (self.target_price < self.entry_price < self.stop_price):
                self.geometry_status = IntegrityStatus.INVALID_GEOMETRY
                return False
        self.geometry_status = IntegrityStatus.VALID
        return True
