import pandas as pd
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

class CacheState(Enum):
    READY = "READY"
    BUILDING = "BUILDING"
    STALE = "STALE"
    INVALID = "INVALID"
    REPAIRING = "REPAIRING"
    EXPIRED = "EXPIRED"

class ProviderStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    AUTH_FAILED = "AUTH_FAILED"

@dataclass
class CapabilityMatrix:
    supports_1m: bool = False
    supports_5m: bool = True
    supports_15m: bool = True
    supports_1h: bool = True
    supports_1d: bool = True
    supports_corporate_actions: bool = False
    supports_oi: bool = False

@dataclass
class DataProvenance:
    provider: str
    fetch_time: datetime
    latency_ms: float
    validation_score: float
    repair_count: int = 0
    cache_version: str = "1.0"
    schema_version: str = "1.0"
    is_adjusted: bool = True

@dataclass
class NormalizedMarketData:
    """
    The strict Data Contract. Every provider MUST return this.
    The dataframe MUST contain: Datetime (Index), Open, High, Low, Close, Volume.
    """
    symbol: str
    timeframe: str
    dataframe: pd.DataFrame
    provenance: DataProvenance
    is_complete_candle: bool = True
    quality_score: float = 100.0
    error: Optional[str] = None
    
    @property
    def is_valid(self) -> bool:
        return self.error is None and not self.dataframe.empty

    def __post_init__(self):
        # [RULE 67 CHANGE-RATIONALE: CRITICAL WEEKEND CANDLE BAN]
        # Invariant enforced at lowest common data-contract layer so every provider payload
        # has Saturday and Sunday candles purged instantly upon model instantiation.
        if self.dataframe is not None and not self.dataframe.empty:
            from trading_calendar import enforce_trading_day_candles
            self.dataframe = enforce_trading_day_candles(self.dataframe, self.symbol)

