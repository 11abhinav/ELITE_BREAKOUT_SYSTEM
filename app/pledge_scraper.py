import os
import requests
import logging
from bs4 import BeautifulSoup
import re
import random
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
from tenacity import retry, stop_after_attempt, wait_exponential
from database import get_connection, init_db
from data_fetch_status import mark_success, mark_failure
from config import DATA_DIR

logger = logging.getLogger(__name__)

import threading
_cache_lock = threading.Lock()



from datetime import timedelta

_blacklisted_proxy_keys_ram = {}
_blacklisted_keys_initialized = False
_active_scraper_key_ram = None
_active_crawlora_key_ram = None

def _get_exhausted_keys_file():
    return os.path.join(DATA_DIR, "exhausted_keys.json")

def _init_proxy_key_state():
    """Restores 7-day blacklisted proxy keys & sticky active keys from PostgreSQL DB and local disk on boot."""
    global _blacklisted_proxy_keys_ram, _blacklisted_keys_initialized, _active_scraper_key_ram, _active_crawlora_key_ram
    if _blacklisted_keys_initialized:
        return
    with _cache_lock:
        if _blacklisted_keys_initialized:
            return
        restored_data = {}
        # 1. Load blacklisted keys from local disk
        try:
            fpath = _get_exhausted_keys_file()
            if os.path.exists(fpath):
                with open(fpath, 'r') as f:
                    restored_data = json.load(f)
        except Exception as e:
            logger.debug(f"Failed loading exhausted keys from file: {e}")

        # 2. Load blacklisted keys & sticky active working keys from PostgreSQL DB system_state
        try:
            from database import get_system_state
            db_json_str = get_system_state("exhausted_proxy_keys_v1")
            if db_json_str:
                db_data = json.loads(db_json_str)
                if isinstance(db_data, dict):
                    for k, v in db_data.items():
                        if k not in restored_data:
                            restored_data[k] = v
                        elif isinstance(v, dict) and isinstance(restored_data.get(k), dict):
                            if v.get("expires_at", "") > restored_data[k].get("expires_at", ""):
                                restored_data[k] = v

            # Load active sticky keys from DB
            _active_scraper_key_ram = get_system_state("active_scraper_key_v1") or None
            _active_crawlora_key_ram = get_system_state("active_crawlora_key_v1") or None
        except Exception as db_err:
            logger.debug(f"Failed loading proxy state from PostgreSQL DB: {db_err}")

        # Filter out expired keys (> 7 days old)
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        now_iso = now_dt.isoformat()
        now_date = now_dt.strftime("%Y-%m-%d")
        valid_data = {}
        for k, v in restored_data.items():
            if isinstance(v, dict):
                exp = v.get("expires_at", "")
                if exp and exp > now_iso:
                    valid_data[k] = v
            elif isinstance(v, str):
                # Legacy YYYY-MM-DD format migration: promote to 7-day expiry timestamp
                if v >= now_date:
                    valid_data[k] = {
                        "exhausted_at": now_iso,
                        "expires_at": (now_dt + timedelta(days=7)).isoformat(),
                        "legacy_date": v
                    }

        _blacklisted_proxy_keys_ram = valid_data
        _blacklisted_keys_initialized = True
        if valid_data:
            logger.info(f"🛡️ [PROXY STATE RESTORED] Restored {len(valid_data)} 7-day blacklisted proxy key(s) from PostgreSQL DB system_state!")

def _is_key_exhausted_today(key: str) -> bool:
    """Checks if a ScraperAPI or Crawlora proxy key is currently blacklisted (7-day TTL)."""
    if not key:
        return True
    try:
        _init_proxy_key_state()
        now_iso = datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()
        with _cache_lock:
            entry = _blacklisted_proxy_keys_ram.get(key)
            if not entry:
                return False
            if isinstance(entry, dict):
                expires_at = entry.get("expires_at", "")
                if expires_at and now_iso < expires_at:
                    return True
                else:
                    _blacklisted_proxy_keys_ram.pop(key, None)
                    return False
            elif isinstance(entry, str):
                today = datetime.now(ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d")
                return entry == today
        return False
    except Exception:
        return False

def mark_key_exhausted_today(key: str, reason: str = "Exhausted / No credits left (7-day blacklist)"):
    """Blacklists a ScraperAPI or Crawlora proxy key for 7 DAYS persistently in PostgreSQL DB & local disk."""
    if not key:
        return
    try:
        global _active_scraper_key_ram, _active_crawlora_key_ram
        _init_proxy_key_state()
        now_dt = datetime.now(ZoneInfo('Asia/Kolkata'))
        expires_dt = now_dt + timedelta(days=7)
        now_iso = now_dt.isoformat()
        expires_iso = expires_dt.isoformat()

        entry = {
            "key": key,
            "exhausted_at": now_iso,
            "expires_at": expires_iso,
            "reason": reason
        }

        masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else key
        logger.warning(f"🚫 [7-DAY PROXY BLACKLIST] Key [{masked_key}] marked EXHAUSTED & BLACKLISTED for 7 days (until {expires_dt.strftime('%Y-%m-%d %H:%M IST')}) persistently in PostgreSQL DB & disk.")

        with _cache_lock:
            _blacklisted_proxy_keys_ram[key] = entry
            if key == _active_scraper_key_ram:
                _active_scraper_key_ram = None
            if key == f"crawlora_{_active_crawlora_key_ram}" or key == _active_crawlora_key_ram:
                _active_crawlora_key_ram = None
            ram_copy = dict(_blacklisted_proxy_keys_ram)

        # 1. Save local disk cache
        try:
            fpath = _get_exhausted_keys_file()
            with open(fpath, 'w') as f:
                json.dump(ram_copy, f, indent=2)
        except Exception as f_err:
            logger.debug(f"Failed to write exhausted keys to disk: {f_err}")

        # 2. Save persistently to PostgreSQL DB system_state
        try:
            from database import save_system_state
            save_system_state("exhausted_proxy_keys_v1", json.dumps(ram_copy, indent=2))
            if _active_scraper_key_ram is None:
                save_system_state("active_scraper_key_v1", "")
            if _active_crawlora_key_ram is None:
                save_system_state("active_crawlora_key_v1", "")
            logger.info(f"⚡ [POSTGRES DB BACKUP] 7-Day proxy blacklist saved to PostgreSQL system_state for key [{masked_key}].")
        except Exception as db_err:
            logger.warning(f"⚠️ Failed to save 7-day proxy blacklist to PostgreSQL DB: {db_err}")

    except Exception as e:
        logger.error(f"Failed to mark proxy key exhausted for {key}: {e}")

def set_active_scraper_key(key: str):
    """Sets the confirmed active ScraperAPI key and persists it to DB so it is reused until exhausted."""
    global _active_scraper_key_ram
    if not key or _is_key_exhausted_today(key):
        return
    with _cache_lock:
        if _active_scraper_key_ram != key:
            _active_scraper_key_ram = key
            try:
                from database import save_system_state
                save_system_state("active_scraper_key_v1", key)
            except Exception:
                pass

def set_active_crawlora_key(key: str):
    """Sets the confirmed active Crawlora key and persists it to DB so it is reused until exhausted."""
    global _active_crawlora_key_ram
    if not key or _is_key_exhausted_today(f"crawlora_{key}"):
        return
    with _cache_lock:
        if _active_crawlora_key_ram != key:
            _active_crawlora_key_ram = key
            try:
                from database import save_system_state
                save_system_state("active_crawlora_key_v1", key)
            except Exception:
                pass

def get_scraper_api_key() -> str:
    """Parse comma-separated SCRAPERAPI_KEY env var and return the active key (used sticky until exhausted)."""
    _init_proxy_key_state()
    keys_str = os.getenv("SCRAPERAPI_KEY", "")
    if not keys_str:
        return ""
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    
    # 1. Use sticky active working key first if valid & not blacklisted
    global _active_scraper_key_ram
    if _active_scraper_key_ram and _active_scraper_key_ram in keys and not _is_key_exhausted_today(_active_scraper_key_ram):
        return _active_scraper_key_ram

    # 2. Find next non-exhausted key
    for k in keys:
        if not _is_key_exhausted_today(k):
            set_active_scraper_key(k)
            return k
            
    return ""

def get_crawlora_api_key() -> str:
    """Parse comma-separated CRAWLORA_API_KEY env var and return the active key (used sticky until exhausted)."""
    _init_proxy_key_state()
    keys_str = os.getenv("CRAWLORA_API_KEY", "")
    if not keys_str:
        logger.warning("⚠️ [CRAWLORA] CRAWLORA_API_KEY environment variable is EMPTY or NOT SET!")
        return ""
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]

    # 1. Use sticky active working key first if valid & not blacklisted
    global _active_crawlora_key_ram
    if _active_crawlora_key_ram and _active_crawlora_key_ram in keys and not _is_key_exhausted_today(f"crawlora_{_active_crawlora_key_ram}"):
        masked_key = f"{_active_crawlora_key_ram[:4]}...{_active_crawlora_key_ram[-4:]}" if len(_active_crawlora_key_ram) > 8 else "CONFIG_KEY"
        logger.debug(f"🔑 [CRAWLORA STICKY ACTIVE KEY] Reusing active working key [{masked_key}]")
        return _active_crawlora_key_ram

    # 2. Find next non-exhausted key
    for k in keys:
        if not _is_key_exhausted_today(f"crawlora_{k}"):
            masked_key = f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "CONFIG_KEY"
            logger.info(f"🔑 [CRAWLORA] Selected active API key [{masked_key}] (out of {len(keys)} total key(s))")
            set_active_crawlora_key(k)
            return k
            
    logger.warning("⚠️ [CRAWLORA] All provided Crawlora API keys are marked EXHAUSTED for the next 7 days!")
    return ""

def mark_crawlora_key_exhausted_today(key: str):
    mark_key_exhausted_today(f"crawlora_{key}")

def _get_fail_file():
    return os.path.join(DATA_DIR, "pledge_failures.json")

def _is_failed_today(symbol: str) -> bool:
    try:
        fail_file = _get_fail_file()
        if not os.path.exists(fail_file): return False
        with open(fail_file, 'r') as f:
            data = json.load(f)
        today = datetime.now(ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d")
        return data.get(symbol) == today
    except Exception:
        return False

def _mark_failed_today(symbol: str):
    try:
        fail_file = _get_fail_file()
        with _cache_lock:
            data = {}
            if os.path.exists(fail_file):
                try:
                    with open(fail_file, 'r') as f:
                        data = json.load(f)
                except Exception:
                    pass
            today = datetime.now(ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d")
            data[symbol] = today
            # Clean up old entries to prevent file from growing indefinitely
            data = {k: v for k, v in data.items() if v == today}
            with open(fail_file, 'w') as f:
                json.dump(data, f)
    except Exception as e:
        logger.debug(f"Failed to write pledge failure cache: {e}")

# [VERSION: PLEDGE_NULL_v1.0] Treat missing/failed pledge data as None
@lru_cache(maxsize=5000)
def fetch_promoter_pledge(symbol: str):
    """
    Fetches the promoter pledge percentage for a given NSE symbol.
    Primarily relies on the PostgreSQL cache populated by the pledge_worker.
    Makes ONE quick fallback attempt if cache is missing.
    """
    init_db()

    # 0. Check Daily Negative Cache
    if _is_failed_today(symbol):
        return None

    # 1. Check DB Cache
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pledge_pct, updated_at 
                    FROM promoter_pledge_cache 
                    WHERE symbol = %s 
                      AND (updated_at >= NOW() - INTERVAL '120 days' OR last_attempted_at >= NOW() - INTERVAL '120 days')
                """, (symbol,))
                row = cur.fetchone()
                if row:
                    val = float(row[0])
                    # Treat negative sentinel values (like -1.0 for 404/Not Found) as None
                    return None if val < 0 else val

    except Exception as e:
        logger.warning(f"Database error checking pledge cache for {symbol}: {e}")

    # 2. Return None if not in database
    # We DO NOT fallback to hitting the API synchronously because it halts the scanner.
    # The pledge_worker daemon will pick up the missing stock tonight.
    return None
