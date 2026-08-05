import logging
from typing import Dict, List
import time
from data_providers.unified_fetcher import fetcher

logger = logging.getLogger(__name__)
_dead_symbols_cache = {}
_DEAD_TTL = 3600 * 24  # 24 hours
_MAX_DEAD_CACHE_SIZE = 1000

_recent_quotes_cache = {}
_RECENT_TTL = 30  # 30 seconds for concurrent timeframe syncing
import threading
_live_prices_lock = threading.Lock()

def _get_dead_symbols() -> dict:
    from session_context import get_session_cache_or_fallback
    return get_session_cache_or_fallback("dead_symbols", _dead_symbols_cache, logger)

def _cleanup_dead_symbols_cache():
    now = time.time()
    cache = _get_dead_symbols()
    # 1. Remove expired
    expired_keys = [k for k, v in cache.items() if now - v > _DEAD_TTL]
    for k in expired_keys:
        del cache[k]
        
    # 2. If still over limit, remove oldest (Python 3.7+ dicts preserve insertion order)
    if len(cache) > _MAX_DEAD_CACHE_SIZE:
        excess = len(cache) - _MAX_DEAD_CACHE_SIZE
        oldest_keys = list(cache.keys())[:excess]
        for k in oldest_keys:
            del cache[k]
        logger.info(f"🧹 Evicted {len(expired_keys)} expired and {len(oldest_keys)} oldest entries from _dead_symbols_cache.")

def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    """
    Fetches real-time Last Traded Price (CMP) for a list of standard NSE symbols.
    Routes through UnifiedFetcher for provider enforcement and telemetry.
    """
    if not symbols:
        return {}

    now = time.time()
    cache = _get_dead_symbols()
    valid_symbols = []
    prices = {}
    
    with _live_prices_lock:
        # Check recent fast-cache first
        for s in symbols:
            if s in _recent_quotes_cache and (now - _recent_quotes_cache[s]["ts"]) < _RECENT_TTL:
                prices[s] = _recent_quotes_cache[s]["price"]
            else:
                if s not in cache or (now - cache.get(s, 0)) >= _DEAD_TTL:
                    valid_symbols.append(s)

    if not valid_symbols:
        return prices

    # Delegate complex fallback, mapping, and chunking to UnifiedFetcher
    results = fetcher.fetch_live_quotes(valid_symbols, consumer="live_prices")
    
    new_prices = {}
    for sym, quote in results.items():
        if "v" in quote and "cmd" in quote["v"]:
            try:
                val = float(quote["v"]["cmd"]["c"])
                new_prices[sym] = val
                prices[sym] = val
            except (ValueError, TypeError):
                pass
                
    with _live_prices_lock:
        for sym, val in new_prices.items():
            _recent_quotes_cache[sym] = {"price": val, "ts": now}
            
        # Clean up stale entries to prevent memory leak
        stale_keys = [k for k, v in _recent_quotes_cache.items() if (now - v["ts"]) > _RECENT_TTL * 2]
        for k in stale_keys:
            del _recent_quotes_cache[k]
                
    # Evaluate completely dead symbols (not returned by any provider)
    missing = set(valid_symbols) - set(new_prices.keys())
    if missing:
        _cleanup_dead_symbols_cache()
        for s in missing:
            cache[s] = time.time()
            logger.warning(f"🚫 Marking {s} as completely DEAD for 24h (failed across all configured providers).")

    return prices
