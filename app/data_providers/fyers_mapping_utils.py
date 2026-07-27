import os
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from core_enums import MappingState

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ==============================================================================
# INFRASTRUCTURE LOOKUPS
# The _fyers_mappings_cache and _fyers_invalid_cache are documented exceptions 
# to the session dataset rules. They are Process-Lifetime Immutable Lookups:
# - Process lifetime
# - Immutable after load (or atomically replaced on refresh)
# - Excluded from session rotation
# - Not governed by LifecycleManager
# - Not part of business datasets
# ==============================================================================
_fyers_mappings_cache = None
_fyers_invalid_cache = None

_last_mappings_fetch_time = 0.0
_CACHE_TTL = 300  # 5 minutes

def load_fyers_mappings():
    global _fyers_mappings_cache, _fyers_invalid_cache, _last_mappings_fetch_time
    now = time.time()
    
    if _fyers_mappings_cache is not None and (now - _last_mappings_fetch_time) < _CACHE_TTL:
        return _fyers_mappings_cache

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Load ACTIVE mappings
                cur.execute("SELECT original_sym, mapped_sym FROM symbol_mappings WHERE mapping_type = 'FYERS' AND (mapping_state = 'ACTIVE' OR (mapping_state IS NULL AND is_invalid = FALSE))")
                rows = cur.fetchall()
                _fyers_mappings_cache = {row[0]: row[1] for row in rows}
                
                # Clean up legacy poisoned invalid entries for 544467 / NSDL
                try:
                    cur.execute("DELETE FROM symbol_mappings WHERE mapping_type = 'FYERS' AND (original_sym LIKE '%%544467%%' OR original_sym = 'NSDL')")
                    conn.commit()
                except Exception:
                    pass

                # Load INVALID mappings (Active invalid symbols whose retry_after is still in the future)
                current_time = datetime.now(IST).isoformat()
                cur.execute("""
                    SELECT original_sym FROM symbol_mappings 
                    WHERE mapping_type = 'FYERS' 
                    AND (mapping_state = 'INVALID' OR is_invalid = TRUE)
                    AND (retry_after IS NULL OR retry_after > %s)
                """, (current_time,))
                inv_rows = cur.fetchall()
                _fyers_invalid_cache = {row[0] for row in inv_rows}
                
        _last_mappings_fetch_time = now
    except Exception as e:
        logger.warning(f"Failed to load Fyers symbol mappings from DB: {e}")
        if _fyers_mappings_cache is None:
            _fyers_mappings_cache = {}
        if _fyers_invalid_cache is None:
            _fyers_invalid_cache = set()
            
    return _fyers_mappings_cache

def load_fyers_invalid():
    load_fyers_mappings() # ensures both caches are loaded
    return _fyers_invalid_cache if _fyers_invalid_cache is not None else set()

def mark_fyers_invalid(symbol: str):
    invalid = load_fyers_invalid()
    if symbol in invalid:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get current failure count
                cur.execute("SELECT failure_count FROM symbol_mappings WHERE mapping_type = 'FYERS' AND original_sym = %s", (symbol,))
                row = cur.fetchone()
                failures = (row[0] if row else 0) + 1
                
                # Exponential backoff: 1 day (daily retry), 7 days, 30 days, 365 days
                if failures == 1: days = 1
                elif failures == 2: days = 7
                elif failures == 3: days = 30
                else: days = 365
                
                retry_after = (datetime.now(IST) + timedelta(days=days)).isoformat()
                now_str = datetime.now(IST).isoformat()
                
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapping_state, failure_count, retry_after, last_verified)
                    VALUES ('FYERS', %s, 'INVALID', %s, %s, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapping_state = 'INVALID', failure_count = EXCLUDED.failure_count, retry_after = EXCLUDED.retry_after, last_verified = EXCLUDED.last_verified, is_invalid = TRUE
                ''', (symbol, failures, retry_after, now_str))
            conn.commit()
            
        if _fyers_mappings_cache is not None and symbol in _fyers_mappings_cache:
            del _fyers_mappings_cache[symbol]
        if _fyers_invalid_cache is not None:
            _fyers_invalid_cache.add(symbol)
            
        logger.warning(f"🚫 Marked Fyers symbol as INVALID (attempt {failures}). Retrying after {days} days: {symbol}")
    except Exception as e:
        logger.warning(f"Failed to mark Fyers mapping invalid for {symbol}: {e}")

def is_fyers_invalid(symbol: str) -> bool:
    return symbol in load_fyers_invalid()

def save_fyers_mapping(original_sym: str, mapped_sym: str):
    mappings = load_fyers_mappings()
    invalid_set = load_fyers_invalid()
    if mappings.get(original_sym) == mapped_sym:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapped_sym, mapping_state, failure_count, last_verified)
                    VALUES ('FYERS', %s, %s, 'ACTIVE', 0, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapped_sym = EXCLUDED.mapped_sym, mapping_state = 'ACTIVE', failure_count = 0, last_verified = EXCLUDED.last_verified, is_invalid = FALSE
                ''', (original_sym, mapped_sym, datetime.now(IST).isoformat()))
            conn.commit()
            
        mappings[original_sym] = mapped_sym
        if original_sym in invalid_set:
            invalid_set.remove(original_sym)
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

def remove_fyers_invalid(original_sym: str):
    invalid_set = load_fyers_invalid()
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM symbol_mappings WHERE mapping_type = 'FYERS' AND original_sym = %s", (original_sym,))
            conn.commit()
            
        if invalid_set and original_sym in invalid_set:
            invalid_set.discard(original_sym)
        logger.info(f"✨ Unblacklisted Fyers invalid mapping for: {original_sym}")
    except Exception as e:
        logger.warning(f"Failed to unblacklist Fyers symbol {original_sym}: {e}")
