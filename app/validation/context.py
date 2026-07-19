from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class ValidationContext:
    """
    Holds contextual information about the validation request.
    This replaces passing multiple explicit arguments and scales as new requirements emerge.
    """
    cache_df: Optional[pd.DataFrame] = None
    provider: str = ""
    interval: str = ""
    period: str = ""
    range_from: Optional[str] = None
    range_to: Optional[str] = None
    fetch_mode: str = "FULL"  # "FULL" or "DELTA"
