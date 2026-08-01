import os
import requests
import time
import logging
from config import WATCHLIST_PATH

import threading

logger = logging.getLogger(__name__)

from data_registry import registry
import threading

_BLACKLIST_TTL = 30 * 60  # 30 minutes
_blacklist_lock = threading.Lock()

def get_live_blacklist() -> set[str]:
    """
    Returns a set of blacklisted symbols (Promoter Blacklist + ASM + GSM).
    Uses a 30-minute in-memory cache to prevent NSE rate-limiting.
    Note: Since this is an in-memory cache, each worker process will fetch
    its own copy every 30 minutes. This is acceptable given the infrequency.
    """
    
    # Return cache if valid (fast path without lock)
    cached = registry.get("blacklist")
    if cached is not None and (time.time() - cached.get("ts", 0)) < _BLACKLIST_TTL:
        return cached["data"]
        
    with _blacklist_lock:
        # Double-check inside lock
        cached = registry.get("blacklist")
        if cached is not None and (time.time() - cached.get("ts", 0)) < _BLACKLIST_TTL:
            return cached["data"]
        
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
                logger.exception(f"Failed to load promoter blacklist")
    
        # 2. Check Postgres backup first (if under 12 hours old, use it and skip API hit)
        try:
            from database import get_system_state
            import json
            db_payload = get_system_state("surveillance_blacklist")
            if db_payload:
                cache_data = json.loads(db_payload)
                saved_time = cache_data.get("timestamp", 0)
                cached_list = cache_data.get("symbols", [])
                
                file_age = time.time() - saved_time
                if file_age < 12 * 3600: # 12 hours
                    if cached_list:
                        for sym in cached_list:
                            blacklist.add(str(sym).strip().upper())
                        logger.info(f"🛡️ Loaded {len(cached_list)} surveillance symbols from fresh Postgres cache (age: {file_age/3600:.1f}h).")
                        registry.put("blacklist", {"ts": time.time(), "data": blacklist})
                        return blacklist
        except Exception as e:
            logger.warning(f"Failed to load fresh Postgres surveillance cache: {e}")

        # 3. Fetch Live NSE ASM/GSM (Surveillance measures) if cache is old or missing
        try:
            from config import DISABLE_NSE_SURVEILLANCE_FETCH
            if DISABLE_NSE_SURVEILLANCE_FETCH:
                logger.warning("NSE Surveillance fetch is disabled via config. Using existing cache or empty set.")
                return cached["data"] if cached else blacklist
        except ImportError:
            pass

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com"
        }
        
        try:
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
                    api_key = get_scraper_api_key()
                    
                    def fetch_nse_json(url: str):
                        curr_key = get_scraper_api_key()
                        if curr_key:
                            try:
                                payload = {'api_key': curr_key, 'url': url, 'render': 'false', 'country_code': 'in'}
                                resp = requests.get('https://api.scraperapi.com/', params=payload, timeout=30)
                                if resp.status_code in [401, 403, 429]:
                                    reason = resp.text.strip()[:200]
                                    try:
                                        err_dict = resp.json()
                                        if isinstance(err_dict, dict) and "error" in err_dict:
                                            reason = err_dict["error"]
                                    except Exception:
                                        pass
                                    masked_key = f"{curr_key[:4]}...{curr_key[-4:]}" if len(curr_key) > 8 else "CONFIG_KEY"
                                    logger.warning(f"⚠️ ScraperAPI returned HTTP {resp.status_code} for key [{masked_key}]. Reason: {reason}")
                                    mark_key_exhausted_today(curr_key)
                                elif resp.status_code == 200:
                                    return resp.json()
                                else:
                                    logger.warning(f"⚠️ ScraperAPI returned unexpected status {resp.status_code} for {url}: {resp.text[:150]}")
                            except Exception as scraper_err:
                                logger.debug(f"ScraperAPI fetch failed for {url}: {scraper_err}")
                        
                        # Fallback to direct nsepython fetch if ScraperAPI fails or is missing key
                        try:
                            import nsepython
                            return nsepython.nsefetch(url)
                        except Exception as nse_err:
                            raise Exception(f"Failed to fetch {url} via ScraperAPI & nsepython fallback: {nse_err}")

                    asm_res = fetch_nse_json("https://www.nseindia.com/api/reportASM")
                    if isinstance(asm_res, dict):
                        for key in ["longterm", "shortterm"]:
                            if key in asm_res and "data" in asm_res[key]:
                                for item in asm_res[key]["data"]:
                                    if "symbol" in item:
                                        blacklist.add(item["symbol"].strip().upper())
                                        
                    time.sleep(1.0)

                    gsm_res = fetch_nse_json("https://www.nseindia.com/api/reportGSM")
                    if isinstance(gsm_res, dict) and "data" in gsm_res:
                        for item in gsm_res["data"]:
                            if "symbol" in item:
                                blacklist.add(item["symbol"].strip().upper())
                    elif isinstance(gsm_res, list):
                        for item in gsm_res:
                            if isinstance(item, dict) and "symbol" in item:
                                blacklist.add(item["symbol"].strip().upper())
                                    
                    logger.info(f"🛡️ Refreshed NSE Surveillance List. Total Blacklisted: {len(blacklist)}")
                    
                    try:
                        from database import save_system_state
                        import json
                        payload = {
                            "timestamp": time.time(),
                            "symbols": list(blacklist)
                        }
                        save_system_state("surveillance_blacklist", json.dumps(payload))
                        logger.info("💾 Saved refreshed NSE surveillance list to Postgres backup.")
                    except Exception as cache_err:
                        logger.warning(f"Failed to write surveillance Postgres backup: {cache_err}")
                        
                    break
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        backoff = 2
                        logger.warning(f"⚠️ NSE surveillance fetch attempt {attempt+1} failed: {str(e)[:150]}. Retrying in {backoff}s...")
                        time.sleep(backoff)
                    else:
                        logger.warning(f"Failed to fetch live NSE surveillance lists after {max_retries} attempts. NSE might be rate-limiting.")
                        raise 
            
        except Exception as e:
            logger.warning(f"Falling back to stale cache due to NSE fetch failure: {str(e)[:100]}")
            
            if cached is not None:
                return cached["data"]
                
            try:
                from database import get_system_state
                import json
                db_payload = get_system_state("surveillance_blacklist")
                if db_payload:
                    cache_data = json.loads(db_payload)
                    cached_list = cache_data.get("symbols", [])
                    if cached_list:
                        for sym in cached_list:
                            blacklist.add(str(sym).strip().upper())
                        logger.warning(f"⚠️ Restored {len(cached_list)} blacklisted symbols from Postgres system_state (stale).")
                        registry.put("blacklist", {"ts": time.time(), "data": blacklist})
                        return blacklist
            except Exception as cache_err:
                logger.warning(f"Failed to read surveillance Postgres backup: {cache_err}")
                
        # Update cache
        registry.put("blacklist", {"ts": time.time(), "data": blacklist})
        return blacklist

def force_refresh_blacklist() -> set[str]:
    """Force a fresh download, ignoring the TTL."""
    with _blacklist_lock:
        registry.put("blacklist", {"ts": 0, "data": set()})  # Invalidates cache
    return get_live_blacklist()
