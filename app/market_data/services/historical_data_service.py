import logging
from typing import Dict, List, Optional
from datetime import datetime

from ..core.models import NormalizedMarketData
from .cache_manager import CacheManager
from .fetch_coordinator import FetchCoordinator
from .request_planner import RequestPlanner

logger = logging.getLogger(__name__)

class HistoricalDataService:
    """
    The main public API for all Scanners. 
    Scanners ONLY interact with this service and never touch providers directly.
    Enforces the rule: Fetch Once -> Compute Once -> Cache Once -> Reuse Many Times.
    """
    
    def __init__(self, cache_manager: CacheManager, fetch_coordinator: FetchCoordinator, request_planner: RequestPlanner):
        self.cache_manager = cache_manager
        self.fetch_coordinator = fetch_coordinator
        self.request_planner = request_planner

    def get_history(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> Optional[NormalizedMarketData]:
        """
        Retrieves historical data for a single symbol.
        1. Checks CacheManager (Is it READY? Are we requesting overlapping dates?)
        2. If delta is needed, calls FetchCoordinator to deduplicate and dispatch request.
        3. Returns fully validated NormalizedMarketData.
        """
        logger.debug(f"[HistoricalDataService] Requesting {symbol} {timeframe} from {range_from} to {range_to}")
        
        # 1. Ask CacheManager if data is fully available
        cached_data = self.cache_manager.get_cache(symbol, timeframe, range_from, range_to)
        
        # 2. Ask RequestPlanner if we actually need a fetch
        needs_fetch, delta_from, delta_to = self.request_planner.evaluate(cached_data, range_from, range_to, timeframe)
        
        if not needs_fetch:
            return cached_data
            
        # 3. Route to FetchCoordinator for delta fetch
        fetched_data = self.fetch_coordinator.fetch_delta(symbol, timeframe, delta_from, delta_to)
        
        # 4. Store new data in cache
        if fetched_data and fetched_data.is_valid:
            self.cache_manager.update_cache(fetched_data)
            # Re-read from cache to get the fully merged dataset
            return self.cache_manager.get_cache(symbol, timeframe, range_from, range_to)
            
        return cached_data or fetched_data

    def get_batch_history(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        """
        Optimized batch retrieval for scanners.
        """
        results = {}
        missing_symbols = []
        
        # Check cache for all
        for sym in symbols:
            cached = self.cache_manager.get_cache(sym, timeframe, range_from, range_to)
            if cached and cached.is_complete_candle:
                results[sym] = cached
            else:
                missing_symbols.append(sym)
                
        if missing_symbols:
            logger.info(f"[HistoricalDataService] {len(missing_symbols)} symbols missing/stale in cache. Delegating to FetchCoordinator.")
            fetched = self.fetch_coordinator.fetch_batch_delta(missing_symbols, timeframe, range_from, range_to)
            for sym, data in fetched.items():
                if data and data.is_valid:
                    self.cache_manager.update_cache(data)
                results[sym] = data
                
        return results
