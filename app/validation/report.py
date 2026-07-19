from dataclasses import dataclass
from typing import Tuple, Optional
import pandas as pd

@dataclass(frozen=True)
class DataQualityReport:
    """
    Immutable published output of the ValidationEngine, consumed by Cache Engines and Scanners.
    """
    is_valid: bool
    quality_score: int
    critical_failures: Tuple[str, ...]
    
    # Metadata for Cache Engines
    row_count: int = 0
    missing_pct: float = 0.0
    stale_days: int = 0
    
    # Traceability
    validator_name: str = "Unknown"
    validator_version: str = "1.0"

@dataclass
class MarketData:
    dataframe: Optional[pd.DataFrame]
    source: str
    quality_report: Optional[DataQualityReport]
    stale: bool
    used_fallback: bool
    error: Optional[str] = None
