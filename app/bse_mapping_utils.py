import os
import json
import logging
import time

logger = logging.getLogger(__name__)

# Fallback in-memory cache
_bse_mappings_cache = None
_last_fetch_time = 0.0
_CACHE_TTL = 300  # Reload from DB every 5 mins

def load_bse_mappings() -> dict[str, str]:
    global _bse_mappings_cache, _last_fetch_time
    now = time.time()
    
    if _bse_mappings_cache is not None and (now - _last_fetch_time) < _CACHE_TTL:
        return _bse_mappings_cache

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT original_sym, mapped_sym FROM symbol_mappings WHERE mapping_type = 'BSE' AND is_invalid = FALSE")
                rows = cur.fetchall()
                _bse_mappings_cache = {row[0]: row[1] for row in rows}
        _last_fetch_time = now
    except Exception as e:
        logger.warning(f"Failed to load BSE symbol mappings from DB: {e}")
        if _bse_mappings_cache is None:
            _bse_mappings_cache = {}
            
    return _bse_mappings_cache

def save_bse_mapping(original_sym: str, mapped_sym: str) -> None:
    mappings = load_bse_mappings()
    orig_clean = original_sym.strip().upper()
    mapped_clean = mapped_sym.strip().upper()
    
    if mappings.get(orig_clean) == mapped_clean:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapped_sym)
                    VALUES ('BSE', %s, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapped_sym = EXCLUDED.mapped_sym, is_invalid = FALSE
                ''', (orig_clean, mapped_clean))
            conn.commit()
            
        mappings[orig_clean] = mapped_clean
        logger.info(f"💾 Persistent DB fallback mapping saved: {orig_clean} -> {mapped_clean}")
    except Exception as e:
        logger.warning(f"Failed to save BSE symbol mapping to DB: {e}")

# [VERSION: POISONED_MAPPING_FIX_v1.0] Invalidate a poisoned BSE mapping
def invalidate_bse_mapping(original_sym: str) -> None:
    global _bse_mappings_cache
    orig_clean = original_sym.strip().upper()
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE symbol_mappings 
                    SET is_invalid = TRUE 
                    WHERE mapping_type = 'BSE' AND original_sym = %s
                ''', (orig_clean,))
            conn.commit()
            
        if _bse_mappings_cache is not None and orig_clean in _bse_mappings_cache:
            del _bse_mappings_cache[orig_clean]
        logger.info(f"🗑️ Poisoned BSE mapping invalidated for: {orig_clean}")
    except Exception as e:
        logger.warning(f"Failed to invalidate BSE mapping for {orig_clean}: {e}")
