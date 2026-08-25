import io
import json
import os
import time
import logging
import threading
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime, date
from zoneinfo import ZoneInfo

logger = logging.getLogger("constituent_service")

# Target URLs for NSE Archives
CONSTITUENT_URLS = {
    "Nifty 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "Nifty Next 50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
    "Nifty Midcap 150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "Nifty Smallcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "Nifty 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "Nifty Microcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
}

# Browser-like headers to bypass NSE's strict user-agent checking
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

def get_nse_session():
    """Returns a curl_cffi Session to bypass NSE WAF, with fallback to requests."""
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="chrome")
        session.headers.update(HTTP_HEADERS)
        return session
    except ImportError:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(HTTP_HEADERS)
        return session

class ConstituentService:
    _cache_lock = threading.Lock()
    _cached_symbols = None
    _cache_trading_date = None
    
    # Statistics
    hits = 0
    misses = 0
    last_refresh = None
    last_download_ms = 0.0
    symbol_count = 0

    @classmethod
    def fetch_constituents(cls) -> list:
        """Download index lists from NSE and return unique, normalized symbol list with thread-safe caching."""
        current_market_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        # 1. Fast path without lock
        if cls._cached_symbols is not None and cls._cache_trading_date == current_market_date:
            cls.hits += 1
            logger.debug(f"✅ Using cached constituents ({cls.symbol_count} symbols) [Hits: {cls.hits}]")
            return list(cls._cached_symbols) # Return a shallow copy of the list

        # 2. Slow path with lock
        with cls._cache_lock:
            # Double-check inside lock
            if cls._cached_symbols is not None and cls._cache_trading_date == current_market_date:
                cls.hits += 1
                logger.debug(f"✅ Using cached constituents ({cls.symbol_count} symbols) [Hits: {cls.hits}]")
                return list(cls._cached_symbols)
            
            cls.misses += 1
            start_t = time.time()
            
            symbols = set()
            session = get_nse_session()
            
            success_count = 0
            
            for name, url in CONSTITUENT_URLS.items():
                try:
                    logger.info(f"📥 Downloading {name} constituents...")
                    
                    response = None
                    max_retries = 2
                    for attempt in range(max_retries):
                        # Attempt 1: Direct Session fetch (PRIMARY - fast 100ms with browser headers)
                        try:
                            res = session.get(url, timeout=10)
                            if res is not None and res.status_code == 200 and len(res.content) > 100:
                                response = res
                                logger.info(f"⚡ [NSE DIRECT SUCCESS] Downloaded {name} constituents ({len(res.content)} bytes)")
                                break
                        except Exception as direct_err:
                            logger.warning(f"⚠️ [NSE DIRECT FETCH FAIL] {name}: {direct_err}")

                        # Attempt 2: Crawlora Proxy (SECONDARY BACKUP)
                        try:
                            from pledge_scraper import get_crawlora_api_key, mark_crawlora_key_exhausted_today
                            crawlora_key = get_crawlora_api_key()
                            if crawlora_key:
                                masked_ckey = f"{crawlora_key[:4]}...{crawlora_key[-4:]}" if len(crawlora_key) > 8 else "CRAWLORA"
                                logger.info(f"🌐 [CRAWLORA BACKUP] Fetching {name} constituents (Key: [{masked_ckey}]): {url}")
                                c_resp = requests.get('https://api.crawlora.net/v1/scrape', params={'api_key': crawlora_key, 'url': url}, timeout=15)
                                if c_resp is not None and c_resp.status_code == 200 and len(c_resp.content) > 100:
                                    response = c_resp
                                    logger.info(f"✅ [CRAWLORA SUCCESS] Downloaded {name} constituents ({len(c_resp.content)} bytes)")
                                    break
                                elif c_resp and c_resp.status_code in (401, 429):
                                    mark_crawlora_key_exhausted_today(crawlora_key)
                                else:
                                    status_str = c_resp.status_code if c_resp else 'No Response'
                                    logger.warning(f"⚠️ [CRAWLORA FAIL] HTTP {status_str} for {name}")
                        except Exception as crawlora_err:
                            logger.warning(f"⚠️ [CRAWLORA ERROR] Crawlora fallback for {name}: {crawlora_err}")

                        # Attempt 3: ScraperAPI Proxy (TERTIARY BACKUP)
                        try:
                            from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
                            scraper_key = get_scraper_api_key()
                            if scraper_key:
                                logger.info(f"🌐 [SCRAPERAPI BACKUP] Fetching {name} constituents via ScraperAPI...")
                                s_resp = requests.get("http://api.scraperapi.com", params={"api_key": scraper_key, "url": url}, timeout=20)
                                if s_resp and s_resp.status_code in (401, 403, 429):
                                    mark_key_exhausted_today(scraper_key)
                                elif s_resp and s_resp.status_code == 200 and len(s_resp.content) > 100:
                                    response = s_resp
                                    logger.info(f"✅ [SCRAPERAPI SUCCESS] Downloaded {name} constituents ({len(s_resp.content)} bytes)")
                                    break
                        except Exception as scraper_err:
                            logger.warning(f"⚠️ [SCRAPERAPI ERROR] ScraperAPI fallback for {name}: {scraper_err}")

                        if attempt < max_retries - 1:
                            time.sleep(2)

                    if response and response.status_code == 200:
                        df = pd.read_csv(io.StringIO(response.text))
                        if "Symbol" in df.columns:
                            for sym in df["Symbol"].dropna().unique():
                                clean_sym = str(sym).strip()
                                if clean_sym:
                                    symbols.add(clean_sym)
                            logger.info(f"✅ Loaded {len(df)} constituents for {name}.")
                            success_count += 1
                        else:
                            logger.warning(f"⚠️ CSV parsed for {name} missing 'Symbol' column.")
                    else:
                        logger.warning(f"⚠️ Could not download {name} constituents from live sources. Continuing with remaining indices...")
                except Exception as e:
                    logger.warning(f"⚠️ Gracefully handled exception for {name}: {e}")
                
                time.sleep(1.0)
            
            if success_count == 0:
                logger.error("❌ Failed to download ANY constituents from NSE.")
                if cls._cached_symbols is not None:
                    logger.warning("⚠️ Retaining previous constituent RAM cache due to complete network failure.")
                    return list(cls._cached_symbols)
                
                # Attempt PostgreSQL DB restore first
                try:
                    from database import get_system_state
                    db_state_str = get_system_state("constituent_cache")
                    if db_state_str:
                        _dc = json.loads(db_state_str)
                        _dc_symbols = _dc.get("symbols", [])
                        if _dc_symbols:
                            logger.info(f"⚡ [CONSTITUENT DB CACHE HIT] Restored {len(_dc_symbols)} symbols from PostgreSQL DB system_state!")
                            cls._cached_symbols = _dc_symbols
                            cls._cache_trading_date = current_market_date
                            return list(_dc_symbols)
                except Exception as _db_err:
                    logger.warning(f"⚠️ [CONSTITUENT DB CACHE] DB restore fallback failed: {_db_err}")

                # RAM & DB cache empty. Attempt to load from the on-disk JSON cache.
                try:
                    from config import CONSTITUENT_CACHE_PATH, CONSTITUENT_DISK_CACHE_MAX_DAYS
                    if os.path.exists(CONSTITUENT_CACHE_PATH):
                        with open(CONSTITUENT_CACHE_PATH, 'r') as _dcf:
                            _dc = json.load(_dcf)
                        _dc_date = datetime.strptime(_dc.get("cached_at", "2000-01-01"), "%Y-%m-%d").date()
                        _dc_age_days = (date.today() - _dc_date).days
                        _dc_symbols = _dc.get("symbols", [])
                        if _dc_symbols and _dc_age_days <= CONSTITUENT_DISK_CACHE_MAX_DAYS:
                            logger.warning(
                                f"⚠️ [CONSTITUENT DISK CACHE] Loaded {len(_dc_symbols)} symbols from disk "
                                f"(cached {_dc_age_days}d ago on {_dc_date}). "
                                "Using as last-resort fallback — NSE live fetch failed."
                            )
                            return list(_dc_symbols)
                        elif _dc_symbols:
                            logger.error(
                                f"❌ [CONSTITUENT DISK CACHE] Cache too old ({_dc_age_days}d > "
                                f"{CONSTITUENT_DISK_CACHE_MAX_DAYS}d limit). Not using stale universe."
                            )
                    else:
                        logger.error("❌ [CONSTITUENT DISK CACHE] No disk cache found at constituent_cache.json.")
                except Exception as _dc_err:
                    logger.warning(f"⚠️ [CONSTITUENT DISK CACHE] Disk cache load failed: {_dc_err}")
                return []
                    
            from daily_builder import SYMBOL_CORRECTIONS
            normalized = []
            for s in symbols:
                if s in SYMBOL_CORRECTIONS:
                    clean = SYMBOL_CORRECTIONS[s]
                else:
                    clean = s
                if "DUMMY" in clean.upper():
                    continue
                normalized.append(clean)
                
            sorted_symbols = sorted(normalized)
            
            cls.last_download_ms = (time.time() - start_t) * 1000
            cls.symbol_count = len(sorted_symbols)
            cls.last_refresh = time.time()
            
            logger.info(f"🎯 Total unique constituent symbols fetched: {cls.symbol_count}")

            cls._cached_symbols = sorted_symbols
            cls._cache_trading_date = current_market_date

            # [VERSION: CONSTITUENT_DISK_CACHE_v1.0] Persist to disk & DB after every successful
            # live download so pod restarts have a fallback without needing to hit NSE again.
            try:
                from config import CONSTITUENT_CACHE_PATH
                _disk_payload = {
                    "symbols": sorted_symbols,
                    "cached_at": str(current_market_date),
                    "symbol_count": len(sorted_symbols)
                }
                with open(CONSTITUENT_CACHE_PATH, 'w') as _dcf:
                    json.dump(_disk_payload, _dcf)
                logger.debug(f"💾 [CONSTITUENT DISK CACHE] Saved {len(sorted_symbols)} symbols to {CONSTITUENT_CACHE_PATH}")
                
                from database import save_system_state
                save_system_state("constituent_cache", _disk_payload)
                logger.info(f"⚡ [CONSTITUENT DB CACHE] Backed up {len(sorted_symbols)} constituent symbols to PostgreSQL DB system_state")
            except Exception as _dc_write_err:
                logger.warning(f"⚠️ [CONSTITUENT DISK CACHE] Failed to persist disk/DB cache: {_dc_write_err}")

            return list(cls._cached_symbols)

def fetch_constituents() -> list:
    """Helper method to easily fetch from the service."""
    return ConstituentService.fetch_constituents()
