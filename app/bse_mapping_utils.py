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
# The _bse_mappings_cache and _bse_invalid_cache are documented exceptions 
# to the session dataset rules. They are Process-Lifetime Immutable Lookups:
# - Process lifetime
# - Immutable after load (or atomically replaced on refresh)
# - Excluded from session rotation
# - Not governed by LifecycleManager
# - Not part of business datasets
# ==============================================================================
_bse_mappings_cache = None
_bse_invalid_cache = None
_last_fetch_time = 0.0
_CACHE_TTL = 300  # Reload from DB every 5 mins

def load_bse_mappings() -> dict[str, str]:
    global _bse_mappings_cache, _bse_invalid_cache, _last_fetch_time
    now = time.time()
    
    if _bse_mappings_cache is not None and (now - _last_fetch_time) < _CACHE_TTL:
        return _bse_mappings_cache

    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Load ACTIVE mappings
                cur.execute("SELECT original_sym, mapped_sym FROM symbol_mappings WHERE mapping_type = 'BSE' AND (mapping_state = 'ACTIVE' OR (mapping_state IS NULL AND is_invalid = FALSE))")
                rows = cur.fetchall()
                _bse_mappings_cache = {row[0]: row[1] for row in rows}
                
                # Load INVALID mappings (only those whose retry_after is still in the future)
                current_time = datetime.now(IST).isoformat()
                cur.execute("""
                    SELECT original_sym FROM symbol_mappings 
                    WHERE mapping_type = 'BSE' 
                    AND (mapping_state = 'INVALID' OR is_invalid = TRUE)
                    AND (retry_after IS NULL OR retry_after > %s)
                """, (current_time,))
                inv_rows = cur.fetchall()
                _bse_invalid_cache = {row[0] for row in inv_rows}
                
        _last_fetch_time = now
    except Exception as e:
        logger.warning(f"Failed to load BSE symbol mappings from DB: {e}")
        if _bse_mappings_cache is None:
            _bse_mappings_cache = {}
        if _bse_invalid_cache is None:
            _bse_invalid_cache = set()
            
    return _bse_mappings_cache

def load_bse_invalid() -> set[str]:
    load_bse_mappings() # ensures both caches are loaded
    return _bse_invalid_cache if _bse_invalid_cache is not None else set()

def is_bse_invalid(original_sym: str) -> bool:
    orig_clean = original_sym.strip().upper()
    if orig_clean.endswith(".NS") or orig_clean.endswith(".BO"):
        orig_clean = orig_clean[:-3]
    return orig_clean in load_bse_invalid()

def save_bse_mapping(original_sym: str, mapped_sym: str) -> None:
    mappings = load_bse_mappings()
    invalid_set = load_bse_invalid()
    orig_clean = original_sym.strip().upper()
    if orig_clean.endswith(".NS") or orig_clean.endswith(".BO"):
        orig_clean = orig_clean[:-3]
        
    mapped_clean = mapped_sym.strip().upper()
    
    if mappings.get(orig_clean) == mapped_clean:
        return
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapped_sym, mapping_state, failure_count, last_verified)
                    VALUES ('BSE', %s, %s, 'ACTIVE', 0, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapped_sym = EXCLUDED.mapped_sym, mapping_state = 'ACTIVE', failure_count = 0, last_verified = EXCLUDED.last_verified, is_invalid = FALSE
                ''', (orig_clean, mapped_clean, datetime.now(IST).isoformat()))
            conn.commit()
            
        mappings[orig_clean] = mapped_clean
        if orig_clean in invalid_set:
            invalid_set.remove(orig_clean)
        logger.info(f"💾 Persistent DB fallback mapping saved: {orig_clean} -> {mapped_clean}")
    except Exception as e:
        logger.warning(f"Failed to save BSE symbol mapping to DB: {e}")

def mark_bse_invalid(original_sym: str) -> None:
    global _bse_mappings_cache, _bse_invalid_cache
    orig_clean = original_sym.strip().upper()
    if orig_clean.endswith(".NS") or orig_clean.endswith(".BO"):
        orig_clean = orig_clean[:-3]
        
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get current failure count
                cur.execute("SELECT failure_count FROM symbol_mappings WHERE mapping_type = 'BSE' AND original_sym = %s", (orig_clean,))
                row = cur.fetchone()
                failures = (row[0] if row else 0) + 1
                
                # Exponential backoff: 7 days, 30 days, 90 days, 365 days
                if failures == 1: days = 7
                elif failures == 2: days = 30
                elif failures == 3: days = 90
                else: days = 365
                
                retry_after = (datetime.now(IST) + timedelta(days=days)).isoformat()
                now_str = datetime.now(IST).isoformat()
                
                cur.execute('''
                    INSERT INTO symbol_mappings (mapping_type, original_sym, mapping_state, failure_count, retry_after, last_verified)
                    VALUES ('BSE', %s, 'INVALID', %s, %s, %s)
                    ON CONFLICT (mapping_type, original_sym) 
                    DO UPDATE SET mapping_state = 'INVALID', failure_count = EXCLUDED.failure_count, retry_after = EXCLUDED.retry_after, last_verified = EXCLUDED.last_verified, is_invalid = TRUE
                ''', (orig_clean, failures, retry_after, now_str))
            conn.commit()
            
        if _bse_mappings_cache is not None and orig_clean in _bse_mappings_cache:
            del _bse_mappings_cache[orig_clean]
        if _bse_invalid_cache is not None:
            _bse_invalid_cache.add(orig_clean)
            
        logger.warning(f"🚫 Marked BSE mapping as INVALID for {orig_clean} (attempt {failures}). System will ignore BSE route for {days} days.")
    except Exception as e:
        logger.warning(f"Failed to mark BSE mapping invalid for {orig_clean}: {e}")

# Maintain backward compatibility temporarily
def invalidate_bse_mapping(original_sym: str) -> None:
    mark_bse_invalid(original_sym)
