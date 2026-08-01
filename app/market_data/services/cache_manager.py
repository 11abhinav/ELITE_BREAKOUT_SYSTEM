import os
import time
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

from ..core.models import NormalizedMarketData, CacheState, DataProvenance

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

class CacheManager:
    """
    Manages reading and writing NormalizedMarketData to/from Parquet on disk.
    Strictly separates raw data caching from indicator calculations.
    """
    def __init__(self):
        self.cache_dir = os.path.join(DATA_DIR, "market_data_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        
    def _get_file_path(self, symbol: str, timeframe: str) -> str:
        safe_sym = symbol.replace(":", "_").replace("/", "_")
        tf_dir = os.path.join(self.cache_dir, timeframe)
        os.makedirs(tf_dir, exist_ok=True)
        return os.path.join(tf_dir, f"{safe_sym}.parquet")

    def get_cache(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> Optional[NormalizedMarketData]:
        file_path = self._get_file_path(symbol, timeframe)
        if not os.path.exists(file_path):
            return None
            
        try:
            df = pd.read_parquet(file_path)
            if df.empty:
                return None
                
            # Verify if the cache covers the requested range
            # Note: For strict implementation, we would compare min/max dates
            # Here we just return the full cached dataframe, RequestPlanner handles deltas
            
            # Reconstruct provenance from attrs if saved, else dummy
            prov_dict = df.attrs.get("provenance", {})
            provenance = DataProvenance(
                provider=prov_dict.get("provider", "Cache"),
                fetch_time=datetime.now(IST),
                latency_ms=0.0,
                validation_score=100.0
            )
            
            return NormalizedMarketData(
                symbol=symbol,
                timeframe=timeframe,
                dataframe=df,
                provenance=provenance,
                is_complete_candle=True,
                quality_score=100.0,
                error=None
            )
        except Exception as e:
            logger.error(f"Failed to read cache for {symbol} {timeframe}: {e}")
            return None

    def update_cache(self, data: NormalizedMarketData) -> None:
        """Saves data to parquet. If file exists, merges delta gracefully."""
        if not data.is_valid:
            return
            
        file_path = self._get_file_path(data.symbol, data.timeframe)
        df_new = data.dataframe.copy()
        
        try:
            if os.path.exists(file_path):
                df_old = pd.read_parquet(file_path)
                if not df_old.empty:
                    # Merge old and new, dropping duplicates by index (Datetime/Date)
                    df_combined = pd.concat([df_old, df_new])
                    df_combined = df_combined[~df_combined.index.duplicated(keep='last')].sort_index()
                else:
                    df_combined = df_new
            else:
                df_combined = df_new
                
            # Save provenance as metadata
            df_combined.attrs["provenance"] = {
                "provider": data.provenance.provider,
                "fetch_time": data.provenance.fetch_time.isoformat(),
            }
            
            df_combined.to_parquet(file_path)
            logger.debug(f"Updated cache for {data.symbol} {data.timeframe} (Rows: {len(df_combined)})")
        except Exception as e:
            logger.error(f"Failed to write cache for {data.symbol} {data.timeframe}: {e}")
