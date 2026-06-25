import os
import requests
import time
import logging
from config import WATCHLIST_PATH

import threading

logger = logging.getLogger(__name__)

_blacklist_cache = None
_blacklist_ts = 0
_BLACKLIST_TTL = 30 * 60  # 30 minutes
_blacklist_lock = threading.Lock()

def get_live_blacklist() -> set[str]:
    """
    Returns a set of blacklisted symbols (Promoter Blacklist + ASM + GSM).
    Uses a 30-minute in-memory cache to prevent NSE rate-limiting.
    Note: Since this is an in-memory cache, each worker process will fetch
    its own copy every 30 minutes. This is acceptable given the infrequency.
    """
    global _blacklist_cache, _blacklist_ts, _blacklist_lock
    
    # Return cache if valid (fast path without lock)
    if _blacklist_cache is not None and (time.monotonic() - _blacklist_ts) < _BLACKLIST_TTL:
        return _blacklist_cache
        
    with _blacklist_lock:
        # Double-check inside lock
        if _blacklist_cache is not None and (time.monotonic() - _blacklist_ts) < _BLACKLIST_TTL:
            return _blacklist_cache
        
    blacklist = set()
    
    # 1. Load Hardcoded Promoter CSV
    csv_path = os.path.join(os.path.dirname(WATCHLIST_PATH), "promoter_blacklist.csv")
    if os.path.exists(csv_path):
        try:
            import pandas as pd
            df_csv = pd.read_csv(csv_path)
            for sym in df_csv["symbol"].dropna():
                blacklist.add(str(sym).strip().upper())
            logger.info(f"🛡️ Loaded {len(df_csv)} blacklisted promoters from CSV.")
        except Exception as e:
            logger.error(f"Failed to load promoter blacklist: {e}")

    # 2. Fetch Live NSE ASM/GSM (Surveillance measures)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com"
    }
    
    try:
        # Establish session first to get cookies
        try:
            from curl_cffi import requests as cffi_requests
            session = cffi_requests.Session(impersonate="chrome110")
        except ImportError:
            session = requests.Session()
            
        # Try fetching with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                session.get("https://www.nseindia.com", headers=headers, timeout=15)
                
                # Fetch ASM (Additional Surveillance Measure)
                asm_res = session.get("https://www.nseindia.com/api/reportASM", headers=headers, timeout=15)
                if asm_res.status_code == 200:
                    data = asm_res.json()
                    for key in ["longterm", "shortterm"]:
                        if key in data and "data" in data[key]:
                            for item in data[key]["data"]:
                                if "symbol" in item:
                                    blacklist.add(item["symbol"].strip().upper())
                                    
                # Fetch GSM (Graded Surveillance Measure - usually shells / bankruptcy)
                gsm_res = session.get("https://www.nseindia.com/api/reportGSM", headers=headers, timeout=15)
                if gsm_res.status_code == 200:
                    data = gsm_res.json()
                    if isinstance(data, list):
                        # Sometimes it's a list, sometimes a dict. Handle safely.
                        for item in data:
                            if isinstance(item, dict) and "symbol" in item:
                                blacklist.add(item["symbol"].strip().upper())
                    elif isinstance(data, dict) and "data" in data:
                        for item in data["data"]:
                            if "symbol" in item:
                                blacklist.add(item["symbol"].strip().upper())
                                
                logger.info(f"🛡️ Refreshed NSE Surveillance List. Total Blacklisted: {len(blacklist)}")
                break # Success, exit retry loop
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"NSE surveillance fetch attempt {attempt+1} failed: {e}. Retrying in 2s...")
                    time.sleep(2)
                else:
                    raise # Re-raise on final failure to hit outer exception handler
        
    except Exception as e:
        logger.error(f"Failed to fetch live NSE surveillance lists: {e}")
        # On failure, if we have a stale cache, keep using it rather than returning empty
        if _blacklist_cache is not None:
            logger.warning("Using stale surveillance cache due to fetch failure.")
            return _blacklist_cache
            
    # Update cache
    _blacklist_cache = blacklist
    _blacklist_ts = time.monotonic()
    return _blacklist_cache

def force_refresh_blacklist() -> set[str]:
    """Force a fresh download, ignoring the TTL."""
    global _blacklist_ts, _blacklist_lock
    with _blacklist_lock:
        _blacklist_ts = 0  # Invalidates cache
    return get_live_blacklist()
