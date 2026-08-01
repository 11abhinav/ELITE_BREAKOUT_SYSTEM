import logging
import threading
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from ..core.models import NormalizedMarketData
from .provider_manager import ProviderManager

logger = logging.getLogger(__name__)

class FetchCoordinator:
    """
    Deduplicates concurrent requests for the same symbol/timeframe.
    Routes actual API calls to the healthiest Provider via ProviderManager.
    """
    def __init__(self, provider_manager: ProviderManager):
        self.provider_manager = provider_manager
        
        # In-flight request tracking: key -> (Event, result)
        self._inflight: Dict[str, Tuple[threading.Event, Optional[NormalizedMarketData]]] = {}
        self._lock = threading.Lock()
        
    def _get_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def fetch_delta(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> Optional[NormalizedMarketData]:
        key = self._get_key(symbol, timeframe)
        
        with self._lock:
            if key in self._inflight:
                logger.info(f"Deduplicating fetch for {key}. Awaiting existing Future.")
                event, _ = self._inflight[key]
                is_new = False
            else:
                event = threading.Event()
                self._inflight[key] = (event, None)
                is_new = True
                
        if not is_new:
            event.wait()
            with self._lock:
                # Retrieve result populated by the worker thread
                _, result = self._inflight.get(key, (None, None))
                return result
                
        # We are the designated fetcher thread
        try:
            provider = self.provider_manager.get_best_provider(timeframe)
            if not provider:
                raise ValueError(f"No healthy provider for {timeframe}")
                
            result = provider.fetch_ohlcv(symbol, timeframe, range_from, range_to)
            
        except Exception as e:
            logger.error(f"FetchCoordinator failed to fetch {symbol}: {e}")
            result = None
            
        finally:
            with self._lock:
                # Store result and wake up any deduplicated threads
                self._inflight[key] = (event, result)
                event.set()
                # Clean up memory
                del self._inflight[key]
                
        return result
        
    def fetch_batch_delta(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        # For simplicity, route batch directly to provider. 
        # Advanced implementations would split batch by inflight futures.
        provider = self.provider_manager.get_best_provider(timeframe)
        if not provider:
            logger.error(f"No healthy provider for batch {timeframe}")
            return {}
            
        return provider.fetch_batch_ohlcv(symbols, timeframe, range_from, range_to)
