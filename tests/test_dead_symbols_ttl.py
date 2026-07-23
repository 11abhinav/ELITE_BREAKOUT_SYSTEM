import time
import pytest
from app.live_prices import _cleanup_dead_symbols_cache, _DEAD_TTL, _MAX_DEAD_CACHE_SIZE, _get_dead_symbols

def test_dead_symbols_cache_ttl_and_eviction():
    """
    Verifies that the migration of _dead_symbols_cache to the Session architecture
    preserves the exact TTL and eviction semantics required by ARCHITECTURE_FREEZE.md §2.
    """
    cache = _get_dead_symbols()
    cache.clear()
    now = time.time()
    
    # 1. Test TTL Expiry
    cache['TCS'] = now - (_DEAD_TTL + 10)  # Expired
    cache['INFY'] = now - (_DEAD_TTL - 10) # Valid
    
    _cleanup_dead_symbols_cache()
    
    assert 'TCS' not in cache, "Expired symbol should be evicted."
    assert 'INFY' in cache, "Valid symbol should remain in cache."
    
    # 2. Test Max Capacity Eviction (Insertion Order Preservation)
    cache.clear()
    
    # Insert exactly MAX_DEAD_CACHE_SIZE elements
    for i in range(_MAX_DEAD_CACHE_SIZE):
        cache[f"SYM_{i}"] = now
        
    assert len(cache) == _MAX_DEAD_CACHE_SIZE
    
    # Insert one more to trigger eviction
    cache["SYM_NEW"] = now
    
    _cleanup_dead_symbols_cache()
    
    assert len(cache) == _MAX_DEAD_CACHE_SIZE
    assert "SYM_0" not in cache, "The oldest element (SYM_0) should have been evicted."
    assert "SYM_NEW" in cache, "The newest element should remain in the cache."
