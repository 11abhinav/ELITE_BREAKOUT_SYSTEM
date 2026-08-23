import logging
import pandas as pd
from typing import Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from ..core.models import NormalizedMarketData

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

class RequestPlanner:
    """
    Evaluates requested date ranges against existing cached data.
    Decides if a network fetch can be skipped entirely, or computes the exact Delta range required.
    """
    def __init__(self):
        pass
        
    def evaluate(self, 
                 cached_data: Optional[NormalizedMarketData], 
                 range_from: datetime, 
                 range_to: datetime,
                 timeframe: str) -> Tuple[bool, Optional[datetime], Optional[datetime]]:
        """
        Returns: (needs_fetch, delta_range_from, delta_range_to)
        """
        now = datetime.now(IST)
        
        # 1. No cache exists at all
        if not cached_data or cached_data.dataframe.empty:
            return True, range_from, range_to
            
        df = cached_data.dataframe
        
        # 2. Extract timestamps from cache
        if "Datetime" in df.columns:
            last_ts = df["Datetime"].iloc[-1]
        elif "Date" in df.columns:
            last_ts = df["Date"].iloc[-1]
        else:
            last_ts = df.index[-1]
            
        last_ts = pd.to_datetime(last_ts)
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize(IST)
        else:
            last_ts = last_ts.tz_convert(IST)
            
        # 3. Check if cache is fresh enough
        # For simplicity, if last_ts is today, and timeframe is daily, we consider it fresh
        if timeframe in ("1d", "1D", "D"):
            if last_ts.date() >= now.date():
                return False, None, None
                
        # 4. If cache is stale, compute delta
        # Delta range_from should be (last_ts - 1 day) to ensure overlapping candles
        from datetime import timedelta
        
        delta_from = last_ts - timedelta(days=1)
        
        # If the requested range_from is earlier than our cache's earliest data, 
        # we need a FULL fetch instead of a delta.
        earliest_ts = pd.to_datetime(df.index[0])
        if earliest_ts.tzinfo is None:
            earliest_ts = earliest_ts.tz_localize(IST)
            
        if range_from < earliest_ts:
            return True, range_from, range_to
            
        return True, delta_from, range_to
