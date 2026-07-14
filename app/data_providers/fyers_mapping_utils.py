import os
import json
import logging
import time

logger = logging.getLogger(__name__)

# Fallback caches
_fyers_mappings_cache = None
_fyers_invalid_cache = None

_last_mappings_fetch_time = 0.0
_last_invalid_fetch_time = 0.0
_CACHE_TTL = 300  # 5 minutes

def load_fyers_mappings():
    global _fyers_mappings_cache, _last_mappings_fetch_time
    now = time.time()
    
    if _fyers_mappings_cache is not None and (now - _last_mappings_fetch_time) < _CACHE_TTL:
        return _fyers_mappings_cache

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT original_sym, mapped_sym FROM symbol_mappings WHERE mapping_type = 'FYERS' AND is_invalid = FALSE")
                rows = cur.fetchall()
                _fyers_mappings_cache = {row[0]: row[1] for row in rows}
        _last_mappings_fetch_time = now
    except Exception as e:
        logger.warning(f"Failed to load Fyers symbol mappings from DB: {e}")
        if _fyers_mappings_cache is None:
            _fyers_mappings_cache = {}
            
    return _fyers_mappings_cache

def load_fyers_invalid():
    global _fyers_invalid_cache, _last_invalid_fetch_time
    now = time.time()
    
    if _fyers_invalid_cache is not None and (now - _last_invalid_fetch_time) < _CACHE_TTL:
        return _fyers_invalid_cache

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT original_sym FROM symbol_mappings WHERE mapping_type = 'FYERS' AND is_invalid = TRUE")
                rows = cur.fetchall()
                _fyers_invalid_cache = {row[0] for row in rows}
        _last_invalid_fetch_time = now
    except Exception as e:
        logger.warning(f"Failed to load Fyers invalid symbols from DB: {e}")
        if _fyers_invalid_cache is None:
            _fyers_invalid_cache = set()
            
    return _fyers_invalid_cache

def mark_fyers_invalid(symbol: str):
    invalid = load_fyers_invalid()
    if symbol in invalid:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, is_invalid)
                    VALUES ('FYERS', %s, TRUE)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET is_invalid = TRUE
                ''', (symbol,))
            conn.commit()
            
        invalid.add(symbol)
        logger.info(f"💾 Persistent Fyers invalid symbol saved: {symbol}")
    except Exception as e:
        logger.warning(f"Failed to save Fyers invalid symbol to DB: {e}")

def is_fyers_invalid(symbol: str) -> bool:
    return symbol in load_fyers_invalid()

def save_fyers_mapping(original_sym: str, mapped_sym: str):
    mappings = load_fyers_mappings()
    if mappings.get(original_sym) == mapped_sym:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapped_sym)
                    VALUES ('FYERS', %s, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapped_sym = EXCLUDED.mapped_sym, is_invalid = FALSE
                ''', (original_sym, mapped_sym))
            conn.commit()
            
        mappings[original_sym] = mapped_sym
        logger.info(f"💾 Persistent Fyers fallback mapping saved: {original_sym} -> {mapped_sym}")
    except Exception as e:
        logger.warning(f"Failed to save Fyers symbol mapping to DB: {e}")

def remove_fyers_mapping(original_sym: str):
    mappings = load_fyers_mappings()
    if original_sym not in mappings:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM symbol_mappings WHERE mapping_type = 'FYERS' AND original_sym = %s", (original_sym,))
            conn.commit()
            
        del mappings[original_sym]
        logger.info(f"🗑️ Removed Fyers mapping for: {original_sym}")
    except Exception as e:
        logger.warning(f"Failed to remove Fyers symbol mapping from DB: {e}")
